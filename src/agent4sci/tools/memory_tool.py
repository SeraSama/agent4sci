"""The ``memory`` tool: a scoped ``/memories`` directory.

It speaks the same command format as Anthropic's native ``memory_20250818`` tool
(view/create/str_replace/insert/delete/rename), so Claude models get the native
declaration while other models see an equivalent JSON schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..memory.store import INDEX_FILE, MemoryAccess, looks_like_injection, split_frontmatter
from .base import Tool, ToolContext, ToolError, ToolResult


class MemoryArgs(BaseModel):
    command: Literal["view", "create", "str_replace", "insert", "delete", "rename"]
    path: str | None = Field(None, description="Path under /memories, e.g. /memories/project/dataset-notes.md")
    view_range: list[int] | None = Field(None, description="[start, end] lines for view")
    file_text: str | None = Field(None, description="Content for create")
    old_str: str | None = None
    new_str: str | None = None
    insert_line: int | None = Field(None, description="Insert after this line (0 = top)")
    insert_text: str | None = None
    old_path: str | None = None
    new_path: str | None = None


class MemoryTool(Tool):
    name = "memory"
    category = "memory"
    parallel_safe = False
    Args = MemoryArgs
    description = (
        "Persistent memory under /memories (one sub-directory per allowed scope). Commands: view (a directory "
        "or a file), create (write a file), str_replace, insert, delete, rename. Keep one topic per file and "
        "start each file with a one-line summary."
    )
    native = {"anthropic": {"type": "memory_20250818", "name": "memory"}}

    def _access(self, ctx: ToolContext) -> MemoryAccess:
        access = ctx.services.get("memory")
        if access is None or not access.enabled:
            raise ToolError("当前对话没有启用记忆。")
        return access

    async def run(self, args: MemoryArgs, ctx: ToolContext) -> ToolResult:
        access = self._access(ctx)
        cmd = args.command
        try:
            if cmd == "view":
                return ToolResult.text(self._view(access, args.path or "/memories", args.view_range))
            if cmd == "rename":
                if not args.old_path or not args.new_path:
                    raise ToolError("rename 需要 old_path 和 new_path")
                src_store, src = access.resolve_virtual(args.old_path)
                dst_store, dst = access.resolve_virtual(args.new_path)
                if src_store is None or dst_store is None or not src.exists():
                    raise ToolError("源文件不存在或路径无效")
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
                src_store.rebuild_index()
                dst_store.rebuild_index()
                self._event(ctx, "rename", args.new_path)
                return ToolResult.text(f"已重命名为 {args.new_path}")
            if not args.path:
                raise ToolError("需要 path")
            store, target = access.resolve_virtual(args.path)
            if store is None or target == store.root.resolve():
                raise ToolError("请指定具体文件，例如 /memories/project/notes.md")
            if target.name == INDEX_FILE:
                raise ToolError("MEMORY.md 是自动维护的索引，不能直接修改。")
            if target.suffix != ".md":
                target = target.with_suffix(".md")
            if cmd == "create":
                text = args.file_text or ""
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            elif cmd == "str_replace":
                if not target.exists():
                    raise ToolError("文件不存在")
                text = target.read_text(encoding="utf-8")
                count = text.count(args.old_str or "")
                if not args.old_str or count != 1:
                    raise ToolError(f"old_str 必须恰好出现一次（实际 {count} 次）")
                target.write_text(text.replace(args.old_str, args.new_str or "", 1), encoding="utf-8")
            elif cmd == "insert":
                if not target.exists():
                    raise ToolError("文件不存在")
                lines = target.read_text(encoding="utf-8").splitlines()
                at = max(0, min(args.insert_line or 0, len(lines)))
                lines[at:at] = (args.insert_text or "").splitlines()
                target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            elif cmd == "delete":
                if not target.exists():
                    raise ToolError("文件不存在")
                target.unlink()
            store.rebuild_index()
            self._event(ctx, cmd, args.path)
            warning = ""
            if cmd != "delete" and target.exists():
                flag = looks_like_injection(target.read_text(encoding="utf-8"))
                if flag:
                    ctx.emit("memory.flagged", path=args.path, reason=flag)
                    warning = f"\n⚠ 注意：内容{flag}，已标记供用户检查。"
            return ToolResult.text(f"{cmd} 完成: {args.path}{warning}")
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    def _view(self, access: MemoryAccess, path: str, view_range: list[int] | None) -> str:
        store, target = access.resolve_virtual(path)
        if store is None:
            lines = ["/memories"]
            for scope, st in access.stores.items():
                count = len(st.files())
                lines.append(f"  {scope}/  （{count} 条记忆）")
            return "\n".join(lines)
        if target.is_dir() or target == store.root.resolve():
            if not target.exists():
                return f"/memories/{store.scope}/ （空）"
            entries = [p for p in sorted(target.rglob("*")) if p.is_file()]
            rel = [p.relative_to(store.root.resolve()).as_posix() for p in entries]
            return "\n".join(f"/memories/{store.scope}/{r}" for r in rel) or "（空）"
        if not target.exists():
            raise ToolError(f"不存在: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start, end = 1, len(lines)
        if view_range and len(view_range) == 2:
            start = max(1, view_range[0])
            end = len(lines) if view_range[1] == -1 else min(len(lines), view_range[1])
        return "\n".join(f"{i:6}\t{lines[i - 1]}" for i in range(start, end + 1)) or "（空文件）"

    def _event(self, ctx: ToolContext, command: str, path: str | None) -> None:
        ctx.emit("memory.write", command=command, path=path)


def memory_tool() -> MemoryTool:
    return MemoryTool()


def memory_summary(access: MemoryAccess) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for scope, store in access.stores.items():
        out[scope] = [item.model_dump(exclude={"body"}) for item in store.list()]
    return out


__all__ = ["MemoryTool", "memory_tool", "memory_summary", "split_frontmatter"]
