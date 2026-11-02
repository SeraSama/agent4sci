"""Tool abstraction shared by built-in tools, skills tools and MCP tools."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from ..core.events import Emitter
from ..llm.types import ImageBlock, TextBlock, ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from ..agent.interactions import InteractionBroker


class ToolError(Exception):
    """An error whose message is meant for the model (returned as an error result)."""


class PathAccessError(ToolError):
    pass


class ToolInputError(ToolError):
    pass


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass
class ToolContext:
    """Everything a tool may touch during one agent run."""

    workspace: Path
    readonly_roots: list[Path] = field(default_factory=list)
    emit: Emitter = field(default_factory=Emitter.null)
    interactions: "InteractionBroker | None" = None
    services: dict[str, Any] = field(default_factory=dict)
    env_passthrough: list[str] = field(default_factory=list)
    auto_mode: bool = False
    permission_mode: str = "ask_risky"  # ask_all | ask_risky | allow_all | custom (see agent/permissions.py)
    conversation_id: str | None = None
    run_id: str | None = None
    project_id: str | None = None
    topics: list[str] = field(default_factory=list)
    protected_write_dirs: tuple[str, ...] = (".a4s", ".git")

    @property
    def outputs_dir(self) -> Path:
        path = self.workspace / ".a4s" / "outputs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def service(self, name: str) -> Any:
        if name not in self.services:
            raise ToolError(f"服务不可用: {name}")
        return self.services[name]

    def resolve(self, path: str, *, write: bool = False) -> Path:
        """Resolve a user/model supplied path, refusing anything outside the allowed roots.

        ``Path.resolve`` follows symlinks, so a link that points outside the
        workspace is rejected as well.
        """
        raw = Path(str(path)).expanduser()
        candidate = raw if raw.is_absolute() else self.workspace / raw
        resolved = candidate.resolve()
        root = self.workspace.resolve()
        if _is_within(resolved, root):
            if write:
                rel = resolved.relative_to(root)
                if rel.parts and rel.parts[0] in self.protected_write_dirs:
                    raise PathAccessError(f"不允许写入内部目录: {path}")
            return resolved
        if not write and any(_is_within(resolved, r.resolve()) for r in self.readonly_roots):
            return resolved
        raise PathAccessError(f"路径不在允许的工作区范围内: {path}")

    def display_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.workspace.resolve()).as_posix()
        except ValueError:
            return str(path)


class ToolResult(BaseModel):
    content: list[TextBlock | ImageBlock]
    is_error: bool = False
    meta: dict[str, Any] = {}

    @classmethod
    def text(cls, text: str, **meta: Any) -> "ToolResult":
        return cls(content=[TextBlock(text=text)], meta=meta)

    @classmethod
    def error(cls, text: str, **meta: Any) -> "ToolResult":
        return cls(content=[TextBlock(text=text)], is_error=True, meta=meta)

    @classmethod
    def coerce(cls, value: Any) -> "ToolResult":
        if isinstance(value, ToolResult):
            return value
        if value is None:
            return cls.text("(ok)")
        if isinstance(value, str):
            return cls.text(value)
        return cls.text(json.dumps(value, ensure_ascii=False, indent=2, default=str))

    def as_text(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))


def clean_schema(schema: Any) -> Any:
    """Drop pydantic's auto-generated ``title`` keys (saves tokens, keeps property names)."""
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key == "title" and isinstance(value, str):
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: clean_schema(sub) for name, sub in value.items()}
            else:
                out[key] = clean_schema(value)
        return out
    if isinstance(schema, list):
        return [clean_schema(v) for v in schema]
    return schema


EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


class Tool:
    name: str = ""
    description: str = ""
    category: str = "misc"
    parallel_safe: bool = False
    timeout_s: float | None = None
    Args: type[BaseModel] | None = None
    native: dict[str, dict[str, Any]] = {}

    def input_schema(self) -> dict[str, Any]:
        if self.Args is None:
            return dict(EMPTY_SCHEMA)
        return clean_schema(self.Args.model_json_schema())

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, input_schema=self.input_schema(),
                        native=dict(self.native))

    def parse_args(self, data: dict[str, Any]) -> Any:
        if self.Args is None:
            return data
        try:
            return self.Args.model_validate(data)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in exc.errors()
            )
            raise ToolInputError(f"参数校验失败: {problems}") from exc

    async def run(self, args: Any, ctx: ToolContext) -> ToolResult:  # pragma: no cover - interface
        raise NotImplementedError

    def preview(self, args: Any) -> str:
        data = args.model_dump() if isinstance(args, BaseModel) else args
        text = json.dumps(data, ensure_ascii=False, default=str)
        return text if len(text) < 400 else text[:400] + "…"


class FunctionTool(Tool):
    def __init__(
        self,
        fn: Callable[..., Awaitable[Any]],
        name: str,
        description: str,
        args_model: type[BaseModel] | None,
        category: str,
        parallel_safe: bool,
        timeout_s: float | None = None,
        native: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.fn = fn
        self.name = name
        self.description = description
        self.Args = args_model
        self.category = category
        self.parallel_safe = parallel_safe
        self.timeout_s = timeout_s
        self.native = native or {}

    async def run(self, args: Any, ctx: ToolContext) -> ToolResult:
        return ToolResult.coerce(await self.fn(args, ctx))


def tool(
    name: str | None = None,
    description: str | None = None,
    *,
    category: str = "misc",
    parallel_safe: bool = False,
    timeout_s: float | None = None,
    native: dict[str, dict[str, Any]] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], FunctionTool]:
    """Turn ``async def fn(args: ArgsModel, ctx: ToolContext)`` into a :class:`Tool`."""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> FunctionTool:
        params = list(inspect.signature(fn).parameters.values())
        hints = inspect.get_annotations(fn, eval_str=True)
        args_model = hints.get(params[0].name) if params else None
        if args_model is not None and not (inspect.isclass(args_model) and issubclass(args_model, BaseModel)):
            args_model = None
        desc = description or inspect.cleandoc(fn.__doc__ or "")
        return FunctionTool(fn, name or fn.__name__, desc, args_model, category, parallel_safe, timeout_s, native)

    return decorator
