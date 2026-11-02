"""Scripted provider for tests and the offline demo mode (``A4S_FAKE_LLM=1``)."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Callable

from pydantic import BaseModel, Field

from ..core.models import ModelProfile, demo_profile
from .base import LLMProvider, StreamCallback
from .types import (
    LLMResponse,
    Message,
    ReasoningBlock,
    StopReason,
    StreamEvent,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
    Usage,
    estimate_tokens,
)


class FakeTurn(BaseModel):
    text: str = ""
    reasoning: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = Field(default_factory=list)
    stop: StopReason | None = None


Brain = Callable[[str, list[Message], list[ToolSpec]], FakeTurn]


class FakeProvider(LLMProvider):
    kind = "fake"

    def __init__(
        self,
        profile: ModelProfile | None = None,
        script: list[FakeTurn] | None = None,
        brain: Brain | None = None,
        delay: float = 0.0,
        cost_per_turn: float | None = 0.0,  # None: leave the cost to the profile pricing
    ) -> None:
        super().__init__(profile or demo_profile(), None)
        self.script = list(script or [])
        self.brain = brain
        self.delay = delay
        self.cost_per_turn = cost_per_turn
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream: StreamCallback | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": [m.model_copy(deep=True) for m in messages],
                           "tools": [t.name for t in tools], "effort": effort})
        if self.script:
            turn = self.script.pop(0)
        elif self.brain is not None:
            turn = self.brain(system, messages, tools)
        else:
            turn = FakeTurn(text="（脚本已结束）")

        blocks: list[Any] = []
        if turn.reasoning:
            await self._stream(on_stream, "reasoning", turn.reasoning)
            blocks.append(ReasoningBlock(text=turn.reasoning))
        if turn.text:
            await self._stream(on_stream, "text", turn.text)
            blocks.append(TextBlock(text=turn.text))
        for name, args in turn.tool_calls:
            call_id = "call_" + uuid.uuid4().hex[:12]
            if on_stream:
                on_stream(StreamEvent(kind="tool_call_start", call_id=call_id, name=name))
            blocks.append(ToolCallBlock(id=call_id, name=name, input=args))

        stop: StopReason = turn.stop or ("tool_use" if turn.tool_calls else "end_turn")
        message = Message(role="assistant", content=blocks, raw={**self.raw_key(), "content": None})
        usage = Usage(
            input_tokens=estimate_tokens(system) + sum(estimate_tokens(m.model_dump()) for m in messages),
            output_tokens=estimate_tokens(turn.model_dump()),
            cost_usd=self.cost_per_turn,
        )
        return LLMResponse(message=message, stop=stop, usage=usage, model=self.profile.model)

    async def _stream(self, on_stream: StreamCallback | None, kind: str, text: str) -> None:
        if on_stream is None:
            return
        step = 6
        for i in range(0, len(text), step):
            on_stream(StreamEvent(kind=kind, text=text[i : i + step]))  # type: ignore[arg-type]
            if self.delay:
                await asyncio.sleep(self.delay)


# ---------------------------------------------------------------------------
# Demo brain: plausible, deterministic behaviour so the UI can be explored
# without API keys.
# ---------------------------------------------------------------------------


def example_from_schema(schema: dict[str, Any], defs: dict[str, Any] | None = None, key: str = "") -> Any:
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        ref = schema["$ref"].split("/")[-1]
        return example_from_schema(defs.get(ref, {}), defs, key)
    for combo in ("anyOf", "oneOf", "allOf"):
        if combo in schema and schema[combo]:
            options = [s for s in schema[combo] if s.get("type") != "null"] or schema[combo]
            return example_from_schema(options[0], defs, key)
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        values = schema["enum"]
        for preferred in ("accept", "approve", "pass", "done", True):
            if preferred in values:
                return preferred
        return values[0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {name: example_from_schema(sub, defs, name) for name, sub in props.items()}
    if kind == "array":
        return [example_from_schema(schema.get("items", {}), defs, key)]
    if kind == "integer":
        return 1
    if kind == "number":
        return 0.9 if "score" in key.lower() else 0.5
    if kind == "boolean":
        return True
    return f"（演示）{key or '内容'}"


def _current_exchange(messages: list[Message]) -> list[Message]:
    """Messages since the last user message that contains typed text (not only tool results)."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role == "user" and any(isinstance(b, TextBlock) for b in m.content) and not m.tool_results():
            return messages[i:]
    return messages


