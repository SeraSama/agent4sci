"""Human-in-the-loop requests: tool approvals, questions and loop checkpoints.

An agent (or the loop engine) calls :meth:`InteractionBroker.ask`; the request
is published on the event bus and waits until the UI/CLI resolves it. A
``handler`` can answer synchronously instead (terminal prompts, auto mode).
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from ..core.events import EventBus

InteractionKind = Literal["approval", "question", "checkpoint"]


class Interaction(BaseModel):
    id: str = Field(default_factory=lambda: "ix_" + uuid.uuid4().hex[:10])
    kind: InteractionKind
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    topics: list[str] = Field(default_factory=list)
    created: float = Field(default_factory=time.time)
    status: Literal["pending", "resolved", "cancelled"] = "pending"
    response: dict[str, Any] | None = None


Handler = Callable[[Interaction], "dict[str, Any] | None | Awaitable[dict[str, Any] | None]"]


class InteractionCancelled(Exception):
    pass


class InteractionBroker:
    def __init__(self, bus: EventBus | None = None, handler: Handler | None = None) -> None:
        self.bus = bus
        self.handler = handler
        self._pending: dict[str, tuple[Interaction, asyncio.Future[dict[str, Any]]]] = {}

    async def ask(
        self,
        kind: InteractionKind,
        title: str,
        payload: dict[str, Any] | None = None,
        topics: list[str] | None = None,
    ) -> dict[str, Any]:
        interaction = Interaction(kind=kind, title=title, payload=payload or {}, topics=topics or [])
        if self.handler is not None:
            result = self.handler(interaction)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                interaction.status, interaction.response = "resolved", result
                return result
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[interaction.id] = (interaction, future)
        self._publish(interaction, "interaction.request")
        try:
            return await future
        finally:
            self._pending.pop(interaction.id, None)
            self._publish(interaction, "interaction.resolved")

    def resolve(self, interaction_id: str, response: dict[str, Any]) -> bool:
        item = self._pending.get(interaction_id)
        if item is None:
            return False
        interaction, future = item
        if future.done():
            return False
        interaction.status, interaction.response = "resolved", response
        future.get_loop().call_soon_threadsafe(future.set_result, response)
        return True

    def cancel(self, topic: str | None = None) -> int:
        count = 0
        for interaction, future in list(self._pending.values()):
            if topic is None or topic in interaction.topics:
                interaction.status = "cancelled"
                if not future.done():
                    future.get_loop().call_soon_threadsafe(future.set_exception, InteractionCancelled())
                count += 1
        return count

    def pending(self, topic: str | None = None) -> list[Interaction]:
        return [i for i, _ in self._pending.values() if topic is None or topic in i.topics]

    def _publish(self, interaction: Interaction, type: str) -> None:
        if self.bus is not None and interaction.topics:
            self.bus.publish(interaction.topics, type, interaction.model_dump(mode="json"))
