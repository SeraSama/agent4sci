from __future__ import annotations

from pydantic import BaseModel, Field

from .base import ToolContext, tool

AUTO_MODE_ANSWER = (
    "当前为全自动模式，没有人可以回答。请基于已有信息做出最合理的决定，"
    "并在结果里明确写出你所做的假设。"
)


class Question(BaseModel):
    question: str = Field(description="One question, in the researcher's language")
    options: list[str] | None = Field(None, description="2-5 short choices, your default marked '（推荐）'")
    multi: bool = Field(False, description="Several options can be picked together")


class AskArgs(BaseModel):
    questions: list[Question] | None = Field(None, max_length=6, description="Ask everything you need at once")
    question: str | None = Field(None, description="Shorthand for a single question")
    options: list[str] | None = Field(None, description="Choices for the shorthand question")
    context: str | None = Field(None, description="Background: what you know, what each choice costs, and what "
                                                  "you will assume if they skip")


def _questions(args: AskArgs) -> list[Question]:
    items = list(args.questions or [])
    if args.question:
        items.insert(0, Question(question=args.question, options=args.options))
    return items


@tool("ask_human", category="human")
async def ask_human(args: AskArgs, ctx: ToolContext):
    """Ask the researcher what you need to know and wait for the answer. Ask everything that blocks you now in
    one call (up to 4 questions) instead of interrupting again a minute later; give each question a few concrete
    options and mark your default. The researcher can pick an option or write their own answer to any question.
    Use it for genuine decisions (scope, trade-offs, access to data), not for things you can find out yourself."""
    items = _questions(args)
    if not items:
        return "没有要问的问题。"
    if ctx.auto_mode or ctx.interactions is None:
        return AUTO_MODE_ANSWER
    payload = {
        "questions": [q.model_dump() for q in items],
        "context": args.context or "",
        "options": items[0].options or [],  # older UIs show only the first question's choices
    }
    title = items[0].question if len(items) == 1 else f"{items[0].question}（共 {len(items)} 个问题）"
    answer = await ctx.interactions.ask("question", title, payload, topics=ctx.topics)

    given = answer.get("answers")
    if not isinstance(given, list):
        single = answer.get("answer") or answer.get("choice") or ""
        note = str(answer.get("note") or "").strip()
        given = [f"{single}（补充：{note}）" if note and note != single else (single or note)]
    lines = []
    for question, value in zip(items, [*given, *[""] * len(items)]):
        text = str(value).strip()
        lines.append(f"- {question.question}\n  {text or '（未回答，请自行决定并写明假设）'}")
    if len(lines) == 1 and "未回答" not in lines[0]:
        return f"研究者的回答：{str(given[0]).strip()}"
    return "研究者的回答：\n" + "\n".join(lines)


HUMAN_TOOLS = [ask_human]
