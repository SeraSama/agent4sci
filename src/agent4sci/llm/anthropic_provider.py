"""Anthropic Messages API adapter (streaming, adaptive thinking, refusal fallbacks)."""

from __future__ import annotations

from typing import Any

import anthropic

from ..core.models import ModelProfile
from .base import LLMError, LLMProvider, StreamCallback
from .types import (
    ImageBlock,
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
)

# Models for which Anthropic recommends server-side refusal fallbacks (fallbacks="default").
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5-1", "claude-fable-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_MAX_TOKENS = 64_000
COMPAT_MAX_TOKENS = 8_192

_STOP_MAP: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "pause_turn": "pause",
    "refusal": "refusal",
}


def is_official(profile: ModelProfile) -> bool:
    return not profile.base_url or "api.anthropic.com" in profile.base_url


def _image_param(block: ImageBlock) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": block.media_type, "data": block.data}}


def _text_param(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text if text.strip() else "(空)"}


def to_anthropic_messages(messages: list[Message], can_replay: Any = None) -> list[dict[str, Any]]:
    """Convert neutral messages; replay raw content for messages this endpoint produced."""
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "assistant":
            raw = message.raw or {}
            if can_replay is not None and can_replay(message) and raw.get("content"):
                out.append({"role": "assistant", "content": raw["content"]})
                continue
            blocks: list[dict[str, Any]] = []
            for b in message.content:
                if isinstance(b, TextBlock) and b.text.strip():
                    blocks.append({"type": "text", "text": b.text})
                elif isinstance(b, ToolCallBlock):
                    blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                # Reasoning from other providers has no signature and cannot be replayed.
            out.append({"role": "assistant", "content": blocks or [_text_param("")]})
            continue

        blocks = []
        for r in message.tool_results():  # tool_result blocks must come first
            content: list[dict[str, Any]] = []
            for c in r.content:
                content.append(_image_param(c) if isinstance(c, ImageBlock) else _text_param(c.text))
            blocks.append({"type": "tool_result", "tool_use_id": r.call_id, "content": content or [_text_param("")],
                           "is_error": r.is_error})
        for b in message.content:
            if isinstance(b, TextBlock) and b.text.strip():
                blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ImageBlock):
                blocks.append(_image_param(b))
        out.append({"role": "user", "content": blocks or [_text_param("")]})
    return out


