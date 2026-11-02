"""Native Ollama adapter (``/api/chat``).

Ollama's OpenAI-compatible endpoint ignores ``num_ctx`` and serves every model with a 4096-token context, which
silently cuts the front off an agent's system prompt and tool definitions. The native API takes ``num_ctx`` per
request, streams NDJSON, returns thinking separately and tool calls as JSON objects, and ``/api/show`` tells us
whether the model supports tools / thinking / images at all.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx2

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
)

DEFAULT_BASE = "http://localhost:11434"
_THINK_TAG = re.compile(r"<think>(.*?)</think>", re.S)


def ollama_base(profile: ModelProfile) -> str:
    base = (profile.base_url or DEFAULT_BASE).rstrip("/")
    return base[:-3] if base.endswith("/v1") else base  # accept an OpenAI-style base URL too


def to_ollama_messages(system: str, messages: list[Message], vision: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        if message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.text()}
            calls = message.tool_calls()
            if calls:
                entry["tool_calls"] = [{"function": {"name": c.name, "arguments": c.input}} for c in calls]
            out.append(entry)
            continue
        images: list[ImageBlock] = []
        for r in message.tool_results():
            text = r.text() or "(空)"
            out.append({"role": "tool", "tool_name": r.name, "content": f"[ERROR] {text}" if r.is_error else text})
            images.extend(c for c in r.content if isinstance(c, ImageBlock))
        texts = [b.text for b in message.content if isinstance(b, TextBlock) and b.text.strip()]
        images.extend(b for b in message.content if isinstance(b, ImageBlock))
        if images and not vision:
            texts.append(f"（{len(images)} 张图片已省略：当前模型不支持图像输入）")
        if texts or (images and vision):
            entry = {"role": "user", "content": "\n\n".join(texts) or "（附图）"}
            if images and vision:
                entry["images"] = [img.data for img in images]
            out.append(entry)
    return out


def _tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCallBlock]:
    out = []
    for call in raw_calls:
        fn = call.get("function") or {}
        args = fn.get("arguments")
        error = None
        if isinstance(args, str):  # some model templates hand back a JSON string
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError as exc:
                args, error = {}, f"模型给出的工具参数不是合法的 JSON 对象（{exc}），请重试。"
        if not isinstance(args, dict):
            args, error = {}, error or "模型给出的工具参数不是 JSON 对象，请重试。"
        out.append(ToolCallBlock(id=call.get("id") or "call_" + uuid.uuid4().hex[:12], name=str(fn.get("name") or ""),
                                 input=args, input_error=error))
    return out


class OllamaProvider(LLMProvider):
    kind = "ollama"

    def __init__(self, profile: ModelProfile, api_key: str | None = None, http_client: Any = None) -> None:
        super().__init__(profile, api_key)
        self.base = ollama_base(profile)
        headers = dict(profile.extra_headers or {})
        if api_key:  # Ollama behind an authenticating proxy, or ollama.com's hosted API
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = http_client or httpx2.AsyncClient(
            timeout=httpx2.Timeout(30.0, read=profile.timeout_s), headers=headers)
        self._owns_client = http_client is None
        self._capabilities: set[str] | None = None

    async def capabilities(self) -> set[str]:
        """What the model can do according to ``/api/show`` (empty when unknown, e.g. very old Ollama)."""
        if self._capabilities is None:
            try:
                resp = await self.client.post(f"{self.base}/api/show", json={"model": self.profile.model})
                if resp.status_code == 404:
                    raise LLMError(f"Ollama 里没有模型 {self.profile.model}：先运行 `ollama pull {self.profile.model}`",
                                   "not_found", 404)
                self._capabilities = set(resp.json().get("capabilities") or []) if resp.status_code == 200 else set()
            except httpx2.ConnectError as exc:
                raise LLMError(f"无法连接 Ollama（{self.base}）：请确认 Ollama 已启动", "connection") from exc
            except (httpx2.HTTPError, ValueError):
                self._capabilities = set()
        return self._capabilities

    async def build_body(self, system: str, messages: list[Message], tools: list[ToolSpec],
                         max_tokens: int | None) -> dict[str, Any]:
        profile = self.profile
        caps = await self.capabilities()
        vision = profile.capabilities.vision and (not caps or "vision" in caps)
        options: dict[str, Any] = {"num_ctx": profile.context_window}
        limit = max_tokens or profile.max_tokens
        if limit:
            options["num_predict"] = limit
        if profile.temperature is not None:
            options["temperature"] = profile.temperature
        body: dict[str, Any] = {"model": profile.model, "messages": to_ollama_messages(system, messages, vision),
                                "stream": True, "options": options}
        # Sending tools or `think` to a model that lacks them is a 400 error, so follow /api/show when it answers.
        if tools and profile.capabilities.tools and (not caps or "tools" in caps):
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.input_schema}} for t in tools]
        if "thinking" in caps and profile.capabilities.thinking:
            body["think"] = True
        extra = dict(profile.extra_body or {})
        if isinstance(extra.get("options"), dict):
            options.update(extra.pop("options"))
        body.update(extra)
        return body

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
        body = await self.build_body(system, messages, tools, max_tokens)
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        calls: list[ToolCallBlock] = []
        final: dict[str, Any] = {}
        try:
            async with self.client.stream("POST", f"{self.base}/api/chat", json=body) as resp:
                if resp.status_code >= 400:
                    raise self._status_error(resp.status_code, (await resp.aread()).decode("utf-8", "replace"))
                async for line in resp.aiter_lines():
                    if on_stream is not None:
                        on_stream(StreamEvent(kind="keepalive"))
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise LLMError(f"Ollama: {chunk['error']}", "server")
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        thinking_parts.append(msg["thinking"])
                        if on_stream:
                            on_stream(StreamEvent(kind="reasoning", text=msg["thinking"]))
                    if msg.get("content"):
                        text_parts.append(msg["content"])
                        if on_stream:
                            on_stream(StreamEvent(kind="text", text=msg["content"]))
                    for call in _tool_calls(msg.get("tool_calls") or []):
                        calls.append(call)
                        if on_stream:
                            on_stream(StreamEvent(kind="tool_call_start", call_id=call.id, name=call.name))
                    if chunk.get("done"):
                        final = chunk
        except httpx2.ConnectError as exc:
            raise LLMError(f"无法连接 Ollama（{self.base}）：请确认 Ollama 已启动", "connection") from exc
        except httpx2.TimeoutException as exc:
            raise LLMError(f"Ollama 响应超时（{self.profile.timeout_s} 秒）", "timeout") from exc
        except httpx2.HTTPError as exc:
            raise LLMError(f"Ollama 连接中断：{exc}", "connection") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama 返回了无法解析的数据：{exc}", "server") from exc

        text = "".join(text_parts)
        thinking = "".join(thinking_parts)
        inline = _THINK_TAG.findall(text)  # templates that still put reasoning inline
        if inline:
            thinking = (thinking + "\n" + "\n".join(inline)).strip()
            text = _THINK_TAG.sub("", text).lstrip()
        blocks: list[Any] = []
        if thinking:
            blocks.append(ReasoningBlock(text=thinking))
        if text:
            blocks.append(TextBlock(text=text))
        blocks.extend(calls)
        done_reason = final.get("done_reason")
        stop: StopReason = "max_tokens" if done_reason == "length" else ("tool_use" if calls else "end_turn")
        message = Message(role="assistant", content=blocks, raw={**self.raw_key()})
        usage = Usage(input_tokens=int(final.get("prompt_eval_count") or 0),
                      output_tokens=int(final.get("eval_count") or 0))
        return LLMResponse(message=message, stop=stop, usage=usage, model=final.get("model") or self.profile.model)

    def _status_error(self, status: int, body: str) -> LLMError:
        try:
            detail = json.loads(body).get("error") or body
        except (json.JSONDecodeError, AttributeError):
            detail = body
        detail = str(detail)[:500]
        if status == 404:
            return LLMError(f"Ollama：{detail}（可运行 `ollama pull {self.profile.model}`）", "not_found", status)
        if status in (401, 403):
            return LLMError(f"Ollama：{detail}", "auth", status)
        if status == 400:
            return LLMError(f"Ollama：{detail}", "bad_request", status)
        return LLMError(f"Ollama：{detail}", "server" if status >= 500 else "other", status)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
