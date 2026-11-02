"""The three permission modes: 总是询问 / 按需提问 / 全部允许."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent4sci.agent.permissions import approvals_for, guards_shell
from agent4sci.tools import shell
from agent4sci.tools.base import ToolContext, ToolError
from agent4sci.agent.interactions import InteractionBroker


async def call(tool, ctx, **kw):
    return await tool.run(tool.parse_args(kw), ctx)


def ctx_for(workspace: Path, mode: str, *, answer: str = "allow", auto: bool = False) -> ToolContext:
    asked: list[str] = []

    def handler(interaction):
        asked.append(interaction.title)
        return {"decision": answer}

    ctx = ToolContext(workspace=workspace, permission_mode=mode, auto_mode=auto,
                      interactions=InteractionBroker(handler=handler))
    ctx.services["asked"] = asked
    return ctx


def test_each_mode_maps_to_the_right_approvals():
    assert approvals_for("ask_all")["exec"] == "ask" and approvals_for("ask_all")["fs.write"] == "ask"
    assert approvals_for("ask_risky") == {"skills.install": "ask"}  # the project's own work runs freely
    assert approvals_for("allow_all") == {}
    assert approvals_for("custom", {"exec": "deny"}) == {"exec": "deny"}
    assert guards_shell("ask_risky") and guards_shell("ask_all") and not guards_shell("allow_all")


async def test_ask_risky_runs_project_work_but_asks_before_leaving_the_project(workspace):
    ctx = ctx_for(workspace, "ask_risky")
    assert "exit code: 0" in (await call(shell.shell, ctx, command="echo hello > out.txt")).as_text()
    assert ctx.services["asked"] == []  # inside the project: no interruption

    await call(shell.shell, ctx, command="ls ~/Documents")
    assert any("项目之外" in title for title in ctx.services["asked"])


async def test_allow_all_skips_even_the_dangerous_command_guard(workspace):
    ctx = ctx_for(workspace, "allow_all")
    await call(shell.shell, ctx, command="echo sudo would normally be blocked")
    assert ctx.services["asked"] == []

    guarded = ctx_for(workspace, "ask_risky")
    with pytest.raises(ToolError, match="用户拒绝"):
        denied = ctx_for(workspace, "ask_risky", answer="deny")
        await call(shell.shell, denied, command="sudo rm -rf /etc")
    assert guards_shell(guarded.permission_mode)


async def test_without_a_human_a_risky_command_is_refused_unless_the_mode_allows_it(workspace):
    auto = ToolContext(workspace=workspace, permission_mode="ask_risky", auto_mode=True)
    with pytest.raises(ToolError, match="被拦截"):
        await call(shell.shell, auto, command="ls ~/Desktop")

    trusted = ToolContext(workspace=workspace, permission_mode="allow_all", auto_mode=True)
    assert "exit code: 0" in (await call(shell.shell, trusted, command="ls ~ > /dev/null")).as_text()


def test_the_harness_mode_can_be_overridden_per_conversation(tmp_path, monkeypatch):
    from agent4sci.agent.harness import HarnessBuilder, HarnessContext
    from agent4sci.core.app import AppServices
    from agent4sci.core.db import Database

    monkeypatch.setenv("A4S_FAKE_LLM", "1")
    app = AppServices(db=Database())
    app.models.ensure_env_profiles()
    builder = HarnessBuilder(app)
    profile = builder.harnesses.get("assistant")
    base = HarnessContext(workspace=tmp_path, memory_mode="none")

    agent = builder.build_agent(profile, base)
    assert agent.ctx.permission_mode == "ask_risky" and agent.config.approvals == {"skills.install": "ask"}

    strict = builder.build_agent(profile, HarnessContext(workspace=tmp_path, memory_mode="none",
                                                         permission_mode="ask_all"))
    assert strict.ctx.permission_mode == "ask_all" and strict.config.approvals["exec"] == "ask"
