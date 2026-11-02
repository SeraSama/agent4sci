"""Put the uv binary next to the packaged backend (backend/bin/uv[.exe]).

The desktop app uses it to create project virtual environments, run skill scripts (``uv run``) and start
Python MCP servers (``uvx``), so users do not need Python installed.  The archive's published sha256 is checked.

    uv run python packaging/fetch_uv.py [--target auto|mac-arm64|mac-x64|win-x64] [--dest DIR] [--version X.Y.Z]

Offline builds: set A4S_UV_BINARY=/path/to/uv (and optionally A4S_UVX_BINARY) to copy local binaries instead.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "mac-arm64": ("aarch64-apple-darwin", ".tar.gz"),
    "mac-x64": ("x86_64-apple-darwin", ".tar.gz"),
    "win-x64": ("x86_64-pc-windows-msvc", ".zip"),
    "linux-x64": ("x86_64-unknown-linux-gnu", ".tar.gz"),
}


def host_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    if sys.platform == "win32":
        return "win-x64"
    return "linux-x64"


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "agent4sci-build"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - fixed https URL
        return resp.read()


def cached_archive(cache_dir: Path, archive_name: str) -> bytes | None:
    """An archive from an earlier build, re-checked against the sha256 prefix recorded in its file name."""
    for path in sorted(cache_dir.glob(f"*-{archive_name}"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest().startswith(path.name.split("-", 1)[0]):
            return data
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="auto", choices=["auto", *TARGETS])
    parser.add_argument("--dest", default=str(ROOT / "build" / "pyinstaller" / "dist" / "backend" / "bin"))
    parser.add_argument("--version", default=os.environ.get("A4S_UV_VERSION", "latest"))
    args = parser.parse_args()

    target = host_target() if args.target == "auto" else args.target
    triple, ext = TARGETS[target]
    windows = target.startswith("win")
    names = ["uv.exe", "uvx.exe"] if windows else ["uv", "uvx"]
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    local = os.environ.get("A4S_UV_BINARY")
    if local:
        shutil.copy2(local, dest / names[0])
        if os.environ.get("A4S_UVX_BINARY"):
            shutil.copy2(os.environ["A4S_UVX_BINARY"], dest / names[1])
        print(f"copied local uv to {dest}")
        return

    base = ("https://github.com/astral-sh/uv/releases/latest/download" if args.version == "latest"
            else f"https://github.com/astral-sh/uv/releases/download/{args.version}")
    archive_name = f"uv-{triple}{ext}"
    cache_dir = ROOT / "build" / "cache"  # survives `rm -rf build/pyinstaller`
    try:
        expected = download(f"{base}/{archive_name}.sha256").decode().split()[0].lower()
    except OSError as exc:  # flaky network: an archive checked on an earlier build is better than no build
        data = cached_archive(cache_dir, archive_name)
        if data is None:
            sys.exit(f"取不到 {archive_name}.sha256，也没有可用的缓存：{exc}")
        print(f"离线：使用缓存的 {archive_name}（{exc}）", flush=True)
    else:
        cached = cache_dir / f"{expected[:16]}-{archive_name}"
        if cached.exists() and hashlib.sha256(cached.read_bytes()).hexdigest() == expected:
            print(f"using cached {cached.name}")
            data = cached.read_bytes()
        else:
            print(f"downloading {base}/{archive_name}", flush=True)
            data = download(f"{base}/{archive_name}")
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                sys.exit(f"sha256 mismatch for {archive_name}: expected {expected}, got {actual}")
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)

    found = 0
    if ext == ".zip":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                name = Path(info.filename).name
                if name in names:
                    (dest / name).write_bytes(zf.read(info))
                    found += 1
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for member in tf.getmembers():
                name = Path(member.name).name
                if member.isfile() and name in names:
                    out = dest / name
                    out.write_bytes(tf.extractfile(member).read())  # type: ignore[union-attr]
                    out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    found += 1
    if found < 1:
        sys.exit(f"no uv binary found in {archive_name}")
    print(f"uv ({target}, sha256 ok) -> {dest}")


if __name__ == "__main__":
    main()
