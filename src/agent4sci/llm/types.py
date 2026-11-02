"""Provider-neutral message model.

Every adapter converts to and from these types. Assistant messages also keep
the provider's own content in ``raw`` so that the next request to the *same*
provider can replay it byte-for-byte (Claude thinking blocks carry signatures
bound to the conversation prefix and must not be rebuilt).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    media_type: str = "image/png"
    data: str  # base64
    path: str | None = None  # where the image was saved, for the UI


class ToolCallBlock(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    input_error: str | None = None  # set when the model produced unparseable/truncated input


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str = ""
    content: list[Annotated[Union[TextBlock, ImageBlock], Field(discriminator="type")]] = Field(default_factory=list)
    is_error: bool = False

    def text(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))


class ReasoningBlock(BaseModel):
    """Readable reasoning (a summary on Claude; ``reasoning_content`` on DeepSeek-style APIs)."""

    type: Literal["reasoning"] = "reasoning"
    text: str = ""


Block = Annotated[
    Union[TextBlock, ImageBlock, ToolCallBlock, ToolResultBlock, ReasoningBlock],
    Field(discriminator="type"),
]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: list[Block] = Field(default_factory=list)
    raw: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def reasoning(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, ReasoningBlock) and b.text)

    def tool_calls(self) -> list[ToolCallBlock]:
        return [b for b in self.content if isinstance(b, ToolCallBlock)]

    def tool_results(self) -> list[ToolResultBlock]:
        return [b for b in self.content if isinstance(b, ToolResultBlock)]


def user_text(text: str, **meta: Any) -> Message:
    return Message(role="user", content=[TextBlock(text=text)], meta=meta)


def assistant_text(text: str) -> Message:
    return Message(role="assistant", content=[TextBlock(text=text)])


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    # Provider-native declarations that replace the JSON-schema tool, e.g.
    # {"anthropic": {"type": "memory_20250818", "name": "memory"}}
    native: dict[str, dict[str, Any]] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None

    @property
    def context_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + other.cost_usd


StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "pause"]


class LLMResponse(BaseModel):
    message: Message
    stop: StopReason
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    latency_s: float = 0.0
    stop_details: dict[str, Any] | None = None
    request_id: str | None = None


class StreamEvent(BaseModel):
    # "keepalive": the connection produced data that is not content (ping, usage, empty chunk). Consumers
    # that render output ignore it; it tells the agent loop the model is still alive.
    kind: Literal["text", "reasoning", "tool_call_start", "tool_input", "keepalive"]
    text: str = ""
    call_id: str | None = None
    name: str | None = None


def estimate_tokens(value: Any) -> int:
    """Cheap token estimate (~4 chars/token for Latin text, ~1.5 for CJK)."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    cjk = sum(1 for ch in value if "一" <= ch <= "鿿")
    return int(cjk / 1.5 + (len(value) - cjk) / 4) + 1
