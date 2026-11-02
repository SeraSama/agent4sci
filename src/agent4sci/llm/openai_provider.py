"""OpenAI Chat Completions adapter; also serves any OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import uuid
from typing import Any

import openai

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
    ToolSpec,
    Usage,
    estimate_tokens,
)


def _image_part(block: ImageBlock) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:{block.media_type};base64,{block.data}"}}


def to_openai_messages(system: str, messages: list[Message], profile: ModelProfile,
                       can_replay: Any = None) -> list[dict[str, Any]]:
    caps = profile.capabilities
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        if message.role == "assistant":
            raw = message.raw or {}
            if can_replay is not None and can_replay(message) and raw.get("message"):
                replay = dict(raw["message"])
                if not caps.echo_reasoning_content:
                    replay.pop("reasoning_content", None)
                out.append(replay)
                continue
            calls = message.tool_calls()
            entry: dict[str, Any] = {"role": "assistant", "content": message.text() or (None if calls else "")}
            if calls:
                entry["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.input, ensure_ascii=False)}}
                    for c in calls
                ]
            if caps.echo_reasoning_content and message.reasoning():
                entry["reasoning_content"] = message.reasoning()
            out.append(entry)
            continue

        images: list[ImageBlock] = []
        for r in message.tool_results():
            text = r.text() or "(空)"
            out.append({"role": "tool", "tool_call_id": r.call_id, "content": f"[ERROR] {text}" if r.is_error else text})
            images.extend(c for c in r.content if isinstance(c, ImageBlock))
        parts: list[dict[str, Any]] = []
        for b in message.content:
            if isinstance(b, TextBlock) and b.text.strip():
                parts.append({"type": "text", "text": b.text})
            elif isinstance(b, ImageBlock):
                images.append(b)
        if images:
            if caps.vision:
                parts.append({"type": "text", "text": "（附图）"})
                parts.extend(_image_part(img) for img in images)
            else:
                parts.append({"type": "text", "text": f"（{len(images)} 张图片已省略：当前模型不支持图像输入）"})
        if parts:
            if all(p["type"] == "text" for p in parts):
                out.append({"role": "user", "content": "\n\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": "user", "content": parts})
    return out


def to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in tools
    ]


class _CallAccumulator:
    def __init__(self) -> None:
        self.calls: dict[int, dict[str, str]] = {}

    def add(self, tc: Any, on_stream: StreamCallback | None) -> None:
        index = tc.index if getattr(tc, "index", None) is not None else len(self.calls)
        entry = self.calls.setdefault(index, {"id": "", "name": "", "arguments": "", "announced": ""})
        if getattr(tc, "id", None):
            entry["id"] = tc.id
        fn = getattr(tc, "function", None)
        if fn is not None:
            name = getattr(fn, "name", None)
            if name:
                if not entry["name"]:
                    entry["name"] = name
                elif name != entry["name"]:
                    entry["name"] += name
            if getattr(fn, "arguments", None):
                entry["arguments"] += fn.arguments
        if entry["name"] and not entry["announced"] and on_stream is not None:
            entry["announced"] = "1"
            on_stream(StreamEvent(kind="tool_call_start", call_id=entry["id"] or None, name=entry["name"]))

    def blocks(self) -> list[ToolCallBlock]:
        out = []
        for index in sorted(self.calls):
            entry = self.calls[index]
            args_text = entry["arguments"].strip()
            error = None
            try:
                parsed = json.loads(args_text) if args_text else {}
                if not isinstance(parsed, dict):
                    raise ValueError("arguments must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                parsed, error = {}, f"模型给出的工具参数不是合法的 JSON 对象（{exc}），请重试。"
            out.append(ToolCallBlock(id=entry["id"] or "call_" + uuid.uuid4().hex[:12], name=entry["name"],
                                     input=parsed, input_error=error))
        return out


def map_error(exc: Exception) -> LLMError:
    status = getattr(exc, "status_code", None)
    message = getattr(exc, "message", None) or str(exc)
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LLMError(message, "auth", status)
    if isinstance(exc, openai.NotFoundError):
        return LLMError(message, "not_found", status)
    if isinstance(exc, openai.RateLimitError):
        return LLMError(message, "rate_limit", status)
    if isinstance(exc, openai.BadRequestError):
        return LLMError(message, "bad_request", status)
    if isinstance(exc, openai.APITimeoutError):
        return LLMError(message, "timeout")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError(message, "connection")
    if isinstance(exc, openai.APIStatusError):
        return LLMError(message, "server" if (status or 0) >= 500 else "other", status)
    return LLMError(f"{type(exc).__name__}: {exc}")


class OpenAIProvider(LLMProvider):
    kind = "openai"

    OPTIONAL_PARAMS = ("stream_options", "parallel_tool_calls")

    def __init__(self, profile: ModelProfile, api_key: str | None, http_client: Any = None) -> None:
        super().__init__(profile, api_key)
        kwargs: dict[str, Any] = {"api_key": api_key or "not-needed", "timeout": profile.timeout_s, "max_retries": 2}
        if profile.base_url:
            kwargs["base_url"] = profile.base_url
        if profile.extra_headers:
            kwargs["default_headers"] = dict(profile.extra_headers)
        if http_client is not None:
            kwargs["http_client"] = http_client
        self.client = openai.AsyncOpenAI(**kwargs)
        self._dropped: set[str] = set()

    def build_params(self, system: str, messages: list[Message], tools: list[ToolSpec],
                     max_tokens: int | None) -> dict[str, Any]:
        profile = self.profile
        params: dict[str, Any] = {
            "model": profile.model,
            "messages": to_openai_messages(system, messages, profile, self.can_replay),
            "stream": True,
        }
        if tools and profile.capabilities.tools:
            params["tools"] = to_openai_tools(tools)
            params["tool_choice"] = "auto"
            if profile.provider == "openai" and not profile.capabilities.parallel_tools:
                params["parallel_tool_calls"] = False
        if profile.capabilities.stream_usage:
            params["stream_options"] = {"include_usage": True}
        limit = max_tokens or profile.max_tokens
        if limit:
            params[profile.max_tokens_param] = limit
        if profile.reasoning_effort:
            params["reasoning_effort"] = profile.reasoning_effort
        if profile.temperature is not None:
            params["temperature"] = profile.temperature
        if profile.extra_body:
            params["extra_body"] = dict(profile.extra_body)
        for name in self._dropped:
            params.pop(name, None)
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
        params = self.build_params(system, messages, tools, max_tokens)
        try:
            return await self._stream(params, on_stream)
        except openai.BadRequestError as exc:
            optional = [p for p in self.OPTIONAL_PARAMS if p in params]
            if not optional:
                raise map_error(exc) from exc
            # Some compatible endpoints reject optional params; retry once without them.
            self._dropped.update(optional)
            for name in optional:
                params.pop(name, None)
            try:
                return await self._stream(params, on_stream)
            except openai.APIError as exc2:
                raise map_error(exc2) from exc2
        except openai.APIError as exc:
            raise map_error(exc) from exc

    async def _stream(self, params: dict[str, Any], on_stream: StreamCallback | None) -> LLMResponse:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        acc = _CallAccumulator()
        finish: str | None = None
        usage_obj: Any = None
        served = self.profile.model
        stream = await self.client.chat.completions.create(**params)
        async for chunk in stream:
            if on_stream is not None:
                on_stream(StreamEvent(kind="keepalive"))  # also empty/usage-only chunks: the request is alive
            served = getattr(chunk, "model", None) or served
            if getattr(chunk, "usage", None):
                usage_obj = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is not None:
                if delta.content:
                    text_parts.append(delta.content)
                    if on_stream:
                        on_stream(StreamEvent(kind="text", text=delta.content))
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if isinstance(reasoning, str) and reasoning:
                    reasoning_parts.append(reasoning)
                    if on_stream:
                        on_stream(StreamEvent(kind="reasoning", text=reasoning))
                for tc in delta.tool_calls or []:
                    acc.add(tc, on_stream)
            if choice.finish_reason:
                finish = choice.finish_reason

        text = "".join(text_parts)
        reasoning = "".join(reasoning_parts)
        calls = acc.blocks()
        blocks: list[Any] = []
        if reasoning:
            blocks.append(ReasoningBlock(text=reasoning))
        if text:
            blocks.append(TextBlock(text=text))
        blocks.extend(calls)

        stop: StopReason
        if calls:
            stop = "max_tokens" if finish == "length" else "tool_use"
        elif finish == "length":
            stop = "max_tokens"
        elif finish == "content_filter":
            stop = "refusal"
        else:
            stop = "end_turn"

        raw_message: dict[str, Any] = {"role": "assistant", "content": text or (None if calls else "")}
        if calls:
            raw_message["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.input, ensure_ascii=False)}}
                for c in calls
            ]
        if reasoning:
            raw_message["reasoning_content"] = reasoning
        message = Message(role="assistant", content=blocks, raw={**self.raw_key(), "message": raw_message})

        if usage_obj is not None:
            details = getattr(usage_obj, "prompt_tokens_details", None)
            cached = (getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
            prompt = getattr(usage_obj, "prompt_tokens", 0) or 0
            usage = Usage(input_tokens=max(prompt - cached, 0), cache_read_tokens=cached,
                          output_tokens=getattr(usage_obj, "completion_tokens", 0) or 0)
        else:
            usage = Usage(input_tokens=estimate_tokens(params["messages"]),
                          output_tokens=estimate_tokens(text + reasoning) + sum(estimate_tokens(c.input) for c in calls))
            message.meta["usage_estimated"] = True
        return LLMResponse(message=message, stop=stop, usage=usage, model=served)

    async def aclose(self) -> None:
        await self.client.close()
