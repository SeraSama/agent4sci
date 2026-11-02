"""File tools confined to the workspace (skill directories are readable too)."""

from __future__ import annotations

import base64
import fnmatch
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.types import ImageBlock, TextBlock
from ..core.textio import decode_text, looks_binary, text_encoding
from .base import ToolContext, ToolError, ToolResult, tool

IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ipynb_checkpoints"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
MAX_READ_BYTES = 5_000_000
MAX_IMAGE_BYTES = 5_000_000


def _is_binary(sample: bytes) -> bool:
    return looks_binary(sample)


def _pdf_text(path: Path, max_pages: int = 30) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages[:max_pages]):
        pages.append(f"--- 第 {i + 1} 页 ---\n{page.extract_text() or ''}")
    more = len(reader.pages) - max_pages
    if more > 0:
        pages.append(f"…（还有 {more} 页未显示）")
    return "\n".join(pages)


class ReadArgs(BaseModel):
    path: str = Field(description="File path (relative to the workspace or absolute inside allowed roots)")
    start_line: int = Field(1, ge=1, description="First line to read (1-based)")
    max_lines: int = Field(2000, ge=1, le=20000, description="Maximum number of lines to return")


@tool("read_file", category="fs.read", parallel_safe=True)
async def read_file(args: ReadArgs, ctx: ToolContext):
    """Read a text file with line numbers. Images (png/jpg/gif/webp) are returned as images you can see;
    PDFs are converted to text. Use start_line/max_lines to page through long files."""
    path = ctx.resolve(args.path)
    if not path.exists():
        raise ToolError(f"文件不存在: {args.path}")
    if path.is_dir():
        raise ToolError(f"这是目录，请用 list_files: {args.path}")
    size = path.stat().st_size
    suffix = path.suffix.lower()
    if suffix in IMAGE_TYPES:
        if size > MAX_IMAGE_BYTES:
            raise ToolError(f"图片过大（{size} 字节）")
        data = base64.standard_b64encode(path.read_bytes()).decode()
        return ToolResult(content=[TextBlock(text=f"图片 {ctx.display_path(path)}（{size} 字节）"),
                                   ImageBlock(media_type=IMAGE_TYPES[suffix], data=data, path=str(path))])
    if suffix == ".pdf":
        return _pdf_text(path)
    if size > MAX_READ_BYTES:
        raise ToolError(f"文件过大（{size} 字节），请用 grep 或 shell 工具处理")
    raw = path.read_bytes()
    if _is_binary(raw):
        return f"二进制文件 {ctx.display_path(path)}（{size} 字节），无法以文本显示。"
    lines = decode_text(raw).splitlines()
    start = args.start_line - 1
    chunk = lines[start : start + args.max_lines]
    body = "\n".join(f"{start + i + 1:6}\t{line}" for i, line in enumerate(chunk))
    total = len(lines)
    footer = ""
    if start + len(chunk) < total:
        footer = f"\n…（共 {total} 行，已显示 {start + 1}-{start + len(chunk)} 行；用 start_line 继续读取）"
    return (body or "(空文件)") + footer


class WriteArgs(BaseModel):
    path: str
    content: str = Field(description="Full new file content")


