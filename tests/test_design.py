"""The design tools behind the 主理人 harness: writing Harness profiles and Loop graphs."""

from __future__ import annotations

import json

import pytest
import yaml

from agent4sci.agent.harness import HarnessStore
from agent4sci.core.db import Database
from agent4sci.loops.store import LoopStore
from agent4sci.tools.base import ToolContext, ToolError
from agent4sci.tools.design import get_loop_yaml, list_designs, save_harness, save_loop


async def call(tool, ctx, **kw):
    return await tool.run(tool.parse_args(kw), ctx)


@pytest.fixture
def ctx(workspace):
    db = Database()
    return ToolContext(workspace=workspace,
                       services={"harness_store": HarnessStore(db), "loop_store": LoopStore(db)})


async def test_lists_what_already_exists(ctx):
    data = json.loads((await call(list_designs, ctx)).as_text())
    assert {"assistant", "orchestrator"} <= {h["id"] for h in data["harnesses"]}
    assert "research-pipeline" in {g["id"] for g in data["loops"]}
    assert "design" in data["tool_groups"] and "loop" in data["loop_node_types"]


async def test_saves_a_new_harness_and_rejects_unknown_tool_groups(ctx):
    out = (await call(save_harness, ctx, name="游戏设计研究员", role_prompt="你研究游戏机制。",
                      tool_groups=["fs", "literature"], max_turns=0, subagent_max_turns=5)).as_text()
    assert "已保存 Harness" in out
    saved = next(h for h in ctx.services["harness_store"].list() if h.name == "游戏设计研究员")
    assert saved.tool_groups == ["fs", "literature"] and saved.max_turns == 0 and saved.subagent_max_turns == 5
    assert saved.builtin is False

    patched = (await call(save_harness, ctx, id=saved.id, max_cost_usd=3.0)).as_text()
    assert "已保存 Harness" in patched
    assert ctx.services["harness_store"].get(saved.id).max_cost_usd == 3.0
    assert ctx.services["harness_store"].get(saved.id).role_prompt == "你研究游戏机制。"

    with pytest.raises(ToolError, match="未知的工具组"):
        await call(save_harness, ctx, name="坏的", tool_groups=["telepathy"])
    with pytest.raises(ToolError, match="需要 name"):
        await call(save_harness, ctx, max_turns=5)


async def test_saves_a_loop_only_when_the_graph_validates(ctx):
    source = (await call(get_loop_yaml, ctx, id="lit-review")).as_text()
    graph = yaml.safe_load(source)
    assert graph["nodes"]

    graph["id"] = "my-review"
    graph["name"] = "我的文献流程"
    saved = (await call(save_loop, ctx, yaml_source=yaml.safe_dump(graph, allow_unicode=True))).as_text()
    assert "已保存 Loop" in saved and "我的文献流程" in saved
    assert ctx.services["loop_store"].get("my-review").name == "我的文献流程"

    graph["edges"] = [{"source": "nope", "target": "also-missing"}]
    rejected = (await call(save_loop, ctx, yaml_source=yaml.safe_dump(graph, allow_unicode=True), id="my-review")).as_text()
    assert "没有保存" in rejected
    assert ctx.services["loop_store"].get("my-review").name == "我的文献流程"  # untouched

    with pytest.raises(ToolError, match="YAML"):
        await call(save_loop, ctx, yaml_source="name: [broken")


async def test_the_tools_refuse_outside_a_design_conversation(workspace):
    bare = ToolContext(workspace=workspace, services={})
    with pytest.raises(ToolError, match="只有主理人"):
        await call(list_designs, bare)


async def test_saves_a_skill_and_a_field_pack(ctx):
    from agent4sci.agent.harness import load_domain
    from agent4sci.core.paths import user_skills_dir
    from agent4sci.tools.design import save_domain, save_skill

    out = (await call(save_skill, ctx, name="game-balance", description="如何评估游戏数值平衡；在做关卡调参时使用",
                      body="## 何时使用\n调参前先定指标。\n", tags=["game-design"])).as_text()
    assert "已保存技能" in out
    md = (user_skills_dir() / "game-balance" / "SKILL.md").read_text("utf-8")
    assert "name: game-balance" in md and "game-design" in md and "何时使用" in md
    with pytest.raises(ToolError, match="技能名"):
        await call(save_skill, ctx, name="Bad Name", description="x", body="y")

    out = (await call(save_domain, ctx, id="game-design", label="游戏设计研究", tools=["literature", "research"],
                      skill_tags=["game-design"], prompt="Domain notes: ground claims in measured play data.",
                      skills=[{"source": "k-dense", "skills": ["literature-review"], "tags": ["general"]}])).as_text()
    assert "已保存领域包" in out
    pack = load_domain("game-design")
    assert pack["label"] == "游戏设计研究" and pack["builtin"] is False
    assert pack["skills"][0]["source"] == "k-dense" and pack["tools"] == ["literature", "research"]
    with pytest.raises(ToolError, match="未知的工具组"):
        await call(save_domain, ctx, id="x", label="X", tools=["teleport"])


def test_a_harness_without_tool_groups_is_a_plain_chat(tmp_path, monkeypatch):
    """纯模型对话: a project's field must not add tools or field notes behind the user's back."""
    from agent4sci.agent.harness import HarnessBuilder, HarnessContext
    from agent4sci.core.app import AppServices
    from agent4sci.core.db import Database

    monkeypatch.setenv("A4S_FAKE_LLM", "1")  # the demo model, so a harness can be assembled without a key
    app = AppServices(db=Database())
    app.models.ensure_env_profiles()
    builder = HarnessBuilder(app)
    project = app.projects.create("测试项目", path=str(tmp_path / "proj"), domains=["neuro"])
    ctx = HarnessContext(workspace=tmp_path / "proj", project=project, memory_mode="none")

    plain = builder.assemble(builder.harnesses.get("plain"), ctx)
    assert list(plain["tools"]) == []
    assert "Domain notes" not in plain["system"] and "Workspace" not in plain["system"]
    assert "no tools" in plain["system"] and "Date:" in plain["system"]

    assistant = builder.assemble(builder.harnesses.get("assistant"), ctx)
    assert len(list(assistant["tools"])) > 10  # a normal harness still gets the field's tools and notes
    assert "Domain notes" in assistant["system"] and "Workspace" in assistant["system"]
