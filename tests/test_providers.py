from __future__ import annotations

import json

import httpx2
import pytest
from pydantic import BaseModel

from agent4sci.agent.agent import Agent
from agent4sci.core.models import ModelProfile, profile_from_preset
from agent4sci.llm.anthropic_provider import AnthropicProvider, to_anthropic_messages, to_anthropic_tools
from agent4sci.llm.base import LLMError
from agent4sci.llm.openai_provider import OpenAIProvider, to_openai_messages
from agent4sci.llm.types import (
    ImageBlock,
    Message,
    ReasoningBlock,
    StreamEvent,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
    user_text,
)
from agent4sci.tools.base import ToolContext, tool
from agent4sci.tools.registry import ToolRegistry


def sse(events: list[tuple[str | None, dict | str]]) -> bytes:
    out = []
    for name, data in events:
        if name:
            out.append(f"event: {name}")
        out.append("data: " + (data if isinstance(data, str) else json.dumps(data)))
        out.append("")
    return ("\n".join(out) + "\n").encode()


def anthropic_turn(blocks: list[dict], stop: str, model: str = "claude-opus-5") -> bytes:
    events: list = [("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 50,
                  "cache_creation_input_tokens": 10}}})]
    for i, block in enumerate(blocks):
        if block["type"] == "thinking":
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                   "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "thinking_delta", "thinking": block["thinking"]}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "signature_delta", "signature": block["signature"]}}))
        elif block["type"] == "text":
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                   "content_block": {"type": "text", "text": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "text_delta", "text": block["text"]}}))
        elif block["type"] == "tool_use":
            events.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(block["input"])}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                     "usage": {"output_tokens": 42}}))
    events.append(("message_stop", {"type": "message_stop"}))
    return sse(events)


class Recorder:
    def __init__(self, bodies: list[bytes], status: list[int] | None = None) -> None:
        self.bodies = list(bodies)
        self.status = list(status or [])
        self.requests: list[dict] = []
        self.headers: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(dict(request.headers))
        code = self.status.pop(0) if self.status else 200
        if code != 200:
            return httpx2.Response(code, json={"error": {"type": "invalid_request_error", "message": "bad param"}})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=self.bodies.pop(0))


def anthropic_profile(**kw) -> ModelProfile:
    return profile_from_preset("anthropic", **kw)


class EchoArgs(BaseModel):
    text: str


@tool("echo", "Echo text.")
async def echo(args: EchoArgs, ctx: ToolContext):
    return f"echo:{args.text}"


# ---- Anthropic -----------------------------------------------------------------


def test_anthropic_message_conversion():
    foreign = Message(role="assistant", content=[ReasoningBlock(text="hmm"), TextBlock(text="hi"),
                                                   ToolCallBlock(id="t1", name="echo", input={"text": "a"})],
                      raw={"provider": "openai", "base_url": None, "message": {}})
    results = Message(role="user", content=[TextBlock(text="note after"),
                                            ToolResultBlock(call_id="t1", content=[TextBlock(text=""),
                                                            ImageBlock(data="QUJD")], is_error=True)])
    out = to_anthropic_messages([user_text("q"), foreign, results], can_replay=lambda m: False)
    assert out[1]["content"] == [{"type": "text", "text": "hi"},
                                 {"type": "tool_use", "id": "t1", "name": "echo", "input": {"text": "a"}}]
    user = out[2]["content"]
    assert user[0]["type"] == "tool_result" and user[0]["is_error"] is True  # results first
    assert user[0]["content"][0] == {"type": "text", "text": "(空)"}
    assert user[0]["content"][1]["type"] == "image"
    assert user[1] == {"type": "text", "text": "note after"}


def test_anthropic_tools_native_and_eager():
    profile = anthropic_profile()
    specs = [ToolSpec(name="memory", description="m", input_schema={"type": "object"},
                      native={"anthropic": {"type": "memory_20250818", "name": "memory"}}),
             ToolSpec(name="echo", description="e", input_schema={"type": "object"})]
    out = to_anthropic_tools(specs, profile)
    assert out[0] == {"type": "memory_20250818", "name": "memory"}
    assert out[1]["eager_input_streaming"] is True
    compat = anthropic_profile(base_url="https://example.com/anthropic")
    out = to_anthropic_tools(specs, compat)
    assert out[0]["name"] == "memory" and "input_schema" in out[0] and "eager_input_streaming" not in out[0]