def to_anthropic_tools(tools: list[ToolSpec], profile: ModelProfile) -> list[dict[str, Any]]:
    official = is_official(profile)
    caps = profile.capabilities
    out = []
    for spec in tools:
        native = spec.native.get("anthropic")
        if native and official:
            ntype = str(native.get("type", ""))
            allowed = (ntype.startswith("memory_") and caps.native_memory_tool) or (
                ntype.startswith(("web_search_", "web_fetch_")) and caps.native_web_search
            )
            if allowed:
                out.append(native)
                continue
        tool: dict[str, Any] = {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
        if official:
            tool["eager_input_streaming"] = True
        out.append(tool)
    return out


def usage_from(u: Any) -> Usage:
    if u is None:
        return Usage()
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def from_anthropic_message(final: Any, raw_key: dict[str, Any]) -> tuple[Message, StopReason, dict[str, Any] | None]:
    blocks: list[Any] = []
    meta: dict[str, Any] = {}
    server_uses: list[dict[str, Any]] = []
    for b in final.content:
        btype = getattr(b, "type", "")
        if btype == "text":
            if b.text:
                blocks.append(TextBlock(text=b.text))
        elif btype == "thinking":
            if getattr(b, "thinking", ""):
                blocks.append(ReasoningBlock(text=b.thinking))
        elif btype == "tool_use":
            ok = isinstance(b.input, dict)
            blocks.append(ToolCallBlock(id=b.id, name=b.name, input=b.input if ok else {},
                                        input_error=None if ok else "工具参数不是合法的 JSON 对象，请重试。"))
        elif btype == "server_tool_use":
            server_uses.append({"id": b.id, "name": b.name, "input": getattr(b, "input", {})})
        elif btype == "fallback":
            meta["fallback"] = b.to_dict()
    if server_uses:
        meta["server_tool_uses"] = server_uses
    stop = _STOP_MAP.get(str(final.stop_reason or "end_turn"), "end_turn")
    stop_details = final.stop_details.to_dict() if getattr(final, "stop_details", None) else None
    raw = {**raw_key, "served_model": final.model, "content": [b.to_dict(mode="json") for b in final.content]}
    return Message(role="assistant", content=blocks, raw=raw, meta=meta), stop, stop_details


def map_error(exc: Exception) -> LLMError:
    status = getattr(exc, "status_code", None)
    message = getattr(exc, "message", None) or str(exc)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LLMError(message, "auth", status)
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError(message, "not_found", status)
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError(message, "rate_limit", status)
    if isinstance(exc, anthropic.BadRequestError):
        return LLMError(message, "bad_request", status)
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError(message, "timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError(message, "connection")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError(message, "server" if (status or 0) >= 500 else "other", status)
    return LLMError(f"{type(exc).__name__}: {exc}")


class AnthropicProvider(LLMProvider):
    kind = "anthropic"

    def __init__(self, profile: ModelProfile, api_key: str | None, http_client: Any = None) -> None:
        super().__init__(profile, api_key)
        kwargs: dict[str, Any] = {"timeout": profile.timeout_s, "max_retries": 2}
        if api_key:
            kwargs["api_key"] = api_key
        if profile.base_url:
            kwargs["base_url"] = profile.base_url
        if profile.extra_headers:
            kwargs["default_headers"] = dict(profile.extra_headers)
        if http_client is not None:
            kwargs["http_client"] = http_client
        self.client = anthropic.AsyncAnthropic(**kwargs)

    def build_params(self, system: str, messages: list[Message], tools: list[ToolSpec], effort: str | None,
                     max_tokens: int | None) -> dict[str, Any]:
        profile = self.profile
        official = is_official(profile)
        params: dict[str, Any] = {
            "model": profile.model,
            "max_tokens": max_tokens or profile.max_tokens or (DEFAULT_MAX_TOKENS if official else COMPAT_MAX_TOKENS),
            "messages": to_anthropic_messages(messages, self.can_replay),
        }
        if official:
            params["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            params["cache_control"] = {"type": "ephemeral"}
        else:
            params["system"] = system
        if tools:
            params["tools"] = to_anthropic_tools(tools, profile)
        if profile.capabilities.thinking:
            params["thinking"] = {"type": "adaptive", "display": "summarized"} if official else {"type": "adaptive"}
        if effort and official:
            params["output_config"] = {"effort": effort}
        betas: list[str] = []
        if official and profile.fallbacks and profile.model in FALLBACK_MODELS:
            params["fallbacks"] = "default"
            betas.append(FALLBACK_BETA)
        if betas:
            params["betas"] = betas
        if profile.extra_body:
            params["extra_body"] = dict(profile.extra_body)
        return params

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
        params = self.build_params(system, messages, tools, effort, max_tokens)
        try:
            async with self.client.beta.messages.stream(**params) as stream:
                async for event in stream:
                    if on_stream is None:
                        continue
                    on_stream(StreamEvent(kind="keepalive"))  # pings included: the request is alive
                    etype = getattr(event, "type", "")
                    if etype == "text":
                        on_stream(StreamEvent(kind="text", text=event.text))
                    elif etype == "thinking":
                        on_stream(StreamEvent(kind="reasoning", text=event.thinking))
                    elif etype == "content_block_start" and getattr(event.content_block, "type", "") == "tool_use":
                        on_stream(StreamEvent(kind="tool_call_start", call_id=event.content_block.id,
                                              name=event.content_block.name))
                final = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise map_error(exc) from exc
        message, stop, stop_details = from_anthropic_message(final, self.raw_key())
        return LLMResponse(
            message=message, stop=stop, usage=usage_from(final.usage), model=final.model or self.profile.model,
            stop_details=stop_details, request_id=getattr(final, "_request_id", None),
        )

    async def aclose(self) -> None:
        await self.client.close()
