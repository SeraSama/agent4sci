"""Rule-based checks used by ``checks`` nodes (and by reviewers)."""

from __future__ import annotations

import glob
import json
import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from ..tools.bib import BIB_FILE, check_citations_text
from ..core.textio import read_text
from ..tools.findings import load_findings
from ..tools.provenance import PLACEHOLDER, lint_bare_numbers, render_text


def _files(workspace: Path, pattern: str) -> list[Path]:
    target = workspace / pattern
    if target.is_dir():
        return sorted(p for p in target.rglob("*") if p.is_file())
    return sorted(Path(p) for p in glob.glob(str(target), recursive=True) if Path(p).is_file())


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protect(workspace: Path, patterns: list[str]) -> dict[str, str]:
    """Record hashes of protected files and make them read-only."""
    hashes = {}
    for pattern in patterns:
        for path in _files(workspace, pattern):
            rel = path.relative_to(workspace).as_posix()
            hashes[rel] = file_hash(path)
            try:
                os.chmod(path, path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
            except OSError:
                pass
    return hashes


def verify_protected(workspace: Path, recorded: dict[str, str]) -> list[str]:
    problems = []
    for rel, digest in recorded.items():
        path = workspace / rel
        if not path.exists():
            problems.append(f"受保护文件被删除: {rel}")
        elif file_hash(path) != digest:
            problems.append(f"受保护文件被修改: {rel}")
    return problems


def run_checks(config: dict[str, Any], workspace: Path, protected: dict[str, str]) -> dict[str, Any]:
    """config keys: artifacts [paths], citations [docs], numbers [src docs], placeholders [src docs],
    findings (bool), protect [patterns], verify_protected (bool)."""
    errors: list[str] = []
    warnings: list[str] = []
    new_protected: dict[str, str] = {}

    for rel in config.get("artifacts", []) or []:
        if not _files(workspace, rel) and not (workspace / rel).exists():
            errors.append(f"缺少产出: {rel}")
    bib = workspace / BIB_FILE
    bib_text = bib.read_text("utf-8") if bib.exists() else ""
    for rel in config.get("citations", []) or []:
        doc = workspace / rel
        if not doc.exists():
            errors.append(f"无法检查引用，文件不存在: {rel}")
            continue
        report = check_citations_text(read_text(doc), bib_text)
        if report["unknown"]:
            errors.append(f"{rel} 引用了不存在的文献键: {', '.join(report['unknown'])}")
        if not report["cited"]:
            warnings.append(f"{rel} 没有任何引用")
    for rel in config.get("numbers", []) or []:
        doc = workspace / rel
        if doc.exists():
            lint = lint_bare_numbers(read_text(doc))
            if lint:
                warnings.append(f"{rel}: {len(lint)} 个结果数字没有出处（示例：{lint[0]}）")
    for rel in config.get("placeholders", []) or []:
        doc = workspace / rel
        if doc.exists():
            text = read_text(doc)
            _, missing = render_text(text, workspace)
            if missing:
                errors.append(f"{rel}: {len(missing)} 个结果占位符无法解析")
            if not PLACEHOLDER.search(text):
                warnings.append(f"{rel} 没有使用结果占位符")
    for rel in config.get("json_valid", []) or []:
        doc = workspace / rel
        try:
            json.loads(read_text(doc))
        except (OSError, ValueError):
            errors.append(f"{rel} 不存在或不是合法 JSON")
    if config.get("findings"):
        unverified = [f["id"] for f in load_findings(workspace) if not f.get("verified")]
        if unverified:
            warnings.append(f"发现账本中有 {len(unverified)} 条证据未核验: {', '.join(unverified[:10])}")
    if config.get("verify_protected") and protected:
        errors.extend(verify_protected(workspace, protected))
    if config.get("protect"):
        new_protected = protect(workspace, list(config["protect"]))
    return {"passed": not errors, "errors": errors, "warnings": warnings, "protected": new_protected}
