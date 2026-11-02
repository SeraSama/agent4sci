"""Back up and restore the application data: the database, memories, skills and field packs.

Projects live in their own folders (and have their own git history), so they are not copied here — this is
about everything the app itself keeps: conversations, runs, model profiles, harnesses, loops, memories.

API keys are deliberately NOT included: they live in the OS keychain, and a backup file that carried them
would be a plaintext credential store. A restored install therefore has models that look configured but
have no key, which is why `missing_keys` is reported back to the user.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any

from .paths import app_data_dir, db_path

MANIFEST = "agent4sci-backup.json"
FORMAT = 1
COPIED_DIRS = ("memory", "skills", "domains", "conversations")
SKIPPED = {"tmp", "cache", "logs", "backups"}


def backup_dir() -> Path:
    path = app_data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _snapshot_db(target: Path) -> None:
    """A consistent copy even while the app is writing (WAL): let SQLite do the copying."""
    source = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def _model_keys(data_dir: Path) -> list[str]:
    """Which model profiles claim to have a key — those are the ones to re-enter after a restore."""
    try:
        con = sqlite3.connect(f"file:{data_dir / 'app.db'}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute("select data from docs where kind='model'").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    names = []
    for (raw,) in rows:
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if item.get("has_key") or item.get("api_key_ref"):
            names.append(item.get("name") or item.get("id"))
    return names


def create_backup(destination: Path | None = None) -> dict[str, Any]:
    data_dir = app_data_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(destination) if destination else backup_dir() / f"agent4sci-{stamp}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)

    staged_db = data_dir / "tmp" / f"backup-{stamp}.db"
    staged_db.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_db(staged_db)
    files = 0
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST, json.dumps({
                "format": FORMAT, "created": time.time(), "data_dir": str(data_dir),
                "models_needing_keys": _model_keys(data_dir),
            }, ensure_ascii=False, indent=2))
            zf.write(staged_db, "app.db")
            files += 1
            for name in COPIED_DIRS:
                root = data_dir / name
                if not root.is_dir():
                    continue
                for path in root.rglob("*"):
                    if path.is_file() and not any(part in SKIPPED for part in path.relative_to(data_dir).parts):
                        zf.write(path, path.relative_to(data_dir).as_posix())
                        files += 1
    finally:
        staged_db.unlink(missing_ok=True)
    return {"path": str(target), "size": target.stat().st_size, "files": files,
            "models_needing_keys": _model_keys(data_dir)}


def inspect_backup(archive: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive) as zf:
        if MANIFEST not in zf.namelist():
            raise ValueError("这不是 Agent4Sci 的备份文件（缺少 manifest）")
        manifest = json.loads(zf.read(MANIFEST))
        if int(manifest.get("format", 0)) > FORMAT:
            raise ValueError("备份文件来自更新版本的 Agent4Sci，请先升级再恢复")
        return {**manifest, "entries": len(zf.namelist()), "path": str(archive)}


def restore_backup(archive: Path) -> dict[str, Any]:
    """Replace the current data with the archive's, after backing up what is there now."""
    info = inspect_backup(Path(archive))
    data_dir = app_data_dir()
    safety = create_backup(backup_dir() / f"before-restore-{time.strftime('%Y%m%d-%H%M%S')}.zip")

    staging = data_dir / "tmp" / f"restore-{int(time.time())}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                if name == MANIFEST or name.startswith(("/", "..")) or ".." in Path(name).parts:
                    continue  # never let an archive write outside the data directory
                zf.extract(name, staging)
        for name in COPIED_DIRS:
            incoming = staging / name
            if incoming.is_dir():
                shutil.rmtree(data_dir / name, ignore_errors=True)
                shutil.move(str(incoming), str(data_dir / name))
        incoming_db = staging / "app.db"
        if incoming_db.exists():
            for suffix in ("-wal", "-shm"):
                Path(str(db_path()) + suffix).unlink(missing_ok=True)
            shutil.move(str(incoming_db), str(db_path()))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"restored": info, "safety_backup": safety["path"],
            "models_needing_keys": info.get("models_needing_keys", []), "restart_required": True}


def data_dir_size() -> dict[str, Any]:
    data_dir = app_data_dir()
    sizes: dict[str, int] = {}
    for child in data_dir.iterdir():
        if child.is_dir():
            sizes[child.name] = sum(p.stat().st_size for p in child.rglob("*") if p.is_file())
        elif child.is_file():
            sizes[child.name] = child.stat().st_size
    return {"total": sum(sizes.values()), "parts": dict(sorted(sizes.items(), key=lambda kv: -kv[1])[:8]),
            "path": str(data_dir)}
