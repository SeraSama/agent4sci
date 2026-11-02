"""Findings ledger (``findings.jsonl``): claims + the evidence behind them, shared across stages."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..core.textio import read_text
from .base import ToolContext, ToolError, tool
from .bib import BIB_FILE, bib_keys

LEDGER = "findings.jsonl"


def resolve_pointer(data: Any, pointer: str) -> Any:
    """RFC 6901 JSON pointer ("/a/0/b")."""
    if pointer in ("", "/"):
        return data
    current = data
    for raw in pointer.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(part)
    return current


def check_evidence_detail(workspace: Path, evidence: str) -> tuple[bool, str, str]:
    """Check one piece of evidence. Returns (verified, note, strength).

    Evidence forms: bib key ("@smith2020x"), a result file with a JSON pointer ("results/a.json#/acc") — both
    "strong", because the exact value or record is checked — or a bare file path, which is "weak": it only
    proves the file exists, not that it says what the finding claims.
    """
    ev = evidence.strip()
    if "#" in ev and not ev.startswith("@"):
        path, pointer = ev.split("#", 1)
        target = (workspace / path).resolve()
        if not target.exists():
            return False, f"文件不存在: {path}", "none"
        try:
            value = resolve_pointer(json.loads(read_text(target)), pointer)
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            return False, f"JSON 指针无法解析: {ev}", "none"
        return True, f"{ev} = {json.dumps(value, ensure_ascii=False)[:80]}", "strong"
    candidate = (workspace / ev).resolve()
    if candidate.exists() and str(candidate).startswith(str(workspace.resolve())):
        return True, f"文件 {ev}（弱证据：只确认文件存在）", "weak"
    key = ev.lstrip("@")
    bib = workspace / BIB_FILE
    if bib.exists() and key in bib_keys(bib.read_text("utf-8")):
        return True, f"文献 [@{key}]", "strong"
    return False, f"无法核验的证据: {ev}", "none"


def check_evidence(workspace: Path, evidence: str) -> tuple[bool, str]:
    ok, note, _ = check_evidence_detail(workspace, evidence)
    return ok, note


def load_findings(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / LEDGER
    if not path.exists():
        return []
    out = []
    for line in path.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class RecordArgs(BaseModel):
    statement: str = Field(description="The finding, stated precisely (one claim)")
    evidence: list[str] = Field(description="Evidence: bib keys (@key), results files with JSON pointer "
                                            "(results/main.json#/acc/mean), or file paths")
    kind: Literal["result", "literature", "observation", "hypothesis", "negative"] = "result"
    confidence: Literal["low", "medium", "high"] = "medium"
    tags: list[str] = Field(default_factory=list)


@tool("record_finding", category="research.write")
async def record_finding(args: RecordArgs, ctx: ToolContext):
    """Record a finding in the shared ledger (findings.jsonl) with its evidence. Evidence is checked:
    bib keys must exist in references.bib, JSON pointers must resolve, files must exist."""
    if not args.evidence and args.kind != "hypothesis":
        raise ToolError("除假设外，每条发现都必须附带证据。")
    checks = [check_evidence(ctx.workspace, e) for e in args.evidence]
    existing = load_findings(ctx.workspace)
    fid = f"F{len(existing) + 1:03d}"
    record = {"id": fid, "statement": args.statement, "evidence": args.evidence, "kind": args.kind,
              "confidence": args.confidence, "tags": args.tags, "verified": all(ok for ok, _ in checks),
              "checks": [note for _, note in checks], "stage": ctx.services.get("stage_id"), "ts": time.time()}
    with (ctx.workspace / LEDGER).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    ctx.emit("finding.recorded", **{k: record[k] for k in ("id", "statement", "kind", "verified")})
    status = "✅ 证据已核验" if record["verified"] else "⚠ 部分证据无法核验：" + "；".join(
        note for ok, note in checks if not ok)
    return f"已记录 {fid}。{status}"


class QueryArgs(BaseModel):
    query: str | None = Field(None, description="Substring to search in statements/tags")
    kind: str | None = None
    only_verified: bool = False


@tool("query_findings", category="research", parallel_safe=True)
async def query_findings(args: QueryArgs, ctx: ToolContext):
    """List findings from the ledger (optionally filtered)."""
    rows = load_findings(ctx.workspace)
    q = (args.query or "").lower()
    out = []
    for r in rows:
        if args.kind and r.get("kind") != args.kind:
            continue
        if args.only_verified and not r.get("verified"):
            continue
        if q and q not in r.get("statement", "").lower() and not any(q in t.lower() for t in r.get("tags", [])):
            continue
        mark = "✅" if r.get("verified") else "⚠"
        out.append(f"{r['id']} {mark} [{r.get('kind')}/{r.get('confidence')}] {r['statement']}\n"
                   f"    证据: {', '.join(r.get('evidence', []))}")
    return "\n".join(out) if out else "账本中没有匹配的发现。"


FINDINGS_TOOLS = [record_finding, query_findings]
