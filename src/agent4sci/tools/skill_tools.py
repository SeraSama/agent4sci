"""Agent-facing skill tools: activate a skill, search skills, install new ones (with approval)."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from .base import ToolContext, ToolError, tool


class ActivateArgs(BaseModel):
    name: str = Field(description="Skill name from <available_skills> or from search_skills")


@tool("activate_skill", category="skills.read", parallel_safe=True)
async def activate_skill(args: ActivateArgs, ctx: ToolContext):
    """Load a skill's full instructions and list its bundled files. Call this before doing a task that a
    skill covers, then follow the instructions."""
    registry = ctx.service("skills")
    record = registry.get(args.name)
    if record is None:
        matches = [r.name for _, r in registry.search(args.name, limit=5)]
        hint = f"。相近的技能：{', '.join(matches)}" if matches else ""
        raise ToolError(f"没有已启用的技能 {args.name}{hint}")
    ctx.emit("skill.activated", name=record.name, trust=record.trust, path=str(record.path))
    return registry.activation_text(record)


class SearchSkillsArgs(BaseModel):
    query: str
    include_catalog: bool = Field(True, description="Also search the catalog of installable external skills")
    limit: int = Field(8, ge=1, le=30)


@tool("search_skills", category="skills.read", parallel_safe=True)
async def search_skills(args: SearchSkillsArgs, ctx: ToolContext):
    """Search installed skills and (optionally) the catalog of external skills that could be installed."""
    registry = ctx.service("skills")
    lines = []
    for _, record in registry.search(args.query, args.limit):
        lines.append(f"[已安装] {record.name} — {record.description[:200]}")
    index = ctx.services.get("skill_index")
    catalog = ctx.services.get("skill_catalog")
    if args.include_catalog and index is not None and catalog is not None:
        from ..skills.catalog import install_spec_for

        installed = {r.name for r in registry.list(include_inactive=True)}
        for entry in index.search(args.query, args.limit):
            if entry.name in installed:
                continue
            lic = entry.license or "未知许可证"
            lines.append(f"[可安装] {entry.name} — {entry.description[:200]}（来源 {entry.source}，{lic}）"
                         f"\n    安装：install_skill(source=\"{install_spec_for(catalog, entry)}\")")
    if not lines:
        return "没有找到相关技能。" + ("" if index is not None and index.built_at else "（外部技能目录尚未建立索引）")
    return "\n".join(lines)


class InstallArgs(BaseModel):
    source: str = Field(description="owner/repo[/path/to/skill][@ref] or a GitHub URL")
    reason: str = Field("", description="Why this skill is needed")


@tool("install_skill", category="skills.install")
async def install_skill(args: InstallArgs, ctx: ToolContext):
    """Install an external skill into this project (needs the researcher's approval by default). The skill is
    downloaded, license-checked and security-scanned; nothing is executed at install time."""
    hub = ctx.service("skill_hub")
    registry = ctx.service("skills")
    target: Path = ctx.workspace / "skills" / "installed"
    from ..skills.lock import LockFile

    results = await hub.install(args.source, target_dir=target, lock=LockFile(ctx.workspace / "skills.lock"))
    registry.refresh()
    lines = []
    for r in results:
        if r["status"] == "installed":
            lines.append(f"✓ 已安装 {r['name']}（风险 {r['risk']}，许可证 {r.get('license')}）— 现在可以 activate_skill")
        else:
            lines.append(f"✗ {r.get('name', r.get('path'))}: {r.get('reason', r['status'])}")
    ctx.emit("skill.installed", source=args.source, results=results)
    return "\n".join(lines) or "没有安装任何技能。"


SKILL_TOOLS = [activate_skill, search_skills, install_skill]


def make_skill_gate(registry, approved: set[str] | None = None):
    """Shell hook: running scripts of unreviewed third-party skills needs approval (or low risk in auto mode)."""
    approved = approved if approved is not None else set()

    async def gate(command: str, ctx: ToolContext) -> str | None:
        for record in registry.list():
            if record.trust != "unreviewed" or record.name in approved:
                continue
            path = str(record.path)
            rel = f"skills/installed/{record.name}"
            if path in command or rel in command or re.search(rf"\b{re.escape(record.name)}/scripts/", command):
                risk = (record.lock.scan or {}).get("risk", "unknown") if record.lock else "unknown"
                if ctx.auto_mode and risk == "low":
                    return None
                if ctx.auto_mode or ctx.interactions is None:
                    return f"未审核技能 {record.name} 的脚本（扫描风险 {risk}）"
                answer = await ctx.interactions.ask(
                    "approval", f"允许运行未审核技能 {record.name} 的脚本？",
                    {"tool": "shell", "category": "skills.exec", "input": {"command": command},
                     "reason": f"扫描风险：{risk}", "skill": record.name},
                    topics=ctx.topics,
                )
                if answer.get("decision") == "allow":
                    approved.add(record.name)
                    return None
                return f"用户未允许运行技能 {record.name} 的脚本"
        return None

    return gate
