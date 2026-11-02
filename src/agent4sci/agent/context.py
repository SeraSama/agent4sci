"""Context-window helpers: output truncation, history repair and summary restarts."""

from __future__ import annotations

from pathlib import Path

from ..llm.types import Message, TextBlock, ToolResultBlock, ToolSpec, estimate_tokens, user_text

SUMMARY_KIND = "summary"
NOTICE_KIND = "notice"  # shown in the transcript (why a run stopped), never sent to the model

SUMMARY_PROMPT = (
    "The conversation is about to be compacted to free context space. Write a dense progress summary "
    "that lets you continue seamlessly: the overall goal, what has been done (with file paths created or "
    "modified), key findings and numbers with their sources, decisions and their reasons, open problems, "
    "and the concrete next steps. Do not call tools. Write in the user's language."
)


def truncate_output(text: str, limit: int, save_dir: Path | None, name: str) -> tuple[str, str | None]:
    """Keep head and tail of long tool output; save the full text to a file first."""
    if len(text) <= limit:
        return text, None
    saved: str | None = None
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"{name}.txt"
        path.write_text(text, encoding="utf-8")
        saved = str(path)
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25) :]
    note = f"\n\n…[已截断 {len(text) - len(head) - len(tail)} 个字符"
    note += f"；完整输出已保存到 {saved}，可用 read_file 分段读取]…\n\n" if saved else "]…\n\n"
    return head + note + tail, saved


def fixed_context_tokens(system: str, specs: list[ToolSpec]) -> int:
    """What every request carries before the conversation: the system prompt and the tool schemas."""
    return estimate_tokens(system) + sum(estimate_tokens(s.model_dump()) for s in specs)


def history_tokens(messages: list[Message]) -> int:
    return sum(message_tokens(m) for m in messages)


def message_tokens(message: Message) -> int:
    return estimate_tokens(message.model_dump(mode="json", exclude={"raw"}))


def context_from_history(history: list[Message]) -> list[Message]:
    """The working context starts at the most recent summary restart (if any)."""
    history = [m for m in history if m.meta.get("kind") != NOTICE_KIND]
    for i in range(len(history) - 1, -1, -1):
        if history[i].meta.get("kind") == SUMMARY_KIND:
            return history[i:]
    return list(history)


def notice_message(text: str) -> Message:
    """A line for the transcript that explains what happened, written by the app, not the model."""
    return Message(role="assistant", content=[TextBlock(text=text)], meta={"kind": NOTICE_KIND})


def repair_history(messages: list[Message]) -> list[Message]:
    """Make a possibly interrupted history valid for the APIs.

    Every tool call must be answered by a tool result in the next user message.
    If the app stopped between the two, append (never edit) a message with
    "interrupted" results so the transcript stays append-only.
    """
    if not messages:
        return messages
    last = messages[-1]
    if last.role == "assistant" and last.tool_calls():
        results = [
            ToolResultBlock(call_id=c.id, name=c.name, is_error=True,
                            content=[TextBlock(text="（这次工具调用被中断，结果未知：它可能没有执行，也可能已经"
                                                     "改动了文件。继续前请先确认当前状态。）")])
            for c in last.tool_calls()
        ]
        return [*messages, Message(role="user", content=results, meta={"kind": "repair"})]
    return messages


def summary_message(summary: str, activated_skills: list[str]) -> Message:
    text = f"<context_summary>\n{summary.strip()}\n</context_summary>\n\n"
    if activated_skills:
        text += (
            "Skills activated before the summary: " + ", ".join(activated_skills)
            + ". Call activate_skill again if you still need their instructions.\n\n"
        )
    text += "Continue the task from where you left off."
    return user_text(text, kind=SUMMARY_KIND)
