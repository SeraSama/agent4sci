import os
import subprocess
import sys
import time

from pathlib import Path

import pytest

from agent4sci.core.db import Database
from agent4sci.core.events import Emitter, EventBus, TraceWriter
from agent4sci.core.models import (
    DEMO_PROFILE_ID,
    ModelRegistry,
    PRESETS,
    profile_from_preset,
)
from agent4sci.core.secrets import MemorySecretStore, mask
from agent4sci.llm.pricing import compute_cost, lookup_pricing
from agent4sci.llm.types import Usage


def test_db_docs_messages_events_settings():
    db = Database()
    db.put("model", "a", {"name": "A"})
    db.put("model", "b", {"name": "B"}, parent="p1")
    assert db.get("model", "a") == {"name": "A"}
    assert {d["name"] for d in db.list("model")} == {"A", "B"}
    assert [d["name"] for d in db.list("model", parent="p1")] == ["B"]
    db.put("model", "a", {"name": "A2"})
    assert db.get("model", "a")["name"] == "A2"
    assert db.delete("model", "a") and db.get("model", "a") is None

    db.add_message("c1", {"role": "user"})
    db.add_message("c1", {"role": "assistant"})
    msgs = db.list_messages("c1")
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[0]["seq"] < msgs[1]["seq"]

    first = db.add_event("run:1", "x", {"a": 1})
    db.add_event("run:1", "y", {})
    assert [e["type"] for e in db.list_events("run:1", after_id=first)] == ["y"]

    db.set_setting("theme", {"id": "dark"})
    assert db.get_setting("theme") == {"id": "dark"}
    assert db.get_setting("missing", 3) == 3


_OPEN_WHEN_TOLD = """
import pathlib, sys, time
from agent4sci.core.db import Database
path, ready, go = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
ready.touch()
while not go.exists():
    time.sleep(0.002)
Database(path).close()
"""


def test_db_survives_processes_creating_it_together(tmp_path):
    # the app and the CLI, or a double launch, can create the database at the same moment
    for round_ in range(2):
        path, go = tmp_path / f"app{round_}.db", tmp_path / f"go{round_}"
        readies = [tmp_path / f"ready{round_}-{i}" for i in range(4)]
        procs = [subprocess.Popen([sys.executable, "-c", _OPEN_WHEN_TOLD, str(path), str(r), str(go)],
                                  stderr=subprocess.PIPE, text=True) for r in readies]
        deadline = time.monotonic() + 60
        while not all(r.exists() for r in readies) and time.monotonic() < deadline:
            time.sleep(0.01)
        go.touch()
        errors = [p.communicate(timeout=60)[1] for p in procs]
        assert [p.returncode for p in procs] == [0, 0, 0, 0], errors


