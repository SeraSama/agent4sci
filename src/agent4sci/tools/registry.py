from __future__ import annotations

from typing import Iterable, Iterator

from ..llm.types import ToolSpec
from .base import Tool


class ToolRegistry:
    """Ordered set of tools. Order is kept stable so the tool list stays prompt-cacheable."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.add(t)

    def add(self, tool: Tool, *, replace: bool = False) -> None:
        if tool.name in self._tools and not replace:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def extend(self, tools: Iterable[Tool], *, replace: bool = False) -> None:
        for t in tools:
            self.add(t, replace=replace)

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def categories(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for t in self._tools.values():
            out.setdefault(t.category, []).append(t.name)
        return out

    def subset(
        self,
        names: Iterable[str] | None = None,
        categories: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
    ) -> "ToolRegistry":
        wanted_names = set(names) if names is not None else None
        wanted_cats = set(categories) if categories is not None else None
        excluded = set(exclude)
        picked = []
        for t in self._tools.values():
            if t.name in excluded:
                continue
            if wanted_names is None and wanted_cats is None:
                picked.append(t)
            elif (wanted_names and t.name in wanted_names) or (
                wanted_cats and any(t.category == c or t.category.startswith(c + ".") for c in wanted_cats)
            ):
                picked.append(t)
        return ToolRegistry(picked)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)
