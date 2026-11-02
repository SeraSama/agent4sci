"""Tools for designing the app itself: Harness profiles and Loop graphs.

Given to the 主理人 (orchestrator) harness, so a user can describe the agent or workflow they want in
chat — "a game-design researcher", "a loop that screens 200 papers" — and get a saved, validated setup
they can then open in the Harness page or the Loop editor.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .base import ToolContext, ToolError, tool

MAX_SOURCE_CHARS = 60_000


def _stores(ctx: ToolContext) -> tuple[Any, Any]:
    harnesses, loops = ctx.services.get("harness_store"), ctx.services.get("loop_store")
    if harnesses is None or loops is None:
        raise ToolError("当前环境不能修改 Harness / Loop（只有主理人对话可以）。")
    return harnesses, loops


class NoArgs(BaseModel):
    pass


@tool("list_designs", category="design", parallel_safe=True)
async def list_designs(args: NoArgs, ctx: ToolContext):
    """List the existing Harness profiles, Loop graphs, tool groups and loop node types — read this before
    creating or editing anything, so you reuse what is there and use valid values."""
    from ..agent.harness import GROUP_LABELS
    from ..loops.schema import NODE_TYPE_INFO

    harnesses, loops = _stores(ctx)
    out = {
        "harnesses": [{"id": h.id, "name": h.name, "description": h.description, "builtin": h.builtin,
                       "tool_groups": h.tool_groups, "max_turns": h.max_turns, "memory_mode": h.memory_mode}
                      for h in harnesses.list()],
        "loops": [{"id": g.id, "name": g.name, "description": g.description, "category": g.category,
                   "builtin": g.builtin, "nodes": len(g.nodes)} for g in loops.list()],
        "tool_groups": GROUP_LABELS,
        "loop_node_types": {k: (v.get("label") if isinstance(v, dict) else str(v)) for k, v in NODE_TYPE_INFO.items()},
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


class HarnessArgs(BaseModel):
    id: str | None = Field(None, description="Existing harness id to update; omit to create a new one")
    name: str | None = Field(None, description="Display name, e.g. '游戏设计研究员'")
    description: str | None = None
    role_prompt: str | None = Field(None, description="The system role prompt: who the agent is, how it works, "
                                                      "what it must always do. Write it in the user's language.")
    tool_groups: list[str] | None = Field(None, description="Tool groups from list_designs (fs, shell, python, web, "
                                                            "literature, research, neuro, memory, skills, human, agent, mcp, design)")
    skills_mode: Literal["all", "tags", "list", "none"] | None = None
    skill_tags: list[str] | None = None
    skill_names: list[str] | None = None
    memory_mode: Literal["global", "project", "conversation", "none"] | None = None
    model_id: str | None = Field(None, description="Model profile id; omit to use the default model")
    effort: str | None = None
    max_turns: int | None = Field(None, ge=0, description="0 = no limit")
    max_tokens: int | None = Field(None, ge=0, description="Output tokens per reply; 0 = adapter default")
    max_cost_usd: float | None = Field(None, ge=0)
    allow_subagents: bool | None = None
    subagent_max_turns: int | None = Field(None, ge=0)
    approvals: dict[str, Literal["auto", "ask", "deny"]] | None = Field(
        None, description="Tool category → approval mode, e.g. {'shell': 'ask'}")


@tool("save_harness", category="design")
async def save_harness(args: HarnessArgs, ctx: ToolContext):
    """Create or update a Harness profile (an agent preset: role prompt, tools, skills, memory, limits).
    Call list_designs first. Updating a built-in profile saves an override the user can reset."""
    from ..agent.harness import ALL_GROUPS, HarnessProfile

    harnesses, _ = _stores(ctx)
    patch = {k: v for k, v in args.model_dump(exclude={"id"}).items() if v is not None}
    if args.id:
        profile = harnesses.get(args.id)
        if profile.id != args.id:
            raise ToolError(f"没有这个 Harness：{args.id}（用 list_designs 查看）")
        data = {**profile.model_dump(), **patch, "builtin": profile.builtin}
    else:
        if not patch.get("name"):
            raise ToolError("新建 Harness 需要 name。")
        data = {**patch, "builtin": False}
    unknown = [g for g in data.get("tool_groups", []) if g not in ALL_GROUPS]
    if unknown:
        raise ToolError(f"未知的工具组：{', '.join(unknown)}。可用：{', '.join(ALL_GROUPS)}")
    saved = harnesses.save(HarnessProfile(**data))
    return (f"已保存 Harness「{saved.name}」（id={saved.id}）。"
            f"用户可以在「Harness」页查看和修改，在对话顶部切换到它。\n"
            + json.dumps(saved.model_dump(), ensure_ascii=False, indent=2)[:2000])


class LoopReadArgs(BaseModel):
    id: str = Field(description="Loop id from list_designs")


@tool("get_loop_yaml", category="design", parallel_safe=True)
async def get_loop_yaml(args: LoopReadArgs, ctx: ToolContext):
    """Read one Loop graph as YAML — use a built-in loop (research-pipeline, lit-review, …) as the starting
    point for a new one instead of writing a graph from scratch."""
    _, loops = _stores(ctx)
    graph = loops.get(args.id)
    if graph is None:
        raise ToolError(f"没有这个 Loop：{args.id}")
    return yaml.safe_dump(graph.model_dump(mode="json"), allow_unicode=True, sort_keys=False)[:MAX_SOURCE_CHARS]


class LoopSaveArgs(BaseModel):
    yaml_source: str = Field(description="The whole graph as YAML (nodes, edges, name, description). Keep node ids "
                                         "stable when editing an existing loop.")
    id: str | None = Field(None, description="Existing loop id to overwrite; omit to create a new loop")
    note: str = Field("", description="Short note for the version history, e.g. '加入人工检查点'")


@tool("save_loop", category="design")
async def save_loop(args: LoopSaveArgs, ctx: ToolContext):
    """Create or update a Loop graph from YAML. The graph is validated first: if it has errors nothing is
    saved and the problems are returned, so fix them and call again."""
    from ..loops.schema import LoopGraph, validate_graph

    _, loops = _stores(ctx)
    try:
        data = yaml.safe_load(args.yaml_source)
    except yaml.YAMLError as exc:
        raise ToolError(f"YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ToolError("YAML 的顶层必须是一个对象（含 name、nodes、edges）。")
    data.pop("builtin", None)
    if args.id:
        data["id"] = args.id
    try:
        graph = LoopGraph(**data)
    except Exception as exc:
        raise ToolError(f"图的字段不合法：{exc}") from exc
    issues = validate_graph(graph)
    errors = [i for i in issues if getattr(i, "level", "error") == "error"]
    if errors:
        lines = [f"- {getattr(i, 'node', '') or '图'}: {getattr(i, 'message', i)}" for i in errors]
        return "没有保存，图里有这些错误，请修正后重新保存：\n" + "\n".join(lines)
    saved = loops.save(graph, note=args.note or ("由主理人创建" if not args.id else "由主理人修改"))
    warnings = [f"- {getattr(i, 'node', '') or '图'}: {getattr(i, 'message', i)}" for i in issues if i not in errors]
    text = (f"已保存 Loop「{saved.name}」（id={saved.id}，第 {saved.version} 版，{len(saved.nodes)} 个节点）。"
            "用户可以在「Loop 设计器」里打开、修改并运行它。")
    return text + ("\n提示：\n" + "\n".join(warnings) if warnings else "")


class SkillArgs(BaseModel):
    name: str = Field(description="lowercase-with-hyphens, 1-64 chars; also the folder name")
    description: str = Field(description="One sentence: what it does AND when to use it — this is all an agent "
                                         "sees until it activates the skill, so make it specific")
    body: str = Field(description="The SKILL.md body in Markdown: when to use it, the steps, the conventions and "
                                  "pitfalls of this field, what to check before trusting a result")
    tags: list[str] | None = Field(None, description="Tags used to filter skills per field, e.g. ['neuro', 'eeg']")
    scope: Literal["global", "project"] = Field("global", description="global = available in every project")


@tool("save_skill", category="design")
async def save_skill(args: SkillArgs, ctx: ToolContext):
    """Write a skill (SKILL.md) that agents can activate on demand. Use it to teach this app a field's method:
    the steps, the conventions, the numbers that must be checked. Keep it instructional, not a script dump."""
    from ..core.paths import user_skills_dir
    from ..skills.scanner import scan_skill
    from ..skills.spec import load_skill, render_skill_md

    name = args.name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        raise ToolError("技能名只能用小写字母、数字和连字符（1-64 字符），且要与目录名一致。")
    root = (ctx.workspace / "skills" / "installed") if args.scope == "project" else user_skills_dir()
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {"name": name, "description": args.description.strip()[:1024]}
    if args.tags:
        meta["metadata"] = {"tags": ", ".join(args.tags)}  # where the standard keeps non-standard fields
    (directory / "SKILL.md").write_text(render_skill_md(meta, args.body), "utf-8")
    try:
        load_skill(directory, strict=True)
    except Exception as exc:
        raise ToolError(f"技能格式不合法：{exc}") from exc
    report = scan_skill(directory, external=False)
    where = "全局（所有项目可用）" if args.scope == "global" else "本项目"
    return (f"已保存技能「{name}」到{where}：{directory}（安全扫描：{report.risk}）。"
            "新的对话就能在技能清单里看到它；在「技能」页可以查看和编辑。")


class DomainArgs(BaseModel):
    id: str = Field(description="lowercase-with-hyphens, e.g. 'game-design'")
    label: str = Field(description="Display name shown when creating a project, e.g. '游戏设计研究'")
    description: str = Field("", description="One line about what this field covers")
    tools: list[str] | None = Field(None, description="Tool groups projects in this field turn on by default")
    skill_tags: list[str] | None = Field(None, description="Tags used to recommend skills for this field")
    mcp: list[str] | None = Field(None, description="Recommended MCP server names")
    prompt: str = Field("", description="Field notes appended to the system prompt: method requirements, data "
                                        "and unit conventions, the usual tool chain")
    skills: list[dict[str, Any]] | None = Field(None, description="Skills to install with one click, as "
                                                                  "[{source: 'owner/repo', skills: ['a','b'], "
                                                                  "tags: ['x']}]; source may also be a catalog id")


@tool("save_domain", category="design")
async def save_domain(args: DomainArgs, ctx: ToolContext):
    """Create or update a field (领域包): what a project in this field turns on — tools, recommended skills,
    MCP servers, the field notes in the system prompt, and the skills its one-click install brings in."""
    from ..agent.harness import ALL_GROUPS, domain_path

    domain_id = args.id.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", domain_id):
        raise ToolError("领域 id 只能用小写字母、数字、- 和 _。")
    unknown = [g for g in (args.tools or []) if g not in ALL_GROUPS]
    if unknown:
        raise ToolError(f"未知的工具组：{', '.join(unknown)}。可用：{', '.join(ALL_GROUPS)}")
    data: dict[str, Any] = {"id": domain_id, "label": args.label.strip()}
    for key, value in (("description", args.description), ("tools", args.tools), ("skill_tags", args.skill_tags),
                       ("mcp", args.mcp), ("skills", args.skills), ("prompt", args.prompt)):
        if value:
            data[key] = value
    path = domain_path(domain_id, user=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
    return (f"已保存领域包「{args.label}」（id={domain_id}）到 {path}。"
            "新建项目时就能选它；在「技能 → 领域包」里可以编辑，或一键安装它列出的技能。")


DESIGN_TOOLS = [list_designs, save_harness, get_loop_yaml, save_loop, save_skill, save_domain]