def test_model_registry_keys_and_roles(monkeypatch):
    db = Database()
    reg = ModelRegistry(db, MemorySecretStore())
    prof = reg.save(profile_from_preset("anthropic"))
    assert prof.model == "claude-opus-5" and prof.capabilities.thinking
    assert reg.resolve_api_key(prof) is None
    reg.set_api_key(prof.id, "sk-ant-123456789")
    assert reg.get(prof.id).has_key
    assert reg.resolve_api_key(reg.get(prof.id)) == "sk-ant-123456789"
    assert reg.key_status(prof)["masked"] == mask("sk-ant-123456789")

    local = reg.save(profile_from_preset("ollama", model="qwen3"))
    assert reg.resolve_api_key(local) == "not-needed"

    ds = reg.save(profile_from_preset("deepseek"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-key")
    assert reg.resolve_api_key(ds) == "env-key"

    reg.set_default(ds.id)
    assert reg.default_id() == ds.id
    reg.set_role_models({"reviewer": prof.id})
    assert reg.for_role("reviewer") == prof.id
    assert reg.for_role("writer") == ds.id

    assert reg.delete(prof.id) and reg.get(prof.id) is None
    assert set(PRESETS) >= {"anthropic", "openai", "deepseek", "ollama", "custom"}


def test_demo_profile_visible_only_in_demo_mode(monkeypatch):
    reg = ModelRegistry(Database(), MemorySecretStore())
    assert all(p.id != DEMO_PROFILE_ID for p in reg.list())
    monkeypatch.setenv("A4S_FAKE_LLM", "1")
    assert reg.list()[0].id == DEMO_PROFILE_ID
    assert reg.default_id() == DEMO_PROFILE_ID


def test_env_profiles_created(monkeypatch):
    reg = ModelRegistry(Database(), MemorySecretStore())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    created = reg.ensure_env_profiles()
    assert [p.preset for p in created] == ["anthropic"]
    assert reg.ensure_env_profiles() == []


def test_pricing():
    p = lookup_pricing("claude-opus-5")
    assert p.input == 5 and p.output == 25 and p.cache_read == pytest.approx(0.5)
    assert lookup_pricing("anthropic/claude-sonnet-5").input == 2
    assert lookup_pricing("some-unknown-model") is None
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    assert compute_cost(p, usage) == pytest.approx(5 + 2.5 + 0.5)


async def test_event_bus_subscribe_persist_and_trace(tmp_path):
    db = Database()
    bus = EventBus(db)
    sub = bus.subscribe({"run:1"})
    other = bus.subscribe({"conv:*"})
    emit = Emitter(bus, ["run:1"], trace=TraceWriter(tmp_path / "trace.jsonl"))
    emit("tool.call", name="x")
    emit("llm.text.delta", text="hi")
    child = emit.child("delegate")
    child("agent.start")
    got = [await sub.__anext__() for _ in range(3)]
    assert [e.type for e in got] == ["tool.call", "llm.text.delta", "agent.start"]
    assert got[2].data["parent_span"] == emit.span
    assert other.queue.empty()
    # deltas are live-only: not persisted, not traced
    assert [e["type"] for e in db.list_events("run:1")] == ["tool.call", "agent.start"]
    lines = (tmp_path / "trace.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    sub.close()
    other.close()


def test_backup_round_trip_keeps_data_and_names_the_models_needing_keys(tmp_path, monkeypatch):
    from agent4sci.core import backup as backup_mod
    from agent4sci.core.app import AppServices
    from agent4sci.core.models import ModelProfile
    from agent4sci.core.paths import app_data_dir

    app = AppServices()
    app.secrets.set("model:m1", "sk-not-a-real-key")  # keys stay in the keychain and never enter a backup
    app.models.save(ModelProfile(id="m1", name="DeepSeek", provider="openai-compatible", model="deepseek-chat",
                                 base_url="https://api.deepseek.com"))
    (app_data_dir() / "memory" / "global").mkdir(parents=True, exist_ok=True)
    (app_data_dir() / "memory" / "global" / "note.md").write_text("记住这件事", encoding="utf-8")
    app.db.close()

    made = backup_mod.create_backup()
    assert made["files"] > 1 and "DeepSeek" in made["models_needing_keys"]
    assert b"sk-not-a-real-key" not in Path(made["path"]).read_bytes()  # the archive is not a credential store

    # lose everything, then restore
    (app_data_dir() / "memory" / "global" / "note.md").unlink()
    AppServices().db.put("model", "m2", {"id": "m2", "name": "不该留下的"})
    result = backup_mod.restore_backup(Path(made["path"]))

    assert result["restart_required"] and "DeepSeek" in result["models_needing_keys"]
    assert Path(result["safety_backup"]).exists()  # what we replaced is recoverable
    assert (app_data_dir() / "memory" / "global" / "note.md").read_text("utf-8") == "记住这件事"
    restored = AppServices()
    assert [m.name for m in restored.models.list()] == ["DeepSeek"]


def test_a_foreign_archive_is_refused(tmp_path):
    import zipfile

    from agent4sci.core import backup as backup_mod

    bogus = tmp_path / "not-a-backup.zip"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(ValueError, match="备份文件"):
        backup_mod.inspect_backup(bogus)
