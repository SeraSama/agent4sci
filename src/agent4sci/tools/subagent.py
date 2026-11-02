"""``delegate``: run a sub-agent with a fresh context and get back its report.

The actual construction is provided by the harness as ``ctx.services["spawn_agent"]``,
an async callable ``(task, role, tools, max_turns, ctx) -> AgentResult``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import ToolContext, ToolError, ToolResult, tool


class DelegateArgs(BaseModel):
    task: str = Field(description="Self-contained instructions: goal, inputs (file paths), expected output")
    role: str | None = Field(None, description="Optional role, e.g. 'librarian', 'analyst', 'reviewer'")
    tools: list[str] | None = Field(None, description="Restrict the sub-agent to these tool names")
    max_turns: int = Field(30, ge=1, le=200)


@tool("delegate", category="agent", parallel_safe=True, timeout_s=7200)
async def delegate(args: DelegateArgs, ctx: ToolContext):
    """Hand a well-defined sub-task to a sub-agent with its own fresh context (useful for reading many
    papers in parallel or isolated analyses). Several delegate calls in one turn run concurrently.
    The sub-agent shares the workspace and returns only its final report."""
    spawn = ctx.services.get("spawn_agent")
    if spawn is None:
        raise ToolError("当前环境不支持派生子代理。")
    result = await spawn(args.task, args.role, args.tools, args.max_turns, ctx)
    header = f"[子代理 {args.role or 'worker'}：{result.status}，{result.turns} 轮，${result.cost_usd:.4f}]"
    if result.status not in ("done",):
        return ToolResult.error(f"{header}\n{result.error or result.text or '子代理没有完成任务'}")
    return f"{header}\n{result.text}"


AGENT_TOOLS = [delegate]
