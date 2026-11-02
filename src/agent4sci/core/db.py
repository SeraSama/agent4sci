"""Small SQLite store: a generic JSON document table plus messages, events and settings.

The backend is a local single-user app, so one connection guarded by a lock is
enough; calls are short and safe to make from the event loop thread.
"""

from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    parent TEXT,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS docs_parent ON docs(kind, parent, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_topic ON events(topic, id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._init_schema()

    def _init_schema(self, attempts: int = 40) -> None:
        # Processes opening a brand-new database together (the app and the CLI, or a double launch) race to switch it
        # to WAL; SQLite answers the loser "database is locked" at once instead of waiting. Every step is idempotent.
        for attempt in range(attempts):
            try:
                if self.path != ":memory:":
                    self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.executescript(_SCHEMA)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or attempt == attempts - 1:
                    raise
                time.sleep(0.05 + 0.2 * random.random())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    # ---- documents -------------------------------------------------------
    def put(self, kind: str, id: str, data: dict[str, Any], parent: str | None = None) -> None:
        now = time.time()
        self._exec(
            """INSERT INTO docs(kind, id, parent, data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(kind, id) DO UPDATE SET
                 parent=excluded.parent, data=excluded.data, updated_at=excluded.updated_at""",
            (kind, id, parent, json.dumps(data, ensure_ascii=False), now, now),
        )

    def get(self, kind: str, id: str) -> dict[str, Any] | None:
        row = self._exec("SELECT data FROM docs WHERE kind=? AND id=?", (kind, id)).fetchone()
        return json.loads(row["data"]) if row else None

    def list(self, kind: str, parent: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT data FROM docs WHERE kind=?"
        params: list[Any] = [kind]
        if parent is not None:
            sql += " AND parent=?"
            params.append(parent)
        sql += " ORDER BY updated_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [json.loads(r["data"]) for r in self._exec(sql, params).fetchall()]

    def delete(self, kind: str, id: str) -> bool:
        return self._exec("DELETE FROM docs WHERE kind=? AND id=?", (kind, id)).rowcount > 0

    # ---- messages --------------------------------------------------------
    def add_message(self, conversation_id: str, data: dict[str, Any]) -> int:
        cur = self._exec(
            "INSERT INTO messages(conversation_id, data, created_at) VALUES (?, ?, ?)",
            (conversation_id, json.dumps(data, ensure_ascii=False), time.time()),
        )
        return int(cur.lastrowid)

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT id, data FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
        ).fetchall()
        out = []
        for r in rows:
            item = json.loads(r["data"])
            item["seq"] = r["id"]
            out.append(item)
        return out

    def delete_messages(self, conversation_id: str) -> None:
        self._exec("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))

    # ---- events ----------------------------------------------------------
    def add_event(self, topic: str, type: str, data: dict[str, Any], ts: float | None = None) -> int:
        cur = self._exec(
            "INSERT INTO events(topic, type, data, ts) VALUES (?, ?, ?, ?)",
            (topic, type, json.dumps(data, ensure_ascii=False, default=str), ts or time.time()),
        )
        return int(cur.lastrowid)

    def list_events(self, topic: str, after_id: int = 0, limit: int = 5000) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT id, topic, type, data, ts FROM events WHERE topic=? AND id>? ORDER BY id LIMIT ?",
            (topic, after_id, limit),
        ).fetchall()
        return [
            {"id": r["id"], "topic": r["topic"], "type": r["type"], "data": json.loads(r["data"]), "ts": r["ts"]}
            for r in rows
        ]

    def delete_events(self, topic: str) -> None:
        self._exec("DELETE FROM events WHERE topic=?", (topic,))

    # ---- settings --------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._exec("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
