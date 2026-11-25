"""``update_todos``: the agent's own task list, so a long job stays on course and the researcher can see
what it intends to do before it does it.

Storage comes from ``ctx.services["todos"]`` when the caller provides one (the conversation manager keeps
it on the conversation so it survives the agent being rebuilt). Without one — a Loop node, a sub-agent —
it falls back to a list that lives as long as the run, so the tool always works and a sub-agent can never
overwrite its parent's plan.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .base import ToolContext, tool

MARKS = {"pending": "[ ]", "doing": "[→]", "done": "[x]"}


class TodoItem(BaseModel):
    text: str = Field(description="一行祈使句，说清这一步要做出什么", max_length=200)
    status: Literal["pending", "doing", "done"] = "pending"


class TodosArgs(BaseModel):
    items: list[TodoItem] = Field(description="完整的清单，整体替换上一次的（不是增量追加）", max_length=30)


class _RunTodos:
    """The fallback store: a plan that lasts as long as this run."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def read(self) -> list[dict[str, Any]]:
        return list(self.items)

    def write(self, items: list[dict[str, Any]]) -> None:
        self.items = list(items)


def todo_store(ctx: ToolContext) -> Any:
    store = ctx.services.get("todos")
    if store is None:
        store = _RunTodos()
        ctx.services["todos"] = store
    return store


def render_todos(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（清单已清空）"
    done = sum(1 for i in items if i.get("status") == "done")
    lines = [f"{MARKS.get(str(i.get('status')), '[ ]')} {i.get('text', '')}" for i in items]
    return "\n".join(lines) + f"\n\n（{done}/{len(items)} 已完成）"


@tool("update_todos", category="plan")
async def update_todos(args: TodosArgs, ctx: ToolContext):
    """维护你自己的任务清单，研究者会实时看到它。

    什么时候用：任务有三步以上、研究者一次提了好几个要求、或者你要把活分给别的对话。动手之前先把清单列出来。
    怎么用：每次状态有变化就再调一次，**传完整的清单**（这次传的会整体替换上一次的）。同一时刻只能有一个
    doing。做完一项立刻标 done，不要攒到最后一起标。计划变了就改清单，别硬撑着执行一个已经不对的计划。
    什么时候别用：一两步就能做完的小事——列清单只会碍事。"""
    items = [i.model_dump() for i in args.items]
    todo_store(ctx).write(items)
    ctx.emit("todos.changed", items=items)
    return render_todos(items)


PLAN_TOOLS = [update_todos]
