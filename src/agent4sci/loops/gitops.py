"""Workspace versioning with git (commit per node/iteration, keep/discard, rollback)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..core.envfix import find_executable

_IDENTITY = ["-c", "user.name=Agent4Sci", "-c", "user.email=agent4sci@localhost", "-c", "commit.gpgsign=false"]


def _git(workspace: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    git = find_executable("git") or "git"
    # git writes UTF-8 (commit messages here are often Chinese); never decode with the Windows ANSI code page
    return subprocess.run([git, *_IDENTITY, *args], cwd=workspace, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check)


def available(workspace: Path) -> bool:
    return find_executable("git") is not None and (workspace / ".git").exists()


def ensure_repo(workspace: Path) -> bool:
    if available(workspace):
        return True
    if find_executable("git") is None:
        return False
    _git(workspace, "init", "-q")
    return (workspace / ".git").exists()


def head(workspace: Path) -> str | None:
    out = _git(workspace, "rev-parse", "HEAD")
    return out.stdout.strip() or None if out.returncode == 0 else None


def commit_all(workspace: Path, message: str) -> str | None:
    """Stage everything (respecting .gitignore) and commit; returns the new sha or None if nothing changed."""
    if not available(workspace):
        return None
    _git(workspace, "add", "-A")
    status = _git(workspace, "status", "--porcelain")
    if not status.stdout.strip() and head(workspace):
        return None
    args = ["commit", "-q", "-m", message]
    if not head(workspace):
        args.insert(2, "--allow-empty")
    result = _git(workspace, *args)
    return head(workspace) if result.returncode == 0 else None


def discard_changes(workspace: Path) -> None:
    """Drop uncommitted changes to tracked files and remove new untracked (non-ignored) files."""
    if not available(workspace) or not head(workspace):
        return
    _git(workspace, "reset", "-q", "--hard", "HEAD")
    _git(workspace, "clean", "-q", "-fd", "-e", ".a4s")


def log(workspace: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not available(workspace):
        return []
    out = _git(workspace, "log", f"-{limit}", "--pretty=format:%H%x1f%ct%x1f%s")
    rows = []
    for line in out.stdout.splitlines():
        sha, ts, subject = (line.split("\x1f") + ["", "", ""])[:3]
        rows.append({"sha": sha, "time": int(ts or 0), "message": subject})
    return rows


def rollback(workspace: Path, sha: str) -> bool:
    if not available(workspace):
        return False
    commit_all(workspace, f"a4s: 回滚前自动保存（目标 {sha[:8]}）")
    return _git(workspace, "reset", "-q", "--hard", sha).returncode == 0


def diff_stat(workspace: Path, sha: str) -> str:
    return _git(workspace, "show", "--stat", "--format=%s", sha).stdout if available(workspace) else ""
