"""Result provenance: render ``{{results/x.json#/pointer:fmt}}`` placeholders from result files.

Numbers in a paper should come from files written by code, never be typed in.
``render_document`` fills placeholders; the lint lists bare numbers in results
sections that have no provenance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .base import ToolContext, ToolError, tool
from ..core.textio import read_text
from .findings import resolve_pointer

PLACEHOLDER = re.compile(r"\{\{\s*([^#}\s]+)#([^}:\s]*)(?::([^}\s]+))?\s*\}\}")
_BARE_NUMBER = re.compile(r"(?<![\w.{/#])(\d+\.\d+|\d{2,}(?:\.\d+)?)%?(?![\w}])")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RESULTS_HEADING = re.compile(r"^(results?|结果|实验结果|findings)\b", re.I)


def _format(value: Any, spec: str | None) -> str:
    if spec:
        try:
            return format(value, spec)
        except (ValueError, TypeError):
            pass
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value) if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False)


def render_text(text: str, workspace: Path) -> tuple[str, list[str]]:
    missing: list[str] = []
    cache: dict[str, Any] = {}

    def replace(match: re.Match[str]) -> str:
        path, pointer, spec = match.group(1), match.group(2), match.group(3)
        target = (workspace / path).resolve()
        try:
            if path not in cache:
                cache[path] = json.loads(read_text(target))
            return _format(resolve_pointer(cache[path], pointer), spec)
        except (OSError, json.JSONDecodeError, KeyError, IndexError, ValueError):
            missing.append(match.group(0))
            return f"[[缺失: {match.group(0)[2:-2].strip()}]]"

    return PLACEHOLDER.sub(replace, text), missing


def lint_bare_numbers(text: str, *, everywhere: bool = False) -> list[str]:
    """Numbers typed into the text instead of coming from a result file.

    Only the Results section is checked unless ``everywhere`` is set (abstract, discussion, conclusions too).
    A sub-heading inside Results does not end the section — only a sibling or higher one does. Fenced code
    blocks are skipped; placeholders and citation keys are stripped before matching.
    """
    issues = []
    in_results = everywhere
    results_level = 0
    in_code = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if _RESULTS_HEADING.match(heading.group(2).strip()):
                in_results, results_level = True, level
            elif in_results and not everywhere and level <= results_level:
                in_results = False
            continue
        if not in_results:
            continue
        clean = PLACEHOLDER.sub("", line)
        clean = re.sub(r"\[[^\]]*@[^\]]*\]", "", clean)
        for m in _BARE_NUMBER.finditer(clean):
            issues.append(f"第 {lineno} 行：数字 {m.group(0)} 没有出处（请改用 {{{{results/…#/…}}}} 占位符）")
    return issues


class RenderArgs(BaseModel):
    source: str = Field("paper/paper.src.md", description="Source file with {{file#/pointer:fmt}} placeholders")
    output: str = Field("paper/paper.md", description="Rendered output path")


@tool("render_document", category="research.write")
async def render_document(args: RenderArgs, ctx: ToolContext):
    """Fill result placeholders like {{results/main.json#/accuracy/mean:.3f}} from result files and write the
    rendered document. Also lists numbers in Results sections that lack provenance."""
    src = ctx.resolve(args.source)
    if not src.exists():
        raise ToolError(f"文件不存在: {args.source}")
    text = read_text(src)
    rendered, missing = render_text(text, ctx.workspace)
    out = ctx.resolve(args.output, write=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    n = len(PLACEHOLDER.findall(text))
    lines = [f"已渲染 {n} 个占位符 → {ctx.display_path(out)}"]
    if missing:
        lines.append("❌ 无法解析的占位符: " + "; ".join(missing))
    lint = lint_bare_numbers(text)
    if lint:
        lines.append("⚠ 结果章节中没有出处的数字:\n" + "\n".join(lint[:30]))
    return "\n".join(lines)


PROVENANCE_TOOLS = [render_document]