@tool("write_file", category="fs.write")
async def write_file(args: WriteArgs, ctx: ToolContext):
    """Create or overwrite a text file in the workspace (parent directories are created)."""
    path = ctx.resolve(args.path, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(args.content, encoding="utf-8")
    ctx.emit("file.changed", path=ctx.display_path(path), action="modified" if existed else "created")
    return f"已{'覆盖' if existed else '创建'} {ctx.display_path(path)}（{len(args.content)} 个字符）"


class EditArgs(BaseModel):
    path: str
    old_string: str = Field(description="Exact text to replace (must match the file exactly, including whitespace)")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(False, description="Replace every occurrence instead of requiring a unique match")


@tool("edit_file", category="fs.write")
async def edit_file(args: EditArgs, ctx: ToolContext):
    """Replace an exact string in a file. The old string must be unique unless replace_all is true."""
    path = ctx.resolve(args.path, write=True)
    if not path.exists():
        raise ToolError(f"文件不存在: {args.path}")
    raw = path.read_bytes()
    encoding = text_encoding(raw) or "utf-8"  # keep a BOM / UTF-16 file in its own encoding
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        raise ToolError("文件不是 UTF-8 / UTF-16 文本，无法安全地逐字替换；请用 write_file 重写或用 shell 转码。") from None
    count = text.count(args.old_string)
    if not args.old_string or count == 0:
        raise ToolError("没有找到 old_string（需要与文件内容逐字一致，包括缩进）。先用 read_file 查看文件。")
    if count > 1 and not args.replace_all:
        raise ToolError(f"old_string 出现了 {count} 次，请提供更多上下文使其唯一，或设置 replace_all。")
    new_text = text.replace(args.old_string, args.new_string) if args.replace_all else text.replace(
        args.old_string, args.new_string, 1)
    path.write_bytes(new_text.encode(encoding))
    ctx.emit("file.changed", path=ctx.display_path(path), action="modified")
    return f"已修改 {ctx.display_path(path)}（替换 {count if args.replace_all else 1} 处）"


class ListArgs(BaseModel):
    path: str = Field(".", description="Directory to list")
    pattern: str | None = Field(None, description="Optional glob filter on file names, e.g. *.py")
    max_entries: int = Field(300, ge=1, le=5000)


@tool("list_files", category="fs.read", parallel_safe=True)
async def list_files(args: ListArgs, ctx: ToolContext):
    """List files under a directory recursively (skips .git, .venv, node_modules and caches)."""
    root = ctx.resolve(args.path)
    if not root.exists():
        raise ToolError(f"目录不存在: {args.path}")
    if root.is_file():
        return ctx.display_path(root)
    entries: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not (d == "cache" and ".a4s" in dirpath))
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(filenames):
            if args.pattern and not fnmatch.fnmatch(name, args.pattern):
                continue
            full = Path(dirpath) / name
            try:
                size = full.stat().st_size
            except OSError:
                continue
            entries.append(f"{(rel_dir / name).as_posix()}  ({size} B)")
            if len(entries) >= args.max_entries:
                truncated = True
                break
        if truncated:
            break
    if not entries:
        return "（没有文件）"
    return "\n".join(entries) + ("\n…（结果已截断）" if truncated else "")


class GrepArgs(BaseModel):
    pattern: str = Field(description="Python regular expression")
    path: str = Field(".", description="File or directory to search")
    glob: str | None = Field(None, description="Only search files matching this glob, e.g. *.md")
    ignore_case: bool = False
    max_results: int = Field(200, ge=1, le=2000)


@tool("grep", category="fs.read", parallel_safe=True)
async def grep(args: GrepArgs, ctx: ToolContext):
    """Search file contents with a regular expression; returns path:line: text."""
    try:
        regex = re.compile(args.pattern, re.IGNORECASE if args.ignore_case else 0)
    except re.error as exc:
        raise ToolError(f"正则表达式无效: {exc}") from exc
    root = ctx.resolve(args.path)
    files = [root] if root.is_file() else []
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            for name in filenames:
                if args.glob and not fnmatch.fnmatch(name, args.glob):
                    continue
                files.append(Path(dirpath) / name)
    hits: list[str] = []
    for file in sorted(files):
        try:
            if file.stat().st_size > MAX_READ_BYTES:
                continue
            raw = file.read_bytes()
        except OSError:
            continue
        if _is_binary(raw):
            continue
        for lineno, line in enumerate(decode_text(raw).splitlines(), 1):
            if regex.search(line):
                hits.append(f"{ctx.display_path(file)}:{lineno}: {line.strip()[:300]}")
                if len(hits) >= args.max_results:
                    return "\n".join(hits) + "\n…（结果已截断）"
    return "\n".join(hits) if hits else "没有匹配。"


FS_TOOLS = [read_file, write_file, edit_file, list_files, grep]
