"""Harness profiles and the builder that turns a profile + context into a running Agent.

A *harness* is everything around the model: role prompt, tool set, visible
skills, MCP servers, memory scope, approval policy and limits. Profiles are
editable in the desktop app ("可视化 Harness").
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

import yaml
from pydantic import BaseModel, Field

from ..core.envfix import IS_WINDOWS, shell_command, venv_python
from ..core.events import Emitter
from ..core.models import ModelProfile
from ..core.paths import conversation_memory_dir, global_memory_dir, resource, user_domains_dir
from ..core.projects import Project, compute_summary, project_environment
from ..llm import create_provider
from ..llm.base import LLMProvider
from ..llm.types import Message, estimate_tokens
from ..memory.store import MemoryAccess
from ..skills.registry import SkillRegistry, default_roots
from ..tools.base import Tool, ToolContext
from ..tools.registry import ToolRegistry
from .agent import Agent, AgentConfig, AgentResult, ApprovalMode
from .budget import Budget
from .permissions import DEFAULT_MODE, PermissionMode, approvals_for
from .prompts import (BASE_RULES, DEFAULT_ASSISTANT_ROLE, PLAIN_ASSISTANT_ROLE, build_system_prompt,
                      environment_block)

if TYPE_CHECKING:  # pragma: no cover
    from ..core.app import AppServices

MemoryMode = Literal["global", "project", "conversation", "none"]


def tool_groups() -> dict[str, list[Tool]]:
    from ..tools.bib import BIB_TOOLS
    from ..tools.findings import FINDINGS_TOOLS
    from ..tools.fs import FS_TOOLS
    from ..tools.human import HUMAN_TOOLS
    from ..tools.literature import LITERATURE_TOOLS
    from ..tools.memory_tool import memory_tool
    from ..tools.design import DESIGN_TOOLS
    from ..tools.neuro_data import NEURO_TOOLS
    from ..tools.provenance import PROVENANCE_TOOLS
    from ..tools.python_exec import PYTHON_TOOLS
    from ..tools.shell import SHELL_TOOLS
    from ..tools.skill_tools import SKILL_TOOLS
    from ..tools.subagent import AGENT_TOOLS
    from ..tools.web import WEB_TOOLS

    return {
        "fs": list(FS_TOOLS),
        "shell": list(SHELL_TOOLS),
        "python": list(PYTHON_TOOLS),
        "web": list(WEB_TOOLS),
        "literature": list(LITERATURE_TOOLS) + list(BIB_TOOLS),
        "research": list(FINDINGS_TOOLS) + list(PROVENANCE_TOOLS),
        "neuro": list(NEURO_TOOLS),
        "memory": [memory_tool()],
        "skills": list(SKILL_TOOLS),
        "human": list(HUMAN_TOOLS),
        "agent": list(AGENT_TOOLS),
        "design": list(DESIGN_TOOLS),
    }


GROUP_LABELS = {
    "fs": "文件读写", "shell": "命令行与后台任务", "python": "Python 内核", "web": "网页抓取/搜索",
    "literature": "文献与引用", "research": "发现账本与结果溯源", "neuro": "神经科学数据", "memory": "记忆",
    "skills": "技能", "human": "询问研究者", "agent": "子代理", "mcp": "MCP 工具",
    "design": "设计 Harness 与 Loop",
}
ALL_GROUPS = list(GROUP_LABELS)


class HarnessProfile(BaseModel):
    id: str = Field(default_factory=lambda: "h_" + uuid.uuid4().hex[:8])
    name: str
    description: str = ""
    role: str | None = None  # used for role → model mapping
    role_prompt: str = DEFAULT_ASSISTANT_ROLE
    include_rules: bool = True
    model_id: str | None = None
    effort: str | None = None
    tool_groups: list[str] = Field(default_factory=lambda: [g for g in ALL_GROUPS if g != "design"])
    tool_exclude: list[str] = Field(default_factory=list)
    skills_mode: Literal["all", "tags", "list", "none"] = "all"
    skill_tags: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    mcp_servers: list[str] | None = None
    memory_mode: MemoryMode = "project"
    permission_mode: PermissionMode = DEFAULT_MODE  # ask_all | ask_risky | allow_all | custom
    approvals: dict[str, ApprovalMode] = Field(default_factory=lambda: {"skills.install": "ask"})  # mode=custom
    max_turns: int = 80  # 0 = 无上限
    max_tokens: int | None = None  # 每条回复的输出上限；None/0 = 由模型适配器决定
    max_cost_usd: float | None = None
    tool_output_limit: int = 20_000
    compact_ratio: float = 0.75
    tool_timeout_s: float = 900.0
    stream_idle_timeout_s: float = 300.0  # 连接多久没有任何数据（含心跳）就中断重试；0 = 不限
    allow_subagents: bool = True
    subagent_max_turns: int = 30  # 子代理的轮数上限（0 = 无上限）
    builtin: bool = False


def _role_prompt(name: str) -> str:
    path = resource("loops", "roles", f"{name}.md")
    return path.read_text("utf-8") if path.exists() else DEFAULT_ASSISTANT_ROLE


def builtin_harnesses() -> list[HarnessProfile]:
    items = [
        HarnessProfile(id="assistant", name="研究助手", description="通用科研助手：文献、数据分析、写作、技能与记忆",
                       builtin=True),
        HarnessProfile(id="plain", name="纯模型对话", role_prompt=PLAIN_ASSISTANT_ROLE,
                       description="直接对话，不带任何工具、技能、MCP 与记忆——就是普通的 API 聊天",
                       include_rules=False, tool_groups=[], skills_mode="none", mcp_servers=[],
                       memory_mode="none", allow_subagents=False, builtin=True),
        HarnessProfile(id="librarian", name="文献专员", role="librarian", role_prompt=_role_prompt("librarian"),
                       description="系统检索、筛选、阅读文献并写综述",
                       tool_groups=["fs", "web", "literature", "research", "skills", "memory", "agent", "human"],
                       builtin=True),
        HarnessProfile(id="analyst", name="数据分析师", role="analyst", role_prompt=_role_prompt("analyst"),
                       description="数据清洗、统计分析、作图，结果写入 results/",
                       tool_groups=["fs", "shell", "python", "research", "skills", "memory", "neuro", "human"],
                       builtin=True),
        HarnessProfile(id="experimenter", name="实验员", role="experimenter", role_prompt=_role_prompt("experimenter"),
                       description="实现并运行计算实验（后台任务、固定种子、记录结果）",
                       tool_groups=["fs", "shell", "python", "research", "skills", "memory", "web", "human"],
                       builtin=True),
        HarnessProfile(id="writer", name="写作者", role="writer", role_prompt=_role_prompt("writer"),
                       description="基于发现账本与已核验文献撰写论文/报告",
                       tool_groups=["fs", "literature", "research", "skills", "memory"], builtin=True),
        HarnessProfile(id="orchestrator", name="主理人", role="orchestrator",
                       role_prompt=_role_prompt("orchestrator"),
                       description="扩展这个软件：为你的领域新建 Harness、技能、Loop 与领域包，写好直接保存",
                       tool_groups=["design", "fs", "literature", "skills", "memory", "human"],
                       memory_mode="global", max_turns=40, permission_mode="ask_risky", builtin=True),
        HarnessProfile(id="reviewer", name="评审", role="reviewer", role_prompt=_role_prompt("reviewer"),
                       description="严格的同行评审：结论与证据、统计、引用",
                       tool_groups=["fs", "literature", "research", "skills"], memory_mode="none", builtin=True),
    ]
    return items


def domain_path(domain_id: str, user: bool = False) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", domain_id)
    return (user_domains_dir() if user else resource("domains")) / f"{safe}.yaml"


def _read_domain(path: Path, builtin: bool) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text("utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return {**data, "id": data.get("id") or path.stem, "builtin": builtin}


def load_domain(domain_id: str) -> dict[str, Any] | None:
    """A pack written by the user (data directory) wins over the one shipped with the app."""
    for path, builtin in ((domain_path(domain_id, user=True), False), (domain_path(domain_id), True)):
        if path.exists():
            data = _read_domain(path, builtin)
            if data is not None:
                return data
    return None


def list_domains() -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(resource("domains").glob("*.yaml")):
        data = _read_domain(path, True)
        if data is not None:
            out[data["id"]] = data
    for path in sorted(user_domains_dir().glob("*.yaml")):
        data = _read_domain(path, False)
        if data is not None:
            out[data["id"]] = data
    return list(out.values())


class HarnessStore:
    KIND = "harness"

    def __init__(self, db: Any) -> None:
        self.db = db

    def list(self) -> list[HarnessProfile]:
        custom = {d["id"]: HarnessProfile(**d) for d in self.db.list(self.KIND)}
        items = [custom.pop(h.id, h) for h in builtin_harnesses()]
        return items + sorted(custom.values(), key=lambda h: h.name)

    def get(self, harness_id: str | None) -> HarnessProfile:
        for h in self.list():
            if h.id == (harness_id or "assistant"):
                return h
        return builtin_harnesses()[0]

    def save(self, profile: HarnessProfile) -> HarnessProfile:
        self.db.put(self.KIND, profile.id, profile.model_dump(mode="json"))
        return profile

    def reset(self, harness_id: str) -> None:
        self.db.delete(self.KIND, harness_id)


@dataclass
class HarnessContext:
    """Where and how an agent runs."""

    workspace: Path
    project: Project | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    topics: list[str] = field(default_factory=list)
    memory_mode: MemoryMode = "project"
    auto_mode: bool = False
    permission_mode: PermissionMode | None = None  # per-conversation override of the harness default
    emit: Emitter | None = None
    budget: Budget | None = None
    depth: int = 0
    stage_id: str | None = None
    model_override: str | None = None
    extra_tools: list[Tool] = field(default_factory=list)
    extra_sections: list[str] = field(default_factory=list)
    extra_services: dict[str, Any] = field(default_factory=dict)


def memory_roots(ctx: HarnessContext) -> dict[str, Path]:
    roots: dict[str, Path] = {"global": global_memory_dir()}
    if ctx.project is not None:
        roots["project"] = ctx.project.workspace / ".a4s" / "memory"
    if ctx.conversation_id:
        roots["conversation"] = conversation_memory_dir(ctx.conversation_id)
    elif ctx.run_id:
        roots["conversation"] = conversation_memory_dir(ctx.run_id)
    return roots


class HarnessBuilder:
    def __init__(self, app: "AppServices", provider_factory: Callable[[ModelProfile, str | None], LLMProvider] | None = None) -> None:
        self.app = app
        self.provider_factory = provider_factory or create_provider
        self.harnesses = HarnessStore(app.db)

    # ---- pieces --------------------------------------------------------------------
    def model_for(self, profile: HarnessProfile, ctx: HarnessContext) -> ModelProfile:
        model_id = ctx.model_override or profile.model_id or self.app.models.for_role(profile.role)
        model = self.app.models.get(model_id) if model_id else None
        if model is None:
            raise RuntimeError("还没有可用的模型：请先在“模型”页面添加一个模型并填写 API Key。")
        return model

    def provider_for(self, model: ModelProfile) -> LLMProvider:
        key = self.app.models.resolve_api_key(model)
        if key is None and model.provider != "fake":
            raise RuntimeError(f"模型「{model.name}」没有 API Key：请在“模型”页面填写。")
        return self.provider_factory(model, key)

    def skills_registry(self, ctx: HarnessContext) -> SkillRegistry:
        trusted = bool(ctx.project and ctx.project.settings.trusted)
        return SkillRegistry(default_roots(ctx.project.workspace if ctx.project else None, trust_project_compat=trusted))

    def visible_skills(self, profile: HarnessProfile, registry: SkillRegistry, ctx: HarnessContext,
                       window: int = 0) -> list[Any]:
        if profile.skills_mode == "none":
            return []
        if profile.skills_mode == "list":
            return registry.visible(names=profile.skill_names)
        tags = list(profile.skill_tags)
        if ctx.project is not None:
            for domain in ctx.project.domains:
                d = load_domain(domain) or {}
                tags += d.get("skill_tags", [])
        if profile.skills_mode == "tags":
            return registry.visible(tags=tags, names=profile.skill_names)
        records = registry.list()
        # The listing is pure overhead on every request. Keep it proportional to the model's window and let
        # `search_skills` find the rest: 15 bundled skills already cost ~2.2k tokens.
        budget = min(4000, max(600, int(window * 0.03))) if window else 4000
        if tags and estimate_tokens(SkillRegistry.prompt_block(records)) > budget:
            return registry.visible(tags=tags, names=profile.skill_names)
        return records

    def tools_for(self, profile: HarnessProfile, model: ModelProfile, ctx: HarnessContext,
                  memory: MemoryAccess, skills_on: bool) -> ToolRegistry:
        from ..tools.web import search_backend

        groups = tool_groups()
        wanted = list(profile.tool_groups)
        if not wanted:
            return ToolRegistry()  # a harness with no tool groups is a plain chat: the project must not add any
        if ctx.project is not None:
            for domain in ctx.project.domains:
                for g in (load_domain(domain) or {}).get("tools", []):
                    if g not in wanted and g in groups:
                        wanted.append(g)
        registry = ToolRegistry()
        for group in wanted:
            if group == "memory" and not memory.enabled:
                continue
            if group == "skills" and not skills_on:
                continue
            if group == "agent" and (not profile.allow_subagents or ctx.depth >= 2):
                continue
            if group == "human" and ctx.auto_mode:
                continue  # nobody can answer in auto mode; the tool would only return the canned reply
            for tool in groups.get(group, []):
                if tool.name in profile.tool_exclude or tool.name in registry:
                    continue
                if tool.name == "web_search":
                    native = model.provider == "anthropic" and model.capabilities.native_web_search
                    if not native and search_backend() is None:
                        continue
                registry.add(tool)
        if "mcp" in wanted:
            servers = profile.mcp_servers
            if ctx.project is not None and ctx.project.settings.mcp_servers is not None:
                servers = [s for s in (servers or ctx.project.settings.mcp_servers) if s in ctx.project.settings.mcp_servers]
            for tool in self.app.mcp.tools(servers):
                if tool.name not in profile.tool_exclude and tool.name not in registry:
                    registry.add(tool)
        for tool in ctx.extra_tools:
            registry.add(tool, replace=True)
        return registry

    def environment(self, model: ModelProfile, ctx: HarnessContext) -> dict[str, Any]:
        env: dict[str, Any] = {
            "date": _dt.date.today().isoformat(),
            "os": platform.system(),
            "shell": shell_command("")[1],
            "workspace": str(ctx.workspace),
        }
        if ctx.project is not None:
            env["project"] = f"{ctx.project.name}（领域：{', '.join(ctx.project.domains) or '未指定'}）"
            env["compute"] = compute_summary(project_environment(ctx.project.workspace))
        py = venv_python(ctx.workspace)
        env["python"] = (f"{py}（安装依赖用 `uv pip install <包>`）" if py
                         else "（工作区尚未创建 Python 环境；可用 `uv venv --seed` 创建，再用 `uv pip install <包>` 安装依赖）")
        return env

    def domain_sections(self, ctx: HarnessContext) -> list[str]:
        sections = []
        if ctx.project is not None:
            for domain in ctx.project.domains:
                d = load_domain(domain)
                if d and d.get("prompt"):
                    sections.append(d["prompt"].strip())
        return sections

    # ---- assembly ------------------------------------------------------------------
    def assemble(self, profile: HarnessProfile, ctx: HarnessContext) -> dict[str, Any]:
        model = self.model_for(profile, ctx)
        memory = MemoryAccess(ctx.memory_mode if profile.memory_mode != "none" else "none", memory_roots(ctx))
        skills = self.skills_registry(ctx)
        visible = self.visible_skills(profile, skills, ctx, window=model.context_window)
        tools = self.tools_for(profile, model, ctx, memory, skills_on=profile.skills_mode != "none")
        plain = not profile.tool_groups  # plain chat: no workspace, no field notes, just the role prompt
        system = build_system_prompt(
            profile.role_prompt,
            environment={"date": _dt.date.today().isoformat()} if plain else self.environment(model, ctx),
            skills_block=SkillRegistry.prompt_block(visible),
            memory_block=memory.prompt_block(),
            extra_sections=list(ctx.extra_sections) if plain else [*self.domain_sections(ctx), *ctx.extra_sections],
            include_rules=profile.include_rules,
        )
        return {"model": model, "memory": memory, "skills": skills, "visible_skills": visible, "tools": tools,
                "system": system}

    def build_agent(
        self,
        profile: HarnessProfile,
        ctx: HarnessContext,
        *,
        history: list[Message] | None = None,
        system_override: str | None = None,
        on_message: Callable[[Message], None] | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> Agent:
        parts = self.assemble(profile, ctx)
        model: ModelProfile = parts["model"]
        provider = self.provider_for(model)
        emit = ctx.emit or Emitter(self.app.bus, ctx.topics)
        mode = ctx.permission_mode or profile.permission_mode
        from ..tools.skill_tools import make_skill_gate

        services: dict[str, Any] = {
            "http": self.app.http,
            "kernels": self.app.kernels,
            "skills": parts["skills"],
            "skill_hub": self.app.skill_hub,
            "skill_index": self.app.skill_index,
            "skill_catalog": self.app.catalog,
            "memory": parts["memory"],
            "lit_config": self.app.lit_config(),
            "skill_gate": make_skill_gate(parts["skills"]),
            "stage_id": ctx.stage_id,
            "harness_store": self.harnesses,
            "loop_store": self.app.loops,
            **ctx.extra_services,
        }
        tool_ctx = ToolContext(
            workspace=ctx.workspace,
            readonly_roots=parts["skills"].readonly_roots(),
            emit=emit,
            interactions=self.app.interactions,
            services=services,
            env_passthrough=list(ctx.project.settings.env_passthrough) if ctx.project else ["NCBI_API_KEY"],
            auto_mode=ctx.auto_mode,
            permission_mode=mode,
            conversation_id=ctx.conversation_id,
            run_id=ctx.run_id,
            project_id=ctx.project.id if ctx.project else None,
            topics=ctx.topics,
        )
        budget = ctx.budget or Budget(profile.max_cost_usd)
        if ctx.budget is not None and profile.max_cost_usd:
            budget = ctx.budget.child(profile.max_cost_usd, name=profile.id)
        settings: dict[str, Any] = dict(
            name=profile.name, max_turns=max(0, profile.max_turns), effort=profile.effort,
            max_tokens=profile.max_tokens or None,
            tool_output_limit=profile.tool_output_limit, compact_ratio=profile.compact_ratio,
            tool_timeout_s=profile.tool_timeout_s, stream_idle_timeout_s=profile.stream_idle_timeout_s,
            approvals=approvals_for(mode, profile.approvals),
        )
        settings.update({k: v for k, v in (config_overrides or {}).items() if v is not None})
        config = AgentConfig(**settings)
        agent = Agent(provider=provider, tools=parts["tools"], ctx=tool_ctx,
                      system_prompt=system_override or parts["system"], config=config, history=history,
                      budget=budget, emit=emit, on_message=on_message)
        services["spawn_agent"] = self._spawner(profile, ctx, parts)
        return agent

    def _spawner(self, parent_profile: HarnessProfile, parent_ctx: HarnessContext, parts: dict[str, Any]):
        async def spawn(task: str, role: str | None, tools: list[str] | None, max_turns: int,
                        tool_ctx: ToolContext) -> AgentResult:
            profile = self.harnesses.get(role) if role and role in {h.id for h in self.harnesses.list()} else \
                parent_profile.model_copy(update={"role_prompt": (
                    "You are a focused research sub-agent. Complete the delegated task below and reply with a "
                    "concise, complete report of what you did and found (include file paths).")})
            child_ctx = HarnessContext(
                workspace=parent_ctx.workspace, project=parent_ctx.project, conversation_id=parent_ctx.conversation_id,
                run_id=parent_ctx.run_id, topics=parent_ctx.topics, memory_mode=parent_ctx.memory_mode,
                auto_mode=parent_ctx.auto_mode, emit=tool_ctx.emit.child("delegate", role=role or "worker"),
                budget=(parent_ctx.budget or Budget()).child(name="delegate"), depth=parent_ctx.depth + 1,
                stage_id=parent_ctx.stage_id, model_override=parent_ctx.model_override,
            )
            cap = parent_profile.subagent_max_turns
            overrides: dict[str, Any] = {"max_turns": max_turns if cap <= 0 else min(max_turns, cap)}
            agent = self.build_agent(profile, child_ctx, config_overrides=overrides)
            if tools:
                allowed = set(tools) | {"activate_skill"}
                agent.tools = agent.tools.subset(names=allowed)
                agent.tool_specs = agent.tools.specs()
            return await agent.run(task)

        return spawn

    def context_preview(self, profile: HarnessProfile, ctx: HarnessContext) -> dict[str, Any]:
        """Token breakdown of the fixed context — drives the visual harness page."""
        parts = self.assemble(profile, ctx)
        plain = not profile.tool_groups  # a plain chat gets neither of these; the preview must say the same
        env_text = "" if plain else environment_block(self.environment(parts["model"], ctx))
        skills_text = SkillRegistry.prompt_block(parts["visible_skills"])
        memory_text = parts["memory"].prompt_block()
        domain_text = "" if plain else "\n\n".join(self.domain_sections(ctx))
        specs = parts["tools"].specs()
        tools_tokens = sum(estimate_tokens(json.dumps(s.model_dump(), ensure_ascii=False)) for s in specs)
        sections = [
            {"id": "role", "label": "角色提示", "tokens": estimate_tokens(profile.role_prompt), "text": profile.role_prompt},
            {"id": "rules", "label": "工作与诚信规则", "tokens": estimate_tokens(BASE_RULES) if profile.include_rules else 0,
             "text": BASE_RULES if profile.include_rules else ""},
            {"id": "domain", "label": "领域说明", "tokens": estimate_tokens(domain_text) if domain_text else 0,
             "text": domain_text},
            {"id": "skills", "label": "技能清单", "tokens": estimate_tokens(skills_text) if skills_text else 0,
             "text": skills_text},
            {"id": "memory", "label": "记忆索引", "tokens": estimate_tokens(memory_text) if memory_text else 0,
             "text": memory_text},
            {"id": "environment", "label": "环境信息", "tokens": estimate_tokens(env_text), "text": env_text},
            {"id": "tools", "label": "工具定义", "tokens": tools_tokens, "text": ""},
        ]
        model: ModelProfile = parts["model"]
        return {
            "model": {"id": model.id, "name": model.name, "model": model.model, "provider": model.provider,
                      "context_window": model.context_window},
            "sections": sections,
            "total_tokens": sum(s["tokens"] for s in sections),
            "tools": [{"name": s.name, "group": t.category, "description": s.description[:200],
                       "native": bool(s.native)} for s, t in zip(specs, parts["tools"])],
            "skills": [{"name": r.name, "description": r.description, "trust": r.trust, "root": r.root_kind}
                       for r in parts["visible_skills"]],
            "memory_scopes": parts["memory"].scopes,
            "mcp": self.app.mcp.status(),
        }
