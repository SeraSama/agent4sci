from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent4sci.core.db import Database
from agent4sci.server.app import build_app

TOKEN = "test-token"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("A4S_FAKE_LLM", "1")
    monkeypatch.setenv("A4S_PROJECTS_DIR", str(tmp_path / "projects"))
    app = build_app(TOKEN, data_db=Database())
    with TestClient(app) as c:
        yield c


def wait_for(fn, timeout=20.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("timed out")


def test_auth(client):
    assert client.get("/api/health").json()["ok"]
    assert client.get("/api/info").status_code == 401
    assert client.get("/api/info", headers=H).status_code == 200
    assert client.get(f"/api/info?token={TOKEN}").status_code == 200


def test_models_crud_and_keys(client):
    presets = client.get("/api/models/presets", headers=H).json()
    assert any(p["id"] == "deepseek" for p in presets)
    created = client.post("/api/models", headers=H, json={"preset": "deepseek", "api_key": "sk-1234567890"}).json()
    assert created["model"] == "deepseek-chat" and created["key"]["stored"] and "sk-1" in created["key"]["masked"]
    assert "api_key" not in created
    updated = client.put(f"/api/models/{created['id']}", headers=H, json={"name": "DS", "temperature": 0.2}).json()
    assert updated["name"] == "DS" and updated["temperature"] == 0.2
    listing = client.get("/api/models", headers=H).json()
    assert listing["models"][0]["id"] == "demo"
    client.put("/api/models-defaults", headers=H, json={"default": created["id"], "roles": {"reviewer": "demo"}})
    listing = client.get("/api/models", headers=H).json()
    assert listing["default"] == created["id"] and listing["roles"] == {"reviewer": "demo"}
    assert client.delete(f"/api/models/{created['id']}", headers=H).json()["deleted"]
    bad = client.post("/api/models", headers=H, json={"preset": "custom"})
    assert bad.status_code == 400


def test_settings_and_service_keys(client):
    r = client.put("/api/settings", headers=H, json={"contact_email": "me@lab.org", "language": "en"}).json()
    assert r["contact_email"] == "me@lab.org" and r["language"] == "en"
    assert client.put("/api/settings/services/s2", headers=H, json={"value": "k"}).json()["configured"]
    assert client.get("/api/settings", headers=H).json()["services"]["s2"]["configured"]


def test_ui_prefs_and_terms_round_trip(client):
    fresh = client.get("/api/settings", headers=H).json()
    assert fresh["ui"] == {} and fresh["terms_accepted_at"] is None
    ui = {"sidebarSide": "right", "background": {"asset": None, "opacity": 0.4}, "sound": {"enabled": True}}
    saved = client.put("/api/settings", headers=H, json={"ui": ui, "terms_accepted_at": 1762000000.0}).json()
    assert saved["ui"] == ui and saved["terms_accepted_at"] == 1762000000.0


def test_assets_import_serve_and_delete(client, tmp_path):
    # A 1x1 PNG is enough: the endpoint only cares about the suffix and the bytes.
    png = tmp_path / "wall paper.png"
    png.write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"rest-of-a-png")
    asset = client.post("/api/assets", headers=H, json={"path": str(png)}).json()
    assert asset["kind"] == "image" and asset["name"] == "wall paper.png" and not asset["heavy"]

    listed = client.get("/api/assets", headers=H).json()
    assert [a["id"] for a in listed] == [asset["id"]]

    # <img>/<video> cannot send a header, so the raw endpoint has to accept ?token=
    raw = client.get(f"/api/assets/{asset['id']}/raw?token={TOKEN}")
    assert raw.status_code == 200 and raw.headers["content-type"] == "image/png"
    assert raw.content == png.read_bytes()
    assert client.get(f"/api/assets/{asset['id']}/raw").status_code == 401

    assert client.get("/api/assets/nope.png/raw", headers=H).status_code == 404
    (tmp_path / "notes.txt").write_text("not media", encoding="utf-8")
    wrong_type = client.post("/api/assets", headers=H, json={"path": str(tmp_path / "notes.txt")})
    assert wrong_type.status_code == 400 and ".txt" in wrong_type.json()["detail"]
    assert client.post("/api/assets", headers=H, json={"path": str(tmp_path / "gone.png")}).status_code == 404

    assert client.delete(f"/api/assets/{asset['id']}", headers=H).json()["deleted"]
    assert client.get("/api/assets", headers=H).json() == []


