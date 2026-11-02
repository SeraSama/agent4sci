"""``skills.lock``: provenance of installed skills (source, pinned commit, content digest, license, scan)."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Trust = Literal["builtin", "reviewed", "unreviewed", "learned"]


class LockEntry(BaseModel):
    name: str
    source: str  # the spec the user installed from, e.g. K-Dense-AI/scientific-agent-skills/skills/neurokit2
    kind: str = "github"
    repo: str | None = None
    ref: str | None = None
    commit: str | None = None
    subpath: str = ""
    digest: str = ""
    license: str | None = None
    license_status: str = "unknown"
    tags: list[str] = Field(default_factory=list)
    scan: dict[str, Any] = Field(default_factory=dict)
    trust: Trust = "unreviewed"
    installed_at: float = Field(default_factory=time.time)


class LockFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, LockEntry]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {name: LockEntry(**entry) for name, entry in data.get("skills", {}).items()}

    def save(self, entries: dict[str, LockEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "skills": {n: e.model_dump(mode="json") for n, e in sorted(entries.items())}}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")

    def put(self, entry: LockEntry) -> None:
        entries = self.load()
        entries[entry.name] = entry
        self.save(entries)

    def remove(self, name: str) -> None:
        entries = self.load()
        if entries.pop(name, None) is not None:
            self.save(entries)


def dir_digest(directory: Path) -> str:
    """sha256 over (relative path, file hash) pairs — changes when any file changes."""
    h = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(directory).as_posix()
        if rel.startswith(".history/"):
            continue
        h.update(rel.encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return "sha256:" + h.hexdigest()
