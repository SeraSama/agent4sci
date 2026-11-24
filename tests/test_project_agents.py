"""A project's main conversation can open real conversations in that project and hand them work."""

from __future__ import annotations

import pytest

from agent4sci.agent.harness import HarnessBuilder
from agent4sci.core.app import AppServices
from agent4sci.core.db import Database
from agent4sci.llm.fake_provider import FakeProvider, FakeTurn
from agent4sci.server.conversations import ConversationManager

MAIN_MARKER = "这是本项目的主对话"


def _env(tmp_path, monkeypatch, brain):
    monkeypatch.setenv("A4S_FAKE_LLM", "1")
    app = AppServices(db=Database())
    builder = HarnessBuilder(app, provider_factory=lambda profile, key: FakeProvider(profile, brain=brain))
    manager = ConversationManager(app, builder)
    project = app.projects.create("派活测试", path=str(tmp_path / "proj"), domains=["general"])
    return app, manager, project


def test_the_main_conversation_is_one_per_project(tmp_path, monkeypatch):
    _, manager, project = _env(tmp_path, monkeypatch, lambda *a: FakeTurn(text="hi"))
    main = manager.main_conversation(project.id)
    assert main.is_main and main.project_id == project.id and "主对话" in main.title
    assert manager.main_conversation(project.id).id == main.id  # asking again does not make a second one
    assert [c.id for c in manager.list(project.id) if c.is_main] == [main.id]
    manager.update(main.id, archived=True)
    assert not manager.main_conversation(project.id).archived  # asking for it again brings it back
    with pytest.raises(KeyError):
        manager.main_conversation("p_nope")


@pytest.mark.asyncio
async def test_the_main_conversation_dispatches_a_real_conversation(tmp_path, monkeypatch):
    def brain(system, messages, tools):
        if MAIN_MARKER in system:
            assert "start_conversation" in {t.name for t in tools}
            if not any(m.tool_results() for m in messages):
                return FakeTurn(tool_calls=[("start_conversation", {
                    "title": "读 lit/ 下的文献", "task": "读 lit/ 里的 PDF，摘要写进 notes/summary.md"})])
            return FakeTurn(text="活派出去也收回来了。")
        # the spawned conversation: it must NOT be able to dispatch further
        assert "start_conversation" not in {t.name for t in tools}
        assert MAIN_MARKER not in system
        return FakeTurn(text="摘要写好了，在 notes/summary.md。")

    _, manager, project = _env(tmp_path, monkeypatch, brain)
    main = manager.main_conversation(project.id)
    await manager.send(main.id, "把文献那块安排一下")
    await manager.tasks[main.id]

    spawned = [c for c in manager.list(project.id) if not c.is_main]
    assert len(spawned) == 1
    child = spawned[0]
    assert child.title == "读 lit/ 下的文献" and child.spawned_by == main.id

    results = [b for m in manager.raw_messages(main.id) for b in m["content"] if b.get("type") == "tool_result"]
    report = results[0]["content"][0]["text"]
    assert "跑完了" in report and "摘要写好了" in report  # the child's own answer came back to the main agent
    assert child.id in report
    assert "摘要写好了" in "".join(
        b.get("text", "") for m in manager.raw_messages(child.id) for b in m["content"])


@pytest.mark.asyncio
async def test_dispatching_without_waiting_returns_at_once(tmp_path, monkeypatch):
    def brain(system, messages, tools):
        if MAIN_MARKER in system:
            if not any(m.tool_results() for m in messages):
                return FakeTurn(tool_calls=[("start_conversation", {
                    "title": "后台跑的活", "task": "慢慢做", "wait": False})])
            return FakeTurn(text="派完了，稍后去收。")
        return FakeTurn(text="在做。")

    _, manager, project = _env(tmp_path, monkeypatch, brain)
    main = manager.main_conversation(project.id)
    await manager.send(main.id, "派个后台的活")
    await manager.tasks[main.id]

    child = next(c for c in manager.list(project.id) if not c.is_main)
    results = [b for m in manager.raw_messages(main.id) for b in m["content"] if b.get("type") == "tool_result"]
    assert "已派出" in results[0]["content"][0]["text"]
    task = manager.tasks.get(child.id)
    if task is not None:
        await task  # let the child finish so the test does not leave a task running


@pytest.mark.asyncio
async def test_the_desk_refuses_conversations_outside_its_project(tmp_path, monkeypatch):
    from agent4sci.server.conversations import ProjectDesk
    from agent4sci.tools.base import ToolError

    app, manager, project = _env(tmp_path, monkeypatch, lambda *a: FakeTurn(text="hi"))
    other = app.projects.create("别的项目", path=str(tmp_path / "other"), domains=["general"])
    main = manager.main_conversation(project.id)
    stranger = manager.create(project_id=other.id, title="别人家的对话")
    desk = ProjectDesk(manager, main.id)

    with pytest.raises(ToolError, match="不属于本项目"):
        desk.read(stranger.id, 5)
    with pytest.raises(ToolError, match="本对话自己"):
        desk.read(main.id, 5)
    with pytest.raises(ToolError, match="找不到对话"):
        desk.read("c_nope", 5)
    assert "还没有别的对话" in desk.listing()

@pytest.mark.asyncio
async def test_it_refuses_to_dispatch_without_limit(tmp_path, monkeypatch):
    """A runaway loop must hit a wall instead of opening conversations until the API bill does."""
    from agent4sci.server.conversations import ProjectDesk
    from agent4sci.tools.base import ToolError

    _, manager, project = _env(tmp_path, monkeypatch, lambda *a: FakeTurn(text="hi"))
    main = manager.main_conversation(project.id)
    desk = ProjectDesk(manager, main.id)
    for i in range(ProjectDesk.MAX_LIVE):
        conv = manager.create(project_id=project.id, title=f"派生 {i}")
        conv.spawned_by = main.id
        manager.save(conv)
    with pytest.raises(ToolError, match="已经有 24 个"):
        await desk.start("再来一个", "做点什么", None, False, 60)