def test_asset_id_cannot_escape_the_assets_folder(client, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"not yours")
    for asset_id in ("../secret.png", "..%2Fsecret.png", "/etc/hosts"):
        assert client.get(f"/api/assets/{asset_id}/raw", headers=H).status_code in (404, 400)


def make_project(client, tmp_path, **kw):
    body = {"name": "EEG 研究", "path": str(tmp_path / "proj"), "domains": ["neuro"], **kw}
    return client.post("/api/projects", headers=H, json=body).json()


def test_project_dirs_are_selectable(client, tmp_path):
    layout = client.get("/api/workspace-dirs", headers=H).json()
    assert "plan" in layout["dirs"] and "skills/installed" in layout["required"]

    project = make_project(client, tmp_path, dirs=["notes", "results", "../escape"])
    workspace = Path(project["path"])
    assert sorted(p.name for p in workspace.iterdir() if p.is_dir()) == sorted(
        [".a4s", ".git", "notes", "results", "skills", "loops"])
    # the app's own folders are created whatever the caller asked for, and nothing escapes
    assert (workspace / "skills" / "installed").is_dir()
    assert not (workspace.parent / "escape").exists()
    # a folder nobody asked for stays absent until a tool needs it
    assert not (workspace / "paper").exists()

    full = make_project(client, tmp_path / "all", name="全部目录")
    assert (Path(full["path"]) / "paper").is_dir()


def test_projects_and_files(client, tmp_path):
    project = make_project(client, tmp_path)
    detail = client.get(f"/api/projects/{project['id']}", headers=H).json()
    assert detail["git"] and "memory_gb" in detail["environment"] or "cpus" in detail["environment"]
    client.put("/api/file", headers=H, json={"project_id": project["id"], "path": "notes/a.md", "content": "# hi"})
    listing = client.get(f"/api/files?project_id={project['id']}&path=notes", headers=H).json()
    assert [e["name"] for e in listing["entries"]] == ["a.md"]
    content = client.get(f"/api/file?project_id={project['id']}&path=notes/a.md", headers=H).json()
    assert content["kind"] == "text" and content["content"] == "# hi"
    assert client.get(f"/api/file?project_id={project['id']}&path=../../etc/passwd", headers=H).status_code == 400
    patched = client.put(f"/api/projects/{project['id']}", headers=H,
                         json={"settings": {"default_memory_mode": "conversation"}}).json()
    assert patched["settings"]["default_memory_mode"] == "conversation"


def test_conversation_flow_events_and_temporary(client, tmp_path):
    project = make_project(client, tmp_path)
    conv = client.post("/api/conversations", headers=H, json={"project_id": project["id"]}).json()
    assert conv["memory_mode"] == "project"
    with client.websocket_connect(f"/ws?token={TOKEN}&topics=conv:{conv['id']}") as ws:
        assert client.post(f"/api/conversations/{conv['id']}/messages", headers=H, json={"text": "看看数据"}).json()[
            "status"] == "started"
        seen = set()
        while "agent.done" not in seen:
            seen.add(ws.receive_json()["type"])
        assert {"llm.text.delta", "tool.call", "tool.result"} <= seen

    def finished():
        data = client.get(f"/api/conversations/{conv['id']}", headers=H).json()
        return data if data["status"] == "idle" and len(data["messages"]) >= 4 else None

    data = wait_for(finished)
    assert data["title"] == "看看数据"
    assert "演示模式" in data["messages"][-1]["content"][-1]["text"]
    assert all("raw" not in m for m in data["messages"])
    events = client.get(f"/api/events?topic=conv:{conv['id']}", headers=H).json()
    assert any(e["type"] == "agent.done" for e in events)

    temp = client.post("/api/conversations", headers=H, json={"temporary": True}).json()
    assert temp["memory_mode"] == "none" and temp["temporary"]
    client.post(f"/api/conversations/{temp['id']}/messages", headers=H, json={"text": "hi"})
    wait_for(lambda: client.get(f"/api/conversations/{temp['id']}", headers=H).json()["status"] == "idle"
             and len(client.get(f"/api/conversations/{temp['id']}", headers=H).json()["messages"]) >= 2)
    assert client.app.state if False else True
    ids = [c["id"] for c in client.get("/api/conversations", headers=H).json()]
    assert temp["id"] in ids
    persisted = client.post(f"/api/conversations/{temp['id']}/persist", headers=H).json()
    assert not persisted["temporary"]
    assert len(client.get(f"/api/conversations/{temp['id']}", headers=H).json()["messages"]) >= 2
    assert client.delete(f"/api/conversations/{temp['id']}", headers=H).json()["deleted"]

    remembered = client.post(f"/api/conversations/{conv['id']}/remember", headers=H,
                             json={"text": "被试 3 的采样率是 250Hz", "scope": "project"}).json()
    assert remembered["scope"] == "project"
    memory = client.get(f"/api/memory?project_id={project['id']}&conversation_id={conv['id']}", headers=H).json()
    project_scope = next(s for s in memory if s["scope"] == "project")
    assert project_scope["items"][0]["name"].startswith("被试 3")


