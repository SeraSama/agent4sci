"""Project self-check: run the integrity checks on demand and report what would not survive review.

This is the user-triggered counterpart of a loop's ``checks`` node — it never blocks anything, it only
reports. The checks themselves are the ones the agents already use (``loops.checks.run_checks``), plus a
re-verification of the findings ledger: ``verified`` is frozen when a finding is recorded, so a result file
that changed afterwards leaves claims that nobody ever re-checks.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..tools.bib import BIB_FILE
from ..tools.findings import check_evidence_detail, load_findings
from ..tools.provenance import PLACEHOLDER
from .textio import read_text

REPORT_DIR = ".a4s/audit"
DELIVERABLES = ("paper/paper.md", "lit/review.md")  # what the project claims; review/ is a critique of it
SOURCES = ("paper/paper.src.md",)
MISSING_MARK = "[[缺失:"


def _scratch(path: Path) -> bool:
    """Files like paper/_draft.md are working copies, not deliverables."""
    return path.name.startswith("_") or path.name.startswith(".")


def default_audit_config(workspace: Path) -> dict[str, Any]:
    """What to check in this project, from what it actually contains."""
    docs = [rel for rel in DELIVERABLES if (workspace / rel).exists()]
    docs += sorted(p.relative_to(workspace).as_posix() for p in (workspace / "paper").glob("*.md")
                   if not _scratch(p) and not p.name.endswith(".src.md")
                   and p.relative_to(workspace).as_posix() not in docs)
    sources = [rel for rel in SOURCES if (workspace / rel).exists()]
    sources += sorted(p.relative_to(workspace).as_posix() for p in (workspace / "paper").glob("*.src.md")
                      if not _scratch(p) and p.relative_to(workspace).as_posix() not in sources)
    results = sorted(p.relative_to(workspace).as_posix() for p in (workspace / "results").glob("*.json")
                     if not _scratch(p))
    artifacts = [rel.replace(".src.md", ".md") for rel in sources]  # a source implies a rendered output
    if docs and (workspace / BIB_FILE).exists():
        artifacts.append(BIB_FILE)
    return {
        "artifacts": sorted(set(artifacts)),
        "citations": docs,
        "numbers": docs,
        "placeholders": sources,
        "json_valid": results,
        "findings": True,
    }


def reverify_findings(workspace: Path) -> dict[str, Any]:
    """Re-run every finding's evidence against the files as they are now."""
    drifted: list[dict[str, Any]] = []
    unverifiable: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []
    findings = load_findings(workspace)
    for item in findings:
        evidence = item.get("evidence") or []
        checks = [check_evidence_detail(workspace, e) for e in evidence]
        ok_now = all(ok for ok, _, _ in checks) if checks else item.get("kind") == "hypothesis"
        entry = {"id": item.get("id"), "statement": item.get("statement", "")[:300],
                 "notes": [note for ok, note, _ in checks if not ok][:5]}
        if not ok_now:
            (drifted if item.get("verified") else unverifiable).append(entry)
        elif any(strength == "weak" for _, _, strength in checks):
            weak.append({**entry, "notes": [note for _, note, s in checks if s == "weak"][:5]})
    return {"total": len(findings), "drifted": drifted, "unverifiable": unverifiable, "weak": weak}


def _rendered_gaps(workspace: Path, docs: list[str]) -> list[str]:
    """Rendered documents that still carry [[缺失: …]] markers from an unresolved placeholder."""
    problems = []
    for rel in docs:
        path = workspace / rel
        if not path.exists():
            continue
        text = read_text(path)
        count = text.count(MISSING_MARK)
        if count:
            problems.append(f"{rel}: {count} 处数字没有渲染出来（文中仍是 [[缺失: …]] 标记）")
        elif PLACEHOLDER.search(text):
            problems.append(f"{rel}: 还留着未渲染的占位符，请先运行 render_document")
    return problems


def _attach_files(messages: list[str], known: list[str]) -> list[dict[str, Any]]:
    """Pair each message with the project file it mentions, so the UI can open it."""
    out = []
    for message in messages:
        match = next((rel for rel in known if rel and rel in message), None)
        out.append({"message": message, "file": match})
    return out


def audit_project(workspace: Path, config: dict[str, Any] | None = None, *, save: bool = True) -> dict[str, Any]:
    """Run the project self-check and return a report (also written to .a4s/audit/ unless save=False)."""
    from ..loops.checks import run_checks  # local import: keeps core free of a loops import at module level

    cfg = {**default_audit_config(workspace), **(config or {})}
    result = run_checks(cfg, workspace, protected={})
    errors = list(result["errors"])
    warnings = list(result["warnings"])
    errors += _rendered_gaps(workspace, list(cfg.get("citations") or []))

    findings = reverify_findings(workspace)
    for item in findings["drifted"]:
        errors.append(f"发现 {item['id']} 记录时已核验，现在核验不通过：{'；'.join(item['notes']) or '证据已失效'}")
    for item in findings["unverifiable"]:
        warnings.append(f"发现 {item['id']} 的证据无法核验：{'；'.join(item['notes']) or '缺少可核验的证据'}")
    if findings["weak"]:
        warnings.append(f"{len(findings['weak'])} 条发现只有弱证据（仅确认文件存在，没有指向具体数值或文献）")

    known = [*(cfg.get("citations") or []), *(cfg.get("placeholders") or []), *(cfg.get("artifacts") or []),
             *(cfg.get("json_valid") or [])]
    report = {
        "ts": time.time(),
        "passed": not errors,
        "checked": {k: v for k, v in cfg.items() if isinstance(v, list) and v},
        "errors": _attach_files(errors, known),
        "warnings": _attach_files(warnings, known),
        "findings": findings,
    }
    if save:
        directory = workspace / REPORT_DIR
        directory.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(report["ts"]))
        path = directory / f"{name}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        report["path"] = str(path)
    return report


def latest_report(workspace: Path) -> dict[str, Any] | None:
    directory = workspace / REPORT_DIR
    files = sorted(directory.glob("*.json")) if directory.exists() else []
    for path in reversed(files):
        try:
            return {**json.loads(read_text(path)), "path": str(path)}
        except (OSError, ValueError):
            continue
    return None
