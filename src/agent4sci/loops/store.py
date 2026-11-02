"""Persistence for loop graphs (built-in templates + user graphs with versions) and runs."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..core.db import Database
from ..core.paths import resource
from .schema import LoopGraph

RunStatus = Literal["pending", "running", "paused", "waiting", "done", "failed", "cancelled"]


def builtin_templates() -> list[LoopGraph]:
    out = []
    for path in sorted(resource("loops", "templates").glob("*.yaml")):
        data = yaml.safe_load(path.read_text("utf-8"))
        data["builtin"] = True
        out.append(LoopGraph(**data))
    return out


class LoopStore:
    KIND = "loop"
    VERSION_KIND = "loop_version"

    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[LoopGraph]:
        user = [LoopGraph(**d) for d in self.db.list(self.KIND)]
        user_ids = {g.id for g in user}
        return [t for t in builtin_templates() if t.id not in user_ids] + user

    def get(self, loop_id: str) -> LoopGraph | None:
        data = self.db.get(self.KIND, loop_id)
        if data:
            return LoopGraph(**data)
        return next((t for t in builtin_templates() if t.id == loop_id), None)

    def save(self, graph: LoopGraph, note: str = "") -> LoopGraph:
        previous = self.db.get(self.KIND, graph.id)
        if previous is not None:
            graph.version = int(previous.get("version", 1)) + 1
        graph.builtin = False
        self.db.put(self.KIND, graph.id, graph.model_dump(mode="json"))
        self.db.put(self.VERSION_KIND, f"{graph.id}@{graph.version}",
                    {"graph": graph.model_dump(mode="json"), "note": note, "saved": time.time()}, parent=graph.id)
        return graph

    def versions(self, loop_id: str) -> list[dict[str, Any]]:
        return [{"version": v["graph"]["version"], "note": v.get("note", ""), "saved": v.get("saved")}
                for v in self.db.list(self.VERSION_KIND, parent=loop_id)]

    def get_version(self, loop_id: str, version: int) -> LoopGraph | None:
        data = self.db.get(self.VERSION_KIND, f"{loop_id}@{version}")
        return LoopGraph(**data["graph"]) if data else None

    def duplicate(self, loop_id: str, name: str | None = None) -> LoopGraph:
        graph = self.get(loop_id)
        if graph is None:
            raise KeyError(loop_id)
        copy = graph.model_copy(deep=True, update={"id": "loop_" + uuid.uuid4().hex[:8], "version": 0,
                                                   "name": name or f"{graph.name}（副本）", "builtin": False,
                                                   "category": "custom"})
        return self.save(copy, note="从模板复制")

    def delete(self, loop_id: str) -> bool:
        return self.db.delete(self.KIND, loop_id)


class NodeRecord(BaseModel):
    key: str
    node: str
    status: Literal["running", "done", "failed", "skipped", "waiting"] = "running"
    output: Any = None
    error: str | None = None
    started: float = Field(default_factory=time.time)
    finished: float | None = None
    cost: float = 0.0
    tokens: dict[str, int] = Field(default_factory=dict)  # input/output, so "where did it go" has an answer


class RunState(BaseModel):
    id: str = Field(default_factory=lambda: "run_" + uuid.uuid4().hex[:10])
    loop_id: str
    loop_name: str
    graph: LoopGraph
    project_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    auto_mode: bool = False
    status: RunStatus = "pending"
    records: dict[str, NodeRecord] = Field(default_factory=dict)
    latest: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    protected: dict[str, str] = Field(default_factory=dict)
    cost_usd: float = 0.0
    tokens: dict[str, int] = Field(default_factory=dict)
    cost_unknown: bool = False  # an agent used a model without pricing; budgets cannot be enforced
    max_cost_usd: float | None = None
    model_id: str | None = None
    memory_mode: str = "project"
    conversation_id: str | None = None
    created: float = Field(default_factory=time.time)
    updated: float = Field(default_factory=time.time)
    error: str | None = None
    waiting: dict[str, Any] | None = None
    # things a "done" status would otherwise hide: loops that stopped without meeting their condition,
    # agents that wrapped up early for budget, models without prices
    warnings: list[str] = Field(default_factory=list)

    def node_status(self) -> dict[str, dict[str, Any]]:
        out = {}
        for node_id, key in self.latest.items():
            rec = self.records.get(key)
            if rec is not None:
                out[node_id] = {"status": rec.status, "key": key, "error": rec.error, "cost": rec.cost,
                                "tokens": rec.tokens}
        return out

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "loop_id": self.loop_id, "loop_name": self.loop_name, "project_id": self.project_id,
                "status": self.status, "cost_usd": round(self.cost_usd, 4), "cost_unknown": self.cost_unknown,
                "tokens": self.tokens,
                "created": self.created, "updated": self.updated, "error": self.error, "inputs": self.inputs,
                "auto_mode": self.auto_mode, "warnings": self.warnings}


class RunStore:
    KIND = "run"

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, state: RunState) -> None:
        state.updated = time.time()
        self.db.put(self.KIND, state.id, state.model_dump(mode="json"), parent=state.project_id)

    def get(self, run_id: str) -> RunState | None:
        data = self.db.get(self.KIND, run_id)
        return RunState(**data) if data else None

    def list(self, project_id: str | None = None, limit: int = 100) -> list[RunState]:
        return [RunState(**d) for d in self.db.list(self.KIND, parent=project_id, limit=limit)]

    def delete(self, run_id: str) -> bool:
        self.db.delete_events(f"run:{run_id}")
        return self.db.delete(self.KIND, run_id)
