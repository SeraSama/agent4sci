"""System prompt assembly.

Order matters for prompt caching: stable text first (role, rules), then the
skills catalog and memory index, then the environment block. The prompt is
built once per session and then frozen — editing it mid-conversation would
invalidate the cache and Claude's thinking-block bindings.
"""

from __future__ import annotations

import datetime as _dt
import platform
from typing import Any

BASE_RULES = """\
# Working rules
- Reply in the user's language (Simplified Chinese unless the user writes in another language).
- Use tools to act; do not claim to have done something you did not do. When you finish, say plainly what was done, what was not, and where the outputs are.
- Paths are relative to the workspace unless stated otherwise. Keep files organised (notes/, lit/, code/, data/, results/, figures/, paper/).
- Prefer small, verifiable steps. Check results (shapes, units, sample sizes, sanity values) before building on them.
- When you ask the researcher (ask_human), ask everything that blocks you now in one call (up to 4 questions), each with
  2-4 concrete options and your default marked; put shared background in `context`. They can also type their own answer.
  Do not ask about things you can find out yourself, and decide anything they skip — writing the assumption down.

# Scientific integrity
- Never invent data, results, numbers, quotes or citations. Every number you report must come from a file produced by code in this workspace or from a retrieved source.
- Cite only papers that a tool actually retrieved (add them with add_reference). If you are unsure whether a source supports a claim, say so.
- Distinguish clearly between established findings, your hypotheses, and speculation. Report negative and null results honestly.
- Do not modify evaluation code, metrics or data splits after seeing results to make results look better.
"""


def environment_block(env: dict[str, Any]) -> str:
    lines = ["# Environment"]
    lines.append(f"- Date: {env.get('date') or _dt.date.today().isoformat()}")
    if env.get("os"):
        lines.append(f"- OS: {env['os']} ({platform.machine()})")
    if env.get("shell"):
        lines.append(f"- Shell used by the shell tool: {env['shell']}")
    if env.get("workspace"):
        lines.append(f"- Workspace: {env['workspace']}")
    if env.get("project"):
        lines.append(f"- Project: {env['project']}")
    if env.get("compute"):
        lines.append(f"- Compute: {env['compute']}")
    if env.get("python"):
        lines.append(f"- Python for experiments: {env['python']}")
    for key, value in env.get("extra", {}).items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def build_system_prompt(
    role_prompt: str,
    *,
    environment: dict[str, Any] | None = None,
    skills_block: str = "",
    memory_block: str = "",
    extra_sections: list[str] | None = None,
    include_rules: bool = True,
) -> str:
    parts = [role_prompt.strip()]
    if include_rules:
        parts.append(BASE_RULES.strip())
    for section in extra_sections or []:
        if section.strip():
            parts.append(section.strip())
    if skills_block.strip():
        parts.append(skills_block.strip())
    if memory_block.strip():
        parts.append(memory_block.strip())
    if environment is not None:  # None = a plain chat: say nothing about the machine or the workspace
        parts.append(environment_block(environment))
    return "\n\n".join(parts)


DEFAULT_ASSISTANT_ROLE = """\
You are Agent4Sci, a research assistant that helps scientists do rigorous, reproducible research: \
finding and reading literature, analysing data, designing and running computational experiments, \
and writing up results. You work inside a project workspace with tools for files, shell, Python, \
literature search, citations, memory and skills."""

PLAIN_ASSISTANT_ROLE = """\
You are a helpful, accurate assistant. Answer the question directly, show your reasoning when it matters, \
and say plainly when you are unsure or when something cannot be known. In this mode you have no tools: \
you cannot read or write files, run code or search the web, so never claim to have done so — if a task \
needs that, say which tools-enabled mode the user should switch to."""
