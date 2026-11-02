"""Install skills from GitHub / git / local folders, with pinning, license checks and scanning.

Nothing from a skill is executed at install time.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from ..core.envfix import find_executable
from ..core.paths import cache_dir
from ..tools.http import HttpClient, HttpError
from .lock import LockEntry, LockFile, dir_digest
from .scanner import ScanReport, scan_skill
from .spec import SKILL_FILE_NAMES, SkillParseError, find_skill_file, load_skill

SKIP_DIRS = {".git", ".github", "node_modules", "__pycache__", ".venv"}
MAX_COPY_FILE = 20_000_000
# Sparse GitHub fetches (tree listing + raw files) are used for large repositories when the skills needed are a
# small part of them; otherwise one tarball download is cheaper.
SPARSE_MIN_REPO_BYTES = 5_000_000
SPARSE_MAX_FILES = 2000
LICENSE_PREFIXES = ("license", "licence", "copying")


class SkillSource(BaseModel):
    kind: Literal["github", "git", "local"]
    spec: str
    owner: str | None = None
    repo: str | None = None
    ref: str | None = None
    subpath: str = ""
    url: str | None = None
    path: Path | None = None

    @property
    def repo_id(self) -> str | None:
        return f"{self.owner}/{self.repo}" if self.owner and self.repo else None


_GH_TREE = re.compile(r"^https?://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?/?$")


def parse_source(spec: str) -> SkillSource:
    raw = spec.strip()
    if raw.startswith("gh:"):
        raw = raw[3:]
    match = _GH_TREE.match(raw)
    if match:
        owner, repo, ref, sub = match.groups()
        sub = (sub or "").strip("/")
        if sub.endswith(tuple(SKILL_FILE_NAMES)):
            sub = sub.rsplit("/", 1)[0] if "/" in sub else ""
        return SkillSource(kind="github", spec=spec, owner=owner, repo=repo, ref=ref, subpath=sub)
    local = Path(raw).expanduser()
    if raw.startswith((".", "/", "~")) or (local.exists() and local.is_dir()):
        return SkillSource(kind="local", spec=spec, path=local.resolve())
    if raw.startswith(("git@", "ssh://", "file://")) or raw.endswith(".git") or re.match(r"^https?://", raw):
        ref = None
        if "#" in raw:
            raw, ref = raw.split("#", 1)
        return SkillSource(kind="git", spec=spec, url=raw, ref=ref)
    ref = None
    if "@" in raw:
        raw, ref = raw.rsplit("@", 1)
    parts = [p for p in raw.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"无法识别的技能来源: {spec}（示例：owner/repo 或 owner/repo/path/to/skill@ref）")
    return SkillSource(kind="github", spec=spec, owner=parts[0], repo=parts[1], ref=ref, subpath="/".join(parts[2:]))


# ---- licenses ---------------------------------------------------------------------

OPEN_LICENSES = ("MIT", "Apache-2.0", "BSD", "ISC", "CC-BY", "CC0", "Unlicense", "MPL-2.0", "LGPL", "GPL", "AGPL",
                 "Zlib", "0BSD", "Python-2.0", "Artistic")


def classify_license(text: str | None) -> tuple[str | None, str]:
    """Return (normalised id, status) where status is open | proprietary | unknown | none."""
    if not text or not text.strip():
        return None, "none"
    t = text.strip()
    low = t.lower()
    if any(w in low for w in ("proprietary", "all rights reserved", "commercial license", "not licensed")):
        return "Proprietary", "proprietary"
    checks = [
        ("mit license", "MIT"), ("apache license", "Apache-2.0"), ("apache-2", "Apache-2.0"),
        ("bsd 3-clause", "BSD-3-Clause"), ("bsd-3", "BSD-3-Clause"), ("bsd 2-clause", "BSD-2-Clause"),
        ("bsd-2", "BSD-2-Clause"), ("isc license", "ISC"), ("creative commons attribution", "CC-BY-4.0"),
        ("cc-by", "CC-BY-4.0"), ("cc by", "CC-BY-4.0"), ("cc0", "CC0-1.0"), ("unlicense", "Unlicense"),
        ("mozilla public license", "MPL-2.0"), ("mpl-2", "MPL-2.0"), ("lesser general public", "LGPL"),
        ("affero", "AGPL"), ("gnu general public", "GPL"), ("gpl", "GPL"),
    ]
    for needle, ident in checks:
        if needle in low:
            return ident, "open"
    if re.fullmatch(r"(?i)mit", t) or low.startswith("mit"):
        return "MIT", "open"
    if low.startswith("apache"):
        return "Apache-2.0", "open"
    if low.startswith("bsd"):
        return "BSD", "open"
    if "permission is hereby granted, free of charge" in low:
        return "MIT", "open"
    return t[:40], "unknown"


def _license_dirs(skill_dir: Path, repo_root: Path | None) -> list[Path]:
    """The skill folder and its parents up to the repository root (or a ``.git`` folder, max 6 levels)."""
    dirs = [skill_dir]
    root = repo_root.resolve() if repo_root else None
    current = skill_dir.resolve()
    for _ in range(6):
        if root is not None and current == root:
            break
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
        dirs.append(current)
        if root is None and (current / ".git").exists():
            break
    if root is not None and root not in dirs:
        dirs.append(root)
    return dirs


def skill_license(skill_dir: Path, meta_license: str | None, repo_root: Path | None) -> tuple[str | None, str]:
    if meta_license:
        pointed = skill_dir / meta_license.strip()
        if len(meta_license) < 100 and pointed.is_file():
            return classify_license(pointed.read_text("utf-8", errors="replace")[:5000])
        ident, status = classify_license(meta_license)
        if status != "unknown":
            return ident, status
    for directory in _license_dirs(skill_dir, repo_root):
        for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING"):
            f = directory / name
            if f.is_file():
                return classify_license(f.read_text("utf-8", errors="replace")[:5000])
    if meta_license:
        return classify_license(meta_license)
    return None, "none"


# ---- fetching ------------------------------------------------------------------------


class FetchedRepo(BaseModel):
    root: Path
    commit: str | None = None
    ref: str | None = None


def _safe_extract(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if len(parts) <= 1:
                continue
            rel = Path(*parts[1:])  # strip "<repo>-<sha>/"
            target = (root / rel).resolve()
            if root not in target.parents or member.issym() or member.islnk() or member.isdev():
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is not None:
                    target.write_bytes(source.read())


class SkillHub:
    def __init__(self, http: HttpClient | None = None, cache: Path | None = None) -> None:
        self.http = http or HttpClient()
        self.cache = cache or cache_dir() / "skill-repos"

    @staticmethod
    def _gh_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept}
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _resolve_commit(self, owner: str, repo: str, ref: str | None) -> str | None:
        try:
            resp = await self.http.get(f"https://api.github.com/repos/{owner}/{repo}/commits/{ref or 'HEAD'}",
                                       headers=self._gh_headers("application/vnd.github.sha"))
            sha = resp.text.strip()
            return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None
        except HttpError:
            return None

    # ---- sparse GitHub fetch -----------------------------------------------------------
    async def _tree(self, owner: str, repo: str, commit: str) -> dict[str, int] | None:
        """Regular files at a commit (path → size) from one API call; None when unavailable or truncated."""
        try:
            resp = await self.http.get(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{commit}",
                                       params={"recursive": "1"}, headers=self._gh_headers(), cache_ttl=7 * 86400)
            data = resp.json()
        except (HttpError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("truncated") or not isinstance(data.get("tree"), list):
            return None
        return {item["path"]: int(item.get("size") or 0) for item in data["tree"]
                if item.get("type") == "blob" and item.get("mode") != "120000" and isinstance(item.get("path"), str)}

    @staticmethod
    def tree_skill_dirs(files: Iterable[str], subpath: str = "") -> list[str]:
        """discover() on a file listing: the top-most folders under ``subpath`` that contain a SKILL.md."""
        base = subpath.strip("/")
        found: set[str] = set()
        for path in files:
            parent, _, name = path.rpartition("/")
            if name not in SKILL_FILE_NAMES:
                continue
            if base and parent != base and not parent.startswith(base + "/"):
                continue
            rel = parent[len(base):].lstrip("/") if base else parent
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel.split("/") if part):
                continue
            found.add(parent)
        if base in found:
            return [base]
        top: list[str] = []
        for d in sorted(found, key=lambda x: (x.count("/"), x)):
            if not any(d.startswith(t + "/") or not t for t in top):  # a skill's sub-folders are resources
                top.append(d)
        return sorted(top)

    @staticmethod
    def _license_paths(files: dict[str, int], dirs: list[str]) -> list[str]:
        """License files in each skill folder and in its parents up to the repository root."""
        folders = set()
        for d in dirs:
            parts = d.split("/") if d else []
            folders.update("/".join(parts[:i]) for i in range(len(parts) + 1))
        return [p for p in files if p.rpartition("/")[0] in folders
                and p.rpartition("/")[2].lower().startswith(LICENSE_PREFIXES)]

    async def _download(self, owner: str, repo: str, commit: str, root: Path, paths: Iterable[str]) -> None:
        base = root.resolve()

        async def one(path: str) -> None:
            target = (root / path).resolve()
            if base not in target.parents or target.exists():
                return
            resp = await self.http.get(f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{quote(path)}",
                                       max_bytes=MAX_COPY_FILE)
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            part.write_bytes(resp.content)
            part.replace(target)  # complete files only: the folder doubles as a per-commit cache

        root.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(*(one(p) for p in paths))

    async def _fetch_sparse(self, source: SkillSource, commit: str, select: list[str] | None,
                            metadata_only: bool) -> FetchedRepo | None:
        """Download only the skills needed (or only their SKILL.md and licenses) from a large repository."""
        owner, repo = source.owner or "", source.repo or ""
        files = await self._tree(owner, repo, commit)
        if not files or sum(files.values()) < SPARSE_MIN_REPO_BYTES:
            return None  # small repository: a single tarball is simpler
        dirs = self.tree_skill_dirs(files, source.subpath)
        if not dirs:
            return None
        root = self.cache / "github-sparse" / owner / repo / commit
        skill_md = {d: next(p for p in (f"{d}/{n}".lstrip("/") for n in SKILL_FILE_NAMES) if p in files) for d in dirs}
        chosen = dirs
        wanted = set(select or [])
        if wanted:
            chosen = [d for d in dirs if d.rpartition("/")[2] in wanted]
            unmatched = wanted - {d.rpartition("/")[2] for d in chosen}
            if unmatched:  # frontmatter names can differ from folder names: read the other SKILL.md files
                others = [d for d in dirs if d not in chosen]
                if len(others) > SPARSE_MAX_FILES:
                    return None
                await self._download(owner, repo, commit, root, [skill_md[d] for d in others])
                for d in others:
                    try:
                        if load_skill(root / d, strict=False).meta.name in unmatched:
                            chosen.append(d)
                    except SkillParseError:
                        continue
            if not chosen:
                raise RuntimeError(f"仓库中没有找到这些技能: {', '.join(sorted(wanted))}")
        if metadata_only:
            needed = [skill_md[d] for d in chosen]
        else:
            needed = [p for p in files if any(not d or p.startswith(d + "/") for d in chosen)
                      and not any(part in SKIP_DIRS for part in p.split("/")) and files[p] <= MAX_COPY_FILE]
            if sum(files[p] for p in needed) > 0.5 * sum(files.values()):
                return None  # most of the repository anyway
        needed += self._license_paths(files, chosen)
        if len(needed) > SPARSE_MAX_FILES:
            return None
        await self._download(owner, repo, commit, root, sorted(set(needed)))
        return FetchedRepo(root=root, commit=commit, ref=source.ref)

    async def fetch(self, source: SkillSource, select: list[str] | None = None, *,
                    metadata_only: bool = False) -> FetchedRepo:
        """Get a skill source onto disk. ``select`` limits a GitHub fetch to those skills; ``metadata_only``
        fetches just SKILL.md and license files (enough for indexing) when the repository is large."""
        if source.kind == "local":
            assert source.path is not None
            if not source.path.is_dir():
                raise FileNotFoundError(f"目录不存在: {source.path}")
            return FetchedRepo(root=source.path)
        if source.kind == "git":
            return await self._fetch_git(source)
        owner, repo = source.owner or "", source.repo or ""
        commit = await self._resolve_commit(owner, repo, source.ref)
        if commit:
            sparse = await self._fetch_sparse(source, commit, select, metadata_only)
            if sparse is not None:
                return sparse
        candidates = [commit] if commit else ([source.ref] if source.ref else ["HEAD", "main", "master"])
        last_error: Exception | None = None
        for ref in candidates:
            dest = self.cache / "github" / owner / repo / (ref or "HEAD")
            if commit and dest.exists() and any(dest.iterdir()):
                return FetchedRepo(root=dest, commit=commit, ref=source.ref)
            try:
                resp = await self.http.get(f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}",
                                           max_bytes=300_000_000)
            except HttpError as exc:
                last_error = exc
                continue
            if dest.exists():
                shutil.rmtree(dest)
            _safe_extract(resp.content, dest)
            return FetchedRepo(root=dest, commit=commit, ref=source.ref or (None if commit else ref))
        raise RuntimeError(f"无法下载 {owner}/{repo}: {last_error}")

    async def _fetch_git(self, source: SkillSource) -> FetchedRepo:
        git = find_executable("git")
        if git is None:
            raise RuntimeError("需要 git 才能从这个地址安装（GitHub 仓库无需 git）")
        dest = Path(tempfile.mkdtemp(prefix="a4s-skill-", dir=self.cache.parent if self.cache.parent.exists() else None))
        args = [git, "clone", "--depth", "1"]
        if source.ref:
            args += ["--branch", source.ref]
        args += [source.url or "", str(dest / "repo")]
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone 失败: {err.decode(errors='replace')[:300]}")
        rev = await asyncio.create_subprocess_exec(git, "-C", str(dest / "repo"), "rev-parse", "HEAD",
                                                   stdout=asyncio.subprocess.PIPE)
        out, _ = await rev.communicate()
        return FetchedRepo(root=dest / "repo", commit=out.decode().strip() or None, ref=source.ref)

    # ---- discovery & install ----------------------------------------------------------
    @staticmethod
    def discover(root: Path, subpath: str = "") -> list[Path]:
        base = (root / subpath) if subpath else root
        if not base.exists():
            raise FileNotFoundError(f"仓库中没有这个路径: {subpath}")
        if find_skill_file(base) is not None:
            return [base]
        found = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            if any(name in filenames for name in SKILL_FILE_NAMES):
                found.append(Path(dirpath))
                dirnames[:] = []  # a skill's own sub-folders are resources, not skills
        return found

    async def install(
        self,
        spec: str,
        *,
        target_dir: Path,
        lock: LockFile,
        select: list[str] | None = None,
        allow_license: bool = False,
        force: bool = False,
        tags: list[str] | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        source = parse_source(spec)
        fetched = await self.fetch(source, select)
        skill_dirs = self.discover(fetched.root, source.subpath)
        if not skill_dirs:
            raise RuntimeError("没有找到任何 SKILL.md")
        results: list[dict[str, Any]] = []
        wanted = set(select or [])
        for skill_dir in skill_dirs:
            item: dict[str, Any] = {"path": skill_dir.relative_to(fetched.root).as_posix() or "."}
            try:
                parsed = load_skill(skill_dir, strict=False)
            except SkillParseError as exc:
                results.append({**item, "status": "invalid", "reason": str(exc)})
                continue
            name = parsed.meta.name
            item["name"] = name
            if wanted and name not in wanted and skill_dir.name not in wanted:
                continue
            repo_root = None if source.kind == "local" else fetched.root
            ident, status = skill_license(skill_dir, parsed.meta.license, repo_root)
            item.update(license=ident, license_status=status, warnings=parsed.warnings)
            if status != "open" and not allow_license:
                results.append({**item, "status": "refused",
                                "reason": f"许可证不允许自动安装（{ident or '无许可证'}，{status}）"})
                continue
            report = scan_skill(skill_dir)
            item["scan"] = report.summary()
            if report.risk == "high" and not force:
                results.append({**item, "status": "refused", "reason": "安全扫描发现高风险问题",
                                "findings": [f.model_dump() for f in report.findings if f.severity == "high"][:10]})
                continue
            if dry_run:
                results.append({**item, "status": "ok", "description": parsed.meta.description})
                continue
            dest = target_dir / name
            self._copy(skill_dir, dest)
            entry = LockEntry(
                name=name, source=spec, kind=source.kind, repo=source.repo_id or source.url or str(source.path),
                ref=source.ref, commit=fetched.commit,
                subpath=skill_dir.relative_to(fetched.root).as_posix() if source.kind != "local" else str(skill_dir),
                digest=dir_digest(dest), license=ident, license_status=status, tags=list(tags or []),
                scan=report.summary(), trust="unreviewed", installed_at=time.time(),
            )
            lock.put(entry)
            results.append({**item, "status": "installed", "commit": fetched.commit, "risk": report.risk,
                            "description": parsed.meta.description})
        if wanted and not any(r.get("name") in wanted for r in results):
            raise RuntimeError(f"仓库中没有找到这些技能: {', '.join(sorted(wanted))}")
        return results

    @staticmethod
    def _copy(src: Path, dest: Path) -> None:
        if dest.exists():
            shutil.rmtree(dest)
        root = src.resolve()
        for path in src.rglob("*"):
            rel = path.relative_to(src)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if path.is_symlink():
                target = path.resolve()
                if root not in target.parents:
                    continue
            target_path = dest / rel
            if path.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            elif path.is_file() and path.stat().st_size <= MAX_COPY_FILE:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target_path)
        dest.mkdir(parents=True, exist_ok=True)

    async def check_update(self, name: str, *, target_dir: Path, lock: LockFile) -> dict[str, Any]:
        entry = lock.load().get(name)
        if entry is None:
            raise KeyError(name)
        source = parse_source(entry.source)
        try:
            fetched = await self.fetch(source.model_copy(update={"ref": entry.ref}), [name])
        except RuntimeError as exc:
            if "没有找到" in str(exc):
                return {"name": name, "available": False, "reason": "上游已找不到这个技能"}
            raise
        candidates = [d for d in self.discover(fetched.root, source.subpath) if
                      (find_skill_file(d) is not None and (d.name == name or load_skill(d).meta.name == name))]
        if not candidates:
            return {"name": name, "available": False, "reason": "上游已找不到这个技能"}
        new_dir = candidates[0]
        current = target_dir / name
        changed = _changed_files(current, new_dir)
        return {"name": name, "available": bool(changed), "commit": fetched.commit, "old_commit": entry.commit,
                "changed": changed, "scan": scan_skill(new_dir).summary()}

    def remove(self, name: str, *, target_dir: Path, lock: LockFile) -> bool:
        dest = target_dir / name
        existed = dest.exists()
        if existed:
            shutil.rmtree(dest)
        lock.remove(name)
        return existed


def _changed_files(old: Path, new: Path) -> list[str]:
    def listing(d: Path) -> dict[str, bytes]:
        if not d.exists():
            return {}
        return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()
                and not any(part in SKIP_DIRS for part in p.relative_to(d).parts)}

    a, b = listing(old), listing(new)
    return sorted({k for k in a.keys() | b.keys() if a.get(k) != b.get(k)})
