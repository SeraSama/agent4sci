"""In-process event bus.

Agents, tools and the loop engine publish events (text deltas, tool calls,
node status…). Subscribers are WebSocket connections, the SQLite event log and
per-run trace files. High-frequency delta events are delivered live but never
persisted.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .db import Database

TRANSIENT_TYPES = frozenset({"llm.text.delta", "llm.reasoning.delta", "tool.input.delta"})


@dataclass
class Event:
    topic: str
    type: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)
    id: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "topic": self.topic, "type": self.type, "data": self.data, "ts": self.ts}


class Subscription:
    def __init__(self, bus: "EventBus", topics: set[str] | None, maxsize: int = 10000) -> None:
        self.bus = bus
        self.topics = topics
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.loop = asyncio.get_running_loop()

    def wants(self, topic: str) -> bool:
        if self.topics is None:
            return True
        return topic in self.topics or any(t.endswith("*") and topic.startswith(t[:-1]) for t in self.topics)

    def _offer(self, event: Event) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:  # slow consumer: drop deltas first, keep the rest
            if event.type not in TRANSIENT_TYPES:
                try:
                    self.queue.get_nowait()
                    self.queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def deliver(self, event: Event) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._offer(event)
        else:
            self.loop.call_soon_threadsafe(self._offer, event)

    def close(self) -> None:
        self.bus.unsubscribe(self)

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> Event:
        return await self.queue.get()


class EventBus:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db
        self._subs: list[Subscription] = []
        self._lock = threading.Lock()
        self._counter = itertools.count(1)
        self._sinks: list[Callable[[Event], None]] = []

    def subscribe(self, topics: Iterable[str] | None = None) -> Subscription:
        sub = Subscription(self, set(topics) if topics is not None else None)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def add_sink(self, sink: Callable[[Event], None]) -> None:
        self._sinks.append(sink)

    def publish(self, topics: str | Iterable[str], type: str, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        for topic in [topics] if isinstance(topics, str) else list(topics):
            event = Event(topic=topic, type=type, data=data)
            if self.db is not None and type not in TRANSIENT_TYPES:
                event.id = self.db.add_event(topic, type, data, event.ts)
            else:
                event.id = -next(self._counter)
            for sink in self._sinks:
                try:
                    sink(event)
                except Exception:
                    pass
            with self._lock:
                subs = [s for s in self._subs if s.wants(topic)]
            for sub in subs:
                sub.deliver(event)


class TraceWriter:
    """Append-only JSONL trace of non-transient events (``trace.jsonl`` per run)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, type: str, data: dict[str, Any]) -> None:
        if type in TRANSIENT_TYPES:
            return
        line = json.dumps({"ts": time.time(), "type": type, **data}, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class Emitter:
    """Publishes events to a fixed set of topics, tagging them with a span.

    Spans nest: a sub-agent gets ``emitter.child("delegate")`` so the UI can draw
    it inside its parent's timeline.
    """

    def __init__(
        self,
        bus: EventBus | None,
        topics: Iterable[str] = (),
        span: str | None = None,
        parent_span: str | None = None,
        trace: TraceWriter | None = None,
        labels: dict[str, Any] | None = None,
    ) -> None:
        self.bus = bus
        self.topics = list(topics)
        self.span = span or uuid.uuid4().hex[:12]
        self.parent_span = parent_span
        self.trace = trace
        self.labels = labels or {}
        self.listeners: list[Callable[[str, dict[str, Any]], None]] = []

    def __call__(self, type: str, **data: Any) -> None:
        payload = {"span": self.span, "parent_span": self.parent_span, **self.labels, **data}
        if self.trace is not None:
            self.trace.write(type, payload)
        for listener in self.listeners:
            try:
                listener(type, payload)
            except Exception:
                pass
        if self.bus is not None and self.topics:
            self.bus.publish(self.topics, type, payload)

    def child(self, name: str = "", **labels: Any) -> "Emitter":
        child = Emitter(
            self.bus, self.topics, parent_span=self.span, trace=self.trace,
            labels={**self.labels, **labels, **({"span_name": name} if name else {})},
        )
        child.listeners = list(self.listeners)
        return child

    @classmethod
    def null(cls) -> "Emitter":
        return cls(None)