_EXPECTED = re.compile(r"(?:必须产出这些文件|预期产出还不存在)：([^\n]+)")
_PATH = re.compile(r"[\w./-]+\.(?:md|bib|json|csv|txt|py|yaml)")


def _expected_files(text: str) -> list[str]:
    match = _EXPECTED.search(text or "")
    return _PATH.findall(match.group(1)) if match else []


def _demo_content(path: str, question: str) -> str:
    if path.endswith(".json"):
        return '{"score": 0.5, "primary_metric": 0.5, "note": "demo"}\n'
    if path.endswith(".bib"):
        return "@misc{demo2026example,\n  title = {Demo reference},\n  year = {2026}\n}\n"
    if path.endswith(".py"):
        return "print('demo')\n"
    return f"# {path}\n\n（演示模式生成的内容）\n\n{question[:300]}\n"


def demo_brain(system: str, messages: list[Message], tools: list[ToolSpec]) -> FakeTurn:
    names = {t.name for t in tools}
    exchange = _current_exchange(messages)
    question = exchange[0].text().strip() if exchange else ""
    steps = sum(1 for m in exchange if m.role == "assistant")
    last_results = [b for b in (exchange[-1].content if exchange else []) if isinstance(b, ToolResultBlock)]

    expected = _expected_files(question)
    if steps == 0 and expected and "write_file" in names:
        return FakeTurn(
            reasoning="演示模式：直接生成本步骤要求的产出文件。",
            text="（演示）生成本步骤要求的文件：" + "、".join(expected),
            tool_calls=[("write_file", {"path": path, "content": _demo_content(path, question)}) for path in expected],
        )
    if "submit_result" in names:
        if steps == (1 if expected and "write_file" in names else 0):
            spec = next(t for t in tools if t.name == "submit_result")
            args = example_from_schema(spec.input_schema)
            return FakeTurn(
                reasoning="这是演示模式：直接根据节点要求生成一个示例结果。",
                text="（演示）已完成该节点的任务，提交结构化结果。",
                tool_calls=[("submit_result", args)],
            )
        return FakeTurn(text="（演示）结果已提交。")

    if steps == 0 and "list_files" in names:
        return FakeTurn(
            reasoning="先了解工作区里已有哪些文件，再决定下一步。",
            text="我先看看当前工作区里有哪些文件。",
            tool_calls=[("list_files", {"path": "."})],
        )
    if steps == 1 and "write_file" in names and not any(r.is_error for r in last_results):
        note = f"# 演示笔记\n\n问题：{question or '（无）'}\n\n这是演示模式生成的文件。\n"
        return FakeTurn(
            text="我把要点记到一份笔记里。",
            tool_calls=[("write_file", {"path": "notes/demo-notes.md", "content": note})],
        )
    answer = (
        f"**（演示模式回答）** 你问的是：{question or '……'}\n\n"
        "| 步骤 | 状态 |\n|---|---|\n| 查看工作区 | ✅ |\n| 记录笔记 | ✅ |\n\n"
        "示例公式：$$\\hat{\\theta} = \\arg\\max_\\theta \\sum_i \\log p(x_i \\mid \\theta)$$\n\n"
        "在“模型”页面添加真实模型并填入 API Key 后，就能得到真正的回答。"
    )
    return FakeTurn(reasoning="整理结果并给出总结。", text=answer)
