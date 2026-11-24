"""Tools that let a project's main conversation run the *other* conversations in that project.

Unlike ``delegate`` (a throwaway sub-agent nobody sees), these open real conversations: they show up in
the sidebar, the researcher can read them, keep talking in them, or take one over entirely.

The work is done by ``ctx.services["project_desk"]``, which the conversation manager installs for the
main conversation only — so a spawned conversation cannot spawn more.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import ToolContext, ToolError, ToolResult, tool


def _desk(ctx: ToolContext):
    desk = ctx.services.get("project_desk")
    if desk is None:
        raise ToolError("只有项目主对话能开启和管理项目内的其他对话。")
    return desk


class NoArgs(BaseModel):
    pass


class StartArgs(BaseModel):
    title: str = Field(description="对话标题，研究者在侧边栏看到的就是这个，写清楚做什么")
    task: str = Field(description="完整的任务说明：目标、输入（文件路径）、要交付什么。对方看不到本对话的内容")
    harness: str | None = Field(None, description="用哪个 Harness（如 librarian / analyst / writer），默认和本对话一样")
    wait: bool = Field(True, description="true=等它做完并返回报告；false=立刻返回，之后用 read_conversation 去收")
    timeout_s: float = Field(1800, ge=10, le=86400, description="wait=true 时最多等多久；超时不会停掉它")


@tool("start_conversation", category="agent", parallel_safe=True, timeout_s=86400)
async def start_conversation(args: StartArgs, ctx: ToolContext):
    """在本项目里开一个新对话并把活派给它。新对话是真实对话：会出现在侧边栏，研究者能进去看、接着聊或接手。
    几件互不依赖的事可以先用 wait=false 全派出去，再逐个去收。"""
    return await _desk(ctx).start(args.title, args.task, args.harness, args.wait, args.timeout_s)


@tool("list_conversations", category="agent", parallel_safe=True)
async def list_conversations(args: NoArgs, ctx: ToolContext):
    """列出本项目里的对话：谁在跑、谁停了、花了多少、最后动静是什么时候。"""
    return _desk(ctx).listing()


class ReadArgs(BaseModel):
    conversation_id: str
    last: int = Field(6, ge=1, le=60, description="读最后几条消息")


@tool("read_conversation", category="agent", parallel_safe=True)
async def read_conversation(args: ReadArgs, ctx: ToolContext):
    """读某个对话最近的内容（研究者在里面说的话也在这儿）。"""
    return _desk(ctx).read(args.conversation_id, args.last)


class SendArgs(BaseModel):
    conversation_id: str
    text: str = Field(description="要补充的要求、纠偏，或者新的一轮任务")
    wait: bool = Field(True, description="true=等它这一轮做完再返回")
    timeout_s: float = Field(1800, ge=10, le=86400)


@tool("send_to_conversation", category="agent", parallel_safe=True, timeout_s=86400)
async def send_to_conversation(args: SendArgs, ctx: ToolContext):
    """给本项目里某个对话再派一轮活，或者纠偏。它正在跑的时候，按那个对话自己的「插入/排队」设置处理。"""
    return await _desk(ctx).send(args.conversation_id, args.text, args.wait, args.timeout_s)


class StopArgs(BaseModel):
    conversation_id: str


@tool("stop_conversation", category="agent", parallel_safe=True)
async def stop_conversation(args: StopArgs, ctx: ToolContext):
    """停掉一个跑偏或者没必要再跑的对话。对话本身留着，研究者还能进去看。"""
    if not _desk(ctx).stop(args.conversation_id):
        return ToolResult.error("它本来就没在跑。")
    return "已停止。"


PROJECT_AGENT_TOOLS = [start_conversation, list_conversations, read_conversation, send_to_conversation,
                       stop_conversation]