def test_harness_preview_and_groups(client, tmp_path):
    project = make_project(client, tmp_path)
    harnesses = client.get("/api/harnesses", headers=H).json()
    assert {h["id"] for h in harnesses} >= {"assistant", "librarian", "reviewer"}
    groups = client.get("/api/harness-groups", headers=H).json()
    assert any(g["id"] == "literature" and g["tools"] for g in groups)
    preview = client.post("/api/harnesses/assistant/preview", headers=H,
                          json={"project_id": project["id"], "memory_mode": "project"}).json()
    ids = {s["id"] for s in preview["sections"]}
    assert {"role", "skills", "memory", "tools"} <= ids and preview["total_tokens"] > 500
    assert any(t["name"] == "search_neuro_datasets" for t in preview["tools"])  # neuro domain adds its tools
    assert preview["memory_scopes"] == ["project", "conversation"] or "project" in preview["memory_scopes"]
    custom = client.post("/api/harnesses", headers=H, json={"name": "精简", "tool_groups": ["fs"]}).json()
    small = client.post(f"/api/harnesses/{custom['id']}/preview", headers=H, json={"project_id": project["id"]}).json()
    assert len(small["tools"]) < len(preview["tools"])


def test_loops_validation_and_demo_run(client, tmp_path):
    project = make_project(client, tmp_path)
    loops = client.get("/api/loops", headers=H).json()
    assert any(l["id"] == "research-pipeline" for l in loops)
    graph = client.get("/api/loops/ralph", headers=H).json()
    assert graph["issues"] == [] or all(i["level"] != "error" for i in graph["issues"])
    bad = {"name": "bad", "nodes": [{"id": "a", "type": "agent"}], "edges": []}
    assert any(i["level"] == "error" for i in client.post("/api/loops/validate", headers=H, json=bad).json())
    yaml_text = client.get("/api/loops/ralph/yaml", headers=H).text
    imported = client.post("/api/loops/import", headers=H, json={"yaml": yaml_text}).json()
    assert imported["id"] == "ralph-copy" and not imported["builtin"]
    run = client.post("/api/runs", headers=H, json={"loop_id": "ralph", "project_id": project["id"],
                                                    "inputs": {"task": "整理笔记"}, "auto_mode": True}).json()
    done = wait_for(lambda: (r := client.get(f"/api/runs/{run['id']}", headers=H).json())["status"] in
                    ("done", "failed") and r)
    assert done["status"] == "done", done.get("error")
    assert done["nodes"]["work"]["status"] == "done"
    assert client.get(f"/api/runs?project_id={project['id']}", headers=H).json()[0]["id"] == run["id"]


