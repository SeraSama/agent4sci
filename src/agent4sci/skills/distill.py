"""Self-authored skills: turn what worked in a run into reusable SKILL.md packages.

Proposals are validated strictly, scanned, and written to ``skills/learned`` as
*pending*; they only become visible to agents after the user approves them.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from ..agent.agent import Agent, AgentConfig
from ..agent.budget import Budget
from ..core.events import Emitter
from ..llm.base import LLMProvider
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry
from ..tools.result import ResultSink, SubmitResultTool
from .registry import SkillRecord, set_learned_state
from .scanner import scan_skill
from .spec import NAME_RE, SkillParseError, load_skill, render_skill_md

PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skills": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "lowercase-hyphenated, ≤64 chars"},
                    "description": {"type": "string", "description": "What it does AND when to use it (≤1024 chars)"},
                    "body": {"type": "string", "description": "SKILL.md body in Markdown"},
                    "scripts": {
                        "type": "array",
                        "items": {"type": "object",
                                  "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                  "required": ["path", "content"]},
                    },
                    "update_of": {"type": "string", "description": "Name of an existing learned skill this replaces"},
                    "reason": {"type": "string"},
                },
                "required": ["name", "description", "body"],
            },
        }
    },
    "required": ["skills"],
}

DISTILL_PROMPT = """\
You distill reusable Agent Skills from a finished research run. A skill is a folder with a SKILL.md \
(YAML frontmatter: name, description; Markdown body) plus optional scripts. Propose a skill only for a \
procedure that (1) actually worked in this run, (2) is likely to recur, and (3) is not already covered by an \
existing skill. Proposing nothing is fine.

Writing guidelines:
- name: lowercase letters, digits and hyphens.
- description: say what the skill does AND when to use it, with concrete trigger words.
- body: concise, imperative steps; the exact commands/code that worked; pitfalls and how they were fixed; \
how to verify the result. Keep it under ~300 lines. Reference scripts by relative path (scripts/…).
- scripts: small, self-contained, with a PEP 723 header listing dependencies (# /// script … # ///) so they \
run with `uv run`. Never include secrets, personal data or absolute paths from this machine.
Submit with submit_result."""


def trace_digest(events: list[dict[str, Any]], max_chars: int = 40_000) -> str:
    """Compact, model-readable account of a run from its trace events."""
    lines: list[str] = []
    for ev in events:
        etype = ev.get("type")
        data = ev.get("data", ev)
        if etype == "tool.call":
            lines.append(f"CALL {data.get('name')} {str(data.get('input'))[:300]}")
        elif etype == "tool.result":
            status = "ERROR" if data.get("is_error") else "ok"
            lines.append(f"  → {status}: {str(data.get('preview', ''))[:300]}")
        elif etype == "agent.message":
            msg = data.get("message", {})
            if msg.get("role") == "assistant":
                text = "".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text")
                if text.strip():
                    lines.append(f"ASSISTANT: {text.strip()[:500]}")
        elif etype in ("node.done", "node.failed"):
            lines.append(f"NODE {data.get('node')} {etype}: {str(data.get('summary', ''))[:300]}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars // 2] + "\n…\n" + text[-max_chars // 2:]


async def propose_skills(
    provider: LLMProvider,
    *,
    digest: str,
    existing: list[SkillRecord],
    workspace: Path,
    emit: Emitter | None = None,
    budget: Budget | None = None,
) -> list[dict[str, Any]]:
    listing = "\n".join(f"- {r.name}: {r.description[:150]}" for r in existing) or "（无）"
    task = f"Existing skills:\n{listing}\n\nRun trace:\n<trace>\n{digest}\n</trace>\n\nPropose skills now."
    sink = ResultSink(PROPOSAL_SCHEMA)
    ctx = ToolContext(workspace=workspace, emit=emit or Emitter.null(), services={"result_sink": sink})
    agent = Agent(provider=provider, tools=ToolRegistry([SubmitResultTool(PROPOSAL_SCHEMA)]), ctx=ctx,
                  system_prompt=DISTILL_PROMPT, budget=budget,
                  config=AgentConfig(name="skill-distiller", max_turns=4, stop_on_submit=True))
    await agent.run(task)
    return list((sink.value or {}).get("skills", [])) if sink.submitted else []


_SAFE_REL = re.compile(r"^(?!/)(?!.*\.\.)[\w\-./]+$")


def write_learned_skill(learned_root: Path, proposal: dict[str, Any], *, run_id: str = "") -> dict[str, Any]:
    name = str(proposal.get("name", "")).strip()
    if not NAME_RE.match(name) or len(name) > 64:
        return {"name": name, "status": "invalid", "reason": "name 不符合规范"}
    target = learned_root / name
    if target.exists():
        history = learned_root / ".history" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
        history.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(history))
    target.mkdir(parents=True)
    meta = {
        "name": name,
        "description": " ".join(str(proposal.get("description", "")).split())[:1024],
        "metadata": {"origin": "learned", "run-id": run_id, "created": time.strftime("%Y-%m-%d")},
    }
    (target / "SKILL.md").write_text(render_skill_md(meta, str(proposal.get("body", ""))), "utf-8")
    for script in proposal.get("scripts") or []:
        rel = str(script.get("path", "")).strip()
        if not _SAFE_REL.match(rel):
            continue
        path = target / (rel if rel.startswith("scripts/") else f"scripts/{rel}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(script.get("content", "")), "utf-8")
    try:
        load_skill(target, strict=True)
    except SkillParseError as exc:
        shutil.rmtree(target)
        return {"name": name, "status": "invalid", "reason": str(exc)}
    report = scan_skill(target, external=False)
    if report.risk == "high":
        shutil.rmtree(target)
        return {"name": name, "status": "rejected", "reason": "安全扫描发现高风险内容", "scan": report.summary()}
    set_learned_state(learned_root, name, "pending")
    return {"name": name, "status": "pending", "path": str(target), "scan": report.summary()}


def review_learned(learned_root: Path, name: str, approve: bool) -> None:
    set_learned_state(learned_root, name, "approved" if approve else "rejected")