async def test_anthropic_stream_tool_loop_replays_thinking(workspace):
    recorder = Recorder([
        anthropic_turn([{"type": "thinking", "thinking": "plan", "signature": "sig123"},
                        {"type": "text", "text": "Calling."},
                        {"type": "tool_use", "id": "toolu_1", "name": "echo", "input": {"text": "hi"}}], "tool_use"),
        anthropic_turn([{"type": "text", "text": "All done."}], "end_turn"),
    ])
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    provider = AnthropicProvider(anthropic_profile(), "sk-test", http_client=client)
    events: list[StreamEvent] = []
    ctx = ToolContext(workspace=workspace)
    agent = Agent(provider=provider, tools=ToolRegistry([echo]), ctx=ctx, system_prompt="SYS")
    agent.ctx.emit.listeners.append(lambda t, d: events.append((t, d)))
    result = await agent.run("go")
    assert result.status == "done" and result.text == "All done."

    first = recorder.requests[0]
    assert first["model"] == "claude-opus-5" and first["max_tokens"] == 64000
    assert first["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert first["output_config"] == {"effort": "high"}
    assert first["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in recorder.headers[0].get("anthropic-beta", "")
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["tools"][0]["eager_input_streaming"] is True

    second = recorder.requests[1]
    replayed = second["messages"][1]["content"]
    assert replayed[0]["type"] == "thinking" and replayed[0]["signature"] == "sig123"
    assert second["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "toolu_1",
                                                   "content": [{"type": "text", "text": "echo:hi"}],
                                                   "is_error": False}
    types = [t for t, _ in events]
    assert "llm.reasoning.delta" in types and "llm.text.delta" in types and "tool.call.start" in types
    response_event = next(d for t, d in events if t == "llm.response")
    assert response_event["usage"]["cache_read_tokens"] == 50
    assert response_event["cost"] > 0
    await provider.aclose()


async def test_anthropic_refusal_and_errors():
    recorder = Recorder([anthropic_turn([], "refusal")])
    provider = AnthropicProvider(anthropic_profile(), "k", http_client=httpx2.AsyncClient(
        transport=httpx2.MockTransport(recorder)))
    response = await provider.generate(system="s", messages=[user_text("x")], tools=[])
    assert response.stop == "refusal"

    def unauthorized(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad key"}})

    provider = AnthropicProvider(anthropic_profile(), "k", http_client=httpx2.AsyncClient(
        transport=httpx2.MockTransport(unauthorized)))
    with pytest.raises(LLMError) as err:
        await provider.generate(system="s", messages=[user_text("x")], tools=[])
    assert err.value.kind == "auth" and not err.value.retryable


# ---- OpenAI ----------------------------------------------------------------------


def openai_chunks(parts: list[dict], usage: dict | None = None, model: str = "gpt-test") -> bytes:
    events = []
    for part in parts:
        events.append((None, {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": model,
                              "choices": [{"index": 0, "delta": part.get("delta", {}),
                                           "finish_reason": part.get("finish")}]}))
    if usage:
        events.append((None, {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": model,
                              "choices": [], "usage": usage}))
    events.append((None, "[DONE]"))
    return sse(events)


def test_openai_message_conversion():
    profile = profile_from_preset("deepseek")
    assistant = Message(role="assistant", content=[ReasoningBlock(text="why"), TextBlock(text="t"),
                                                    ToolCallBlock(id="c1", name="echo", input={"text": "a"})])
    results = Message(role="user", content=[ToolResultBlock(call_id="c1", content=[TextBlock(text="r"),
                                                            ImageBlock(data="QUJD")])])
    out = to_openai_messages("SYS", [user_text("q"), assistant, results], profile)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[2]["tool_calls"][0]["function"] == {"name": "echo", "arguments": '{"text": "a"}'}
    assert out[2]["reasoning_content"] == "why"  # DeepSeek preset echoes reasoning
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "r"}
    assert "不支持图像" in out[4]["content"]  # image dropped for a text-only model

    vision = profile_from_preset("openai", model="gpt-x")
    out = to_openai_messages("SYS", [user_text("q"), assistant, results], vision)
    assert "reasoning_content" not in out[2]
    assert out[4]["content"][1]["type"] == "image_url"


async def test_openai_stream_accumulates_tool_calls_and_usage(workspace):
    body1 = openai_chunks([
        {"delta": {"role": "assistant", "content": "Let me "}},
        {"delta": {"content": "check."}},
        {"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                   "function": {"name": "echo", "arguments": ""}}]}},
        {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"text\":"}}]}},
        {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"hi\"}"}}]}},
        {"delta": {}, "finish": "tool_calls"},
    ], usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
              "prompt_tokens_details": {"cached_tokens": 40}})
    body2 = openai_chunks([{"delta": {"content": "Done."}}, {"delta": {}, "finish": "stop"}],
                          usage={"prompt_tokens": 150, "completion_tokens": 5, "total_tokens": 155})
    recorder = Recorder([body1, body2])
    profile = profile_from_preset("openai", model="gpt-test")
    provider = OpenAIProvider(profile, "sk", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)))
    agent = Agent(provider=provider, tools=ToolRegistry([echo]), ctx=ToolContext(workspace=workspace),
                  system_prompt="SYS")
    result = await agent.run("go")
    assert result.status == "done" and result.text == "Done."
    assert agent.messages[1].tool_calls()[0].input == {"text": "hi"}
    req1, req2 = recorder.requests
    assert req1["stream_options"] == {"include_usage": True} and req1["tool_choice"] == "auto"
    assert req2["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert req2["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "echo:hi"}
    assert result.usage.cache_read_tokens == 40 and result.usage.input_tokens == 60 + 150


async def test_openai_drops_unsupported_optional_params_and_bad_json():
    body = openai_chunks([
        {"delta": {"tool_calls": [{"index": 0, "id": "c9", "type": "function",
                                   "function": {"name": "echo", "arguments": "{not json"}}]}},
        {"delta": {}, "finish": "tool_calls"},
    ])
    recorder = Recorder([body], status=[400, 200])
    profile = profile_from_preset("custom", model="m", base_url="http://localhost:9/v1")
    provider = OpenAIProvider(profile, None, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)))
    response = await provider.generate(system="s", messages=[user_text("x")],
                                       tools=[ToolSpec(name="echo", description="e", input_schema={"type": "object"})])
    assert "stream_options" in recorder.requests[0] and "stream_options" not in recorder.requests[1]
    call = response.message.tool_calls()[0]
    assert call.input_error and response.stop == "tool_use"
    assert response.message.meta.get("usage_estimated")