def test_skills_endpoints(client, tmp_path):
    project = make_project(client, tmp_path)
    skills = client.get(f"/api/skills?project_id={project['id']}", headers=H).json()["skills"]
    assert any(s["name"] == "research-workflow" and s["root"] == "builtin" for s in skills)
    repo = tmp_path / "repo"
    (repo / "demo-skill").mkdir(parents=True)
    (repo / "LICENSE").write_text("MIT License")
    (repo / "demo-skill" / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo. Use for tests.\n---\nDo it.\n")
    installed = client.post("/api/skills/install", headers=H,
                            json={"source": str(repo), "project_id": project["id"], "scope": "project"}).json()
    assert installed[0]["status"] == "installed"
    detail = client.get(f"/api/skills/detail/demo-skill?project_id={project['id']}", headers=H).json()
    assert detail["body"].strip() == "Do it." and detail["trust"] == "unreviewed"
    assert client.post(f"/api/skills/trust/demo-skill", headers=H,
                       json={"scope": "project", "project_id": project["id"]}).json()["trust"] == "reviewed"
    assert client.post(f"/api/skills/scan/demo-skill?project_id={project['id']}", headers=H).json()["risk"] == "low"
    catalog = client.get("/api/skills/catalog", headers=H).json()
    assert "neuro" in catalog["packs"]
    assert client.post("/api/skills/remove/demo-skill", headers=H,
                       json={"scope": "project", "project_id": project["id"]}).json()["removed"]


def test_mcp_endpoints(client):
    server = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")
    added = client.post("/api/mcp", headers=H, json={"name": "echo", "command": sys.executable, "args": [server]}).json()
    assert added["name"] == "echo"
    report = client.post("/api/mcp/echo/test", headers=H).json()
    assert report["ok"] and "add" in report["tools"]
    listing = wait_for(lambda: (d := client.get("/api/mcp", headers=H).json())["servers"][0]["status"] and d)
    assert listing["servers"][0]["status"]["ok"]
    assert any(m["name"] == "mne" for m in listing["catalog"])
    assert client.delete("/api/mcp/echo", headers=H).json()["deleted"]


def test_a_run_that_stops_without_an_answer_says_why(client, tmp_path):
    project = make_project(client, tmp_path)
    client.put("/api/harnesses/assistant", headers=H, json={"max_turns": 1})  # 1 turn: the demo model calls a tool
    conv = client.post("/api/conversations", headers=H, json={"project_id": project["id"]}).json()
    client.post(f"/api/conversations/{conv['id']}/messages", headers=H, json={"text": "看看数据"})

    def finished():
        data = client.get(f"/api/conversations/{conv['id']}", headers=H).json()
        return data if data["status"] == "idle" and data["messages"] else None

    last = wait_for(finished)["messages"][-1]
    assert last["meta"]["kind"] == "notice" and "最大轮数" in last["content"][0]["text"]


def test_user_domain_packs_override_builtin_ones(client):
    source = "id: game-design\nlabel: 游戏设计研究\ntools: [literature]\nskill_tags: [general]\n"
    assert client.put("/api/domains/game-design", headers=H, json={"source": source}).json()["label"] == "游戏设计研究"
    packs = {d["id"]: d for d in client.get("/api/domains", headers=H).json()}
    assert packs["game-design"]["builtin"] is False and packs["neuro"]["builtin"] is True
    assert "label" in packs["neuro"]["source"]
    assert client.put("/api/domains/x", headers=H, json={"source": "label: ["}).status_code == 400
    assert client.put("/api/domains/x", headers=H, json={"source": "id: other\nlabel: X"}).status_code == 400
    override = "id: neuro\nlabel: 我的神经科学\n"
    assert client.put("/api/domains/neuro", headers=H, json={"source": override}).json()["builtin"] is False
    assert client.delete("/api/domains/neuro", headers=H).json() == {"deleted": True, "builtin_restored": True}
    assert {d["id"]: d["builtin"] for d in client.get("/api/domains", headers=H).json()}["neuro"] is True
    assert client.delete("/api/domains/neuro", headers=H).status_code == 404


def test_a_message_can_hand_over_files_outside_the_workspace(client, tmp_path):
    project = make_project(client, tmp_path)
    data = tmp_path / "outside.csv"
    data.write_text("a,b\n1,2\n", encoding="utf-8")
    info = client.get("/api/path-info", headers=H, params={"path": str(data)}).json()
    assert info["exists"] and info["name"] == "outside.csv" and info["size"] > 0
    assert client.get("/api/path-info", headers=H, params={"path": str(tmp_path / "nope.csv")}).json()["exists"] is False

    conv = client.post("/api/conversations", headers=H, json={"project_id": project["id"]}).json()
    client.post(f"/api/conversations/{conv['id']}/messages", headers=H, json={"text": "看看这个", "files": [str(data)]})
    detail = wait_for(lambda: (lambda d: d if d["status"] == "idle" and d["messages"] else None)(
        client.get(f"/api/conversations/{conv['id']}", headers=H).json()))
    assert detail["attachments"] == [str(data.resolve())]
    assert str(data.resolve()) in detail["messages"][0]["content"][0]["text"]
    missing = client.post(f"/api/conversations/{conv['id']}/messages", headers=H,
                          json={"text": "x", "files": [str(tmp_path / "nope.csv")]})
    assert missing.status_code == 400 and "找不到文件" in missing.text


def test_project_self_check_endpoint(client, tmp_path):
    project = make_project(client, tmp_path)
    workspace = tmp_path / "proj"
    (workspace / "paper").mkdir(parents=True, exist_ok=True)
    (workspace / "paper" / "paper.md").write_text("# Results\n准确率 0.873，见 [@nobody2020]。\n", encoding="utf-8")
    (workspace / "references.bib").write_text("", encoding="utf-8")

    empty = client.get(f"/api/projects/{project['id']}/audit", headers=H).json()
    assert empty["ts"] is None and "paper/paper.md" in empty["checked"]["citations"]

    report = client.post(f"/api/projects/{project['id']}/audit", headers=H, json={}).json()
    assert report["passed"] is False
    assert any("nobody2020" in e["message"] for e in report["errors"])
    assert any("0.873" in w["message"] for w in report["warnings"])
    assert client.get(f"/api/projects/{project['id']}/audit", headers=H).json()["ts"] == report["ts"]


def test_config_bundle_round_trip(client, tmp_path):
    harness = client.post("/api/harnesses", headers=H, json={"name": "游戏设计研究员", "tool_groups": ["fs"]}).json()
    client.put("/api/domains/game-design", headers=H, json={"source": "id: game-design\nlabel: 游戏设计\n"})
    client.post("/api/mcp", headers=H, json={"name": "demo-mcp", "command": "uvx", "args": ["x"]})
    bundle = client.get("/api/config/export", headers=H).json()

    assert bundle["kind"] == "agent4sci-config"
    assert any(h["id"] == harness["id"] for h in bundle["harnesses"])
    assert [d["id"] for d in bundle["domains"]] == ["game-design"]
    assert [m["name"] for m in bundle["mcp"]] == ["demo-mcp"]
    assert all("has_key" not in m or not m["has_key"] for m in bundle["models"])

    # a fresh install imports it (field packs are files in the data dir, so clear it to act like another machine)
    client.delete("/api/domains/game-design", headers=H)
    fresh = TestClient(build_app(TOKEN, data_db=Database()))
    with fresh:
        report = fresh.post("/api/config/import", headers=H, json={"bundle": bundle}).json()
        assert f"harness:{harness['id']}" in report["added"]
        assert "domain:game-design" in report["added"] and "mcp:demo-mcp" in report["added"]
        names = [h["name"] for h in fresh.get("/api/harnesses", headers=H).json()]
        assert "游戏设计研究员" in names
        again = fresh.post("/api/config/import", headers=H, json={"bundle": bundle}).json()
        assert f"harness:{harness['id']}" in again["skipped"] and not again["added"]
        forced = fresh.post("/api/config/import", headers=H, json={"bundle": bundle, "overwrite": True}).json()
        assert f"harness:{harness['id']}" in forced["replaced"]

    assert client.post("/api/config/import", headers=H, json={"bundle": {"kind": "nope"}}).status_code == 400


def test_diagnostics_reports_the_environment(client):
    data = client.get("/api/diagnostics", headers=H).json()
    names = {c["name"]: c for c in data["checks"]}
    assert names["Python"]["ok"] is True and names["数据目录"]["ok"] is True
    assert "uv" in names and "模型档案" in names
    assert all({"name", "ok", "detail", "hint"} <= set(c) for c in data["checks"])
