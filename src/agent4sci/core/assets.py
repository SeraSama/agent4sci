"""User-imported background images and videos, kept in ``<data>/assets``.

Only the desktop UI uses these: the renderer points ``<img>`` / ``<video>`` at ``/api/assets/{id}/raw``
(the token can ride in the query string, which is the only reason a raw-bytes endpoint works at all).
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from .paths import app_data_dir

Kind = Literal["image", "video"]

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".avif": "image/avif", ".bmp": "image/bmp"}
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".m4v": "video/mp4", ".mov": "video/quicktime",
               ".ogv": "video/ogg"}
CONTENT_TYPES = {**IMAGE_TYPES, **VIDEO_TYPES}

MAX_BYTES: dict[Kind, int] = {"image": 40 * 1024 * 1024, "video": 400 * 1024 * 1024}
# Above this a looping background video noticeably costs battery and GPU on a laptop; the UI warns.
HEAVY_VIDEO_BYTES = 50 * 1024 * 1024


def assets_dir() -> Path:
    path = app_data_dir() / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def kind_of(suffix: str) -> Kind | None:
    low = suffix.lower()
    return "image" if low in IMAGE_TYPES else "video" if low in VIDEO_TYPES else None


def content_type(asset_id: str) -> str:
    return CONTENT_TYPES.get(Path(asset_id).suffix.lower(), "application/octet-stream")


def _index_file() -> Path:
    return assets_dir() / "index.json"


def _load_index() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_index_file().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(index: dict[str, dict[str, Any]]) -> None:
    _index_file().write_text(json.dumps(index, ensure_ascii=False, indent=2), "utf-8")


def asset_path(asset_id: str) -> Path | None:
    """Resolve an id to a file inside the assets folder, or None if it is not one of ours."""
    name = Path(asset_id).name
    if name != asset_id or kind_of(Path(name).suffix) is None:
        return None
    path = assets_dir() / name
    return path if path.is_file() else None


def list_assets() -> list[dict[str, Any]]:
    index = _load_index()
    out = []
    for path in sorted(assets_dir().iterdir()):
        if not path.is_file() or kind_of(path.suffix) is None:
            continue
        meta = index.get(path.name, {})
        stat = path.stat()
        out.append({"id": path.name, "name": meta.get("name") or path.name, "kind": kind_of(path.suffix),
                    "content_type": content_type(path.name), "size": stat.st_size,
                    "created": meta.get("created") or stat.st_mtime})
    return sorted(out, key=lambda a: a["created"], reverse=True)


def import_asset(source: Path) -> dict[str, Any]:
    source = source.expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"找不到文件：{source}")
    kind = kind_of(source.suffix)
    if kind is None:
        raise ValueError(f"不支持的文件类型 {source.suffix or '(无扩展名)'}；"
                         f"图片支持 {', '.join(sorted(IMAGE_TYPES))}，视频支持 {', '.join(sorted(VIDEO_TYPES))}")
    size = source.stat().st_size
    if size > MAX_BYTES[kind]:
        raise ValueError(f"文件太大（{size / 1024 / 1024:.0f} MB）；"
                         f"{'图片' if kind == 'image' else '视频'}上限 {MAX_BYTES[kind] // 1024 // 1024} MB")
    asset_id = f"{uuid.uuid4().hex[:12]}{source.suffix.lower()}"
    shutil.copy2(source, assets_dir() / asset_id)
    index = _load_index()
    index[asset_id] = {"name": source.name, "created": time.time()}
    _save_index(index)
    return {"id": asset_id, "name": source.name, "kind": kind, "content_type": content_type(asset_id),
            "size": size, "created": index[asset_id]["created"], "heavy": kind == "video" and size > HEAVY_VIDEO_BYTES}


def delete_asset(asset_id: str) -> bool:
    path = asset_path(asset_id)
    if path is None:
        return False
    path.unlink()
    index = _load_index()
    if index.pop(path.name, None) is not None:
        _save_index(index)
    return True
