"""Native Ollama adapter: num_ctx per request, NDJSON streaming, thinking, tool calls, capability detection."""

from __future__ import annotations

import json

import httpx2
import pytest

from agent4sci.core.models import profile_from_preset
from agent4sci.llm import create_provider
from agent4sci.llm.base import LLMError
from agent4sci.llm.ollama_provider import OllamaProvider, to_ollama_messages
from agent4sci.llm.probe import test_connection as probe_connection
from agent4sci.llm.types import ImageBlock, Message, TextBlock, ToolCallBlock, ToolResultBlock, ToolSpec, user_text

TOOL = ToolSpec(name="list_files", description="List files", input_schema={"type": "object", "properties": {}})


def ndjson(*chunks: dict) -> bytes:
    return b"".join(json.dumps(c).encode() + b"\n" for c in chunks)


def mock_ollama(capabilities: list[str], chunks: list[dict], seen: list[dict], status: int = 200):
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path == "/api/show":
            return httpx2.Response(200, json={"capabilities": capabilities, "details": {"family": "qwen3"}})
        seen.append(body)
        if status != 200:
            return httpx2.Response(status, json={"error": f"model '{body['model']}' not found"})
        return httpx2.Response(200, content=ndjson(*chunks))
    return handler


def provider_for(handler, **overrides) -> OllamaProvider:
    profile = profile_from_preset("ollama", model="qwen3:0.6b", **overrides)
    return OllamaProvider(profile, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


def test_preset_uses_native_api():
    profile = profile_from_preset("ollama", model="qwen3:0.6b")
    assert profile.provider == "ollama" and profile.base_url == "http://localhost:11434"
    assert isinstance(create_provider(profile, None), OllamaProvider)
    # an OpenAI-style base URL still works
    assert OllamaProvider(profile_from_preset("ollama", model="x", base_url="http://h:11434/v1/")).base == "http://h:11434"


async def test_streams_thinking_text_and_tool_calls_with_num_ctx():
    seen: list[dict] = []
    chunks = [
        {"message": {"role": "assistant", "content": "", "thinking": "I should list "}, "done": False},
        {"message": {"role": "assistant", "content": "", "thinking": "the files."}, "done": False},
        {"message": {"role": "assistant", "content": "Let me look.",
                     "tool_calls": [{"function": {"name": "list_files", "arguments": {"path": "."}}}]}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
         "prompt_eval_count": 812, "eval_count": 41, "model": "qwen3:0.6b"},
    ]
    events = []
    provider = provider_for(mock_ollama(["completion", "tools", "thinking"], chunks, seen), context_window=12288)
    resp = await provider.generate(system="sys", messages=[user_text("hi")], tools=[TOOL], on_stream=events.append)
    body = seen[0]
    assert body["options"]["num_ctx"] == 12288 and body["think"] is True and body["tools"][0]["function"]["name"] == "list_files"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert resp.stop == "tool_use" and resp.message.reasoning() == "I should list the files."
    assert resp.message.text() == "Let me look." and resp.message.tool_calls()[0].input == {"path": "."}
    assert resp.usage.input_tokens == 812 and resp.usage.output_tokens == 41
    # every NDJSON line also emits a keepalive so the agent loop can tell "thinking" from "connection dead"
    assert [e.kind for e in events if e.kind != "keepalive"] == ["reasoning", "reasoning", "text", "tool_call_start"]
    assert sum(1 for e in events if e.kind == "keepalive") >= 4


async def test_follows_model_capabilities_and_inline_think_tags():
    seen: list[dict] = []
    chunks = [{"message": {"role": "assistant", "content": "<think>hmm</think>\n\nOK"}, "done": True,
               "done_reason": "length", "prompt_eval_count": 5, "eval_count": 3}]
    provider = provider_for(mock_ollama(["completion"], chunks, seen))
    resp = await provider.generate(system="s", messages=[user_text("hi")], tools=[TOOL])
    assert "tools" not in seen[0] and "think" not in seen[0]  # would be a 400 for this model
    assert resp.message.text() == "OK" and resp.message.reasoning() == "hmm" and resp.stop == "max_tokens"


async def test_missing_model_and_connection_errors_are_actionable():
    provider = provider_for(mock_ollama(["completion"], [], [], status=404))
    with pytest.raises(LLMError) as err:
        await provider.generate(system="s", messages=[user_text("hi")], tools=[])
    assert err.value.kind == "not_found" and "ollama pull qwen3:0.6b" in str(err.value)

    def refuse(request):
        raise httpx2.ConnectError("refused")

    with pytest.raises(LLMError) as err:
        await provider_for(refuse).generate(system="s", messages=[user_text("hi")], tools=[])
    assert err.value.kind == "connection" and "Ollama" in str(err.value)


def test_history_conversion():
    msgs = [
        user_text("look at this", ),
        Message(role="assistant", content=[TextBlock(text="calling"),
                                           ToolCallBlock(id="c1", name="list_files", input={"path": "."})]),
        Message(role="user", content=[ToolResultBlock(call_id="c1", name="list_files",
                                                      content=[TextBlock(text="a.txt"), ImageBlock(data="QUJD")])]),
    ]
    out = to_ollama_messages("sys", msgs, vision=True)
    assert out[2] == {"role": "assistant", "content": "calling",
                      "tool_calls": [{"function": {"name": "list_files", "arguments": {"path": "."}}}]}
    assert out[3] == {"role": "tool", "tool_name": "list_files", "content": "a.txt"}
    assert out[4]["images"] == ["QUJD"]
    no_vision = to_ollama_messages("sys", msgs, vision=False)
    assert "images" not in no_vision[-1] and "不支持图像" in no_vision[-1]["content"]


async def test_probe_reports_capabilities_and_tool_support(monkeypatch):
    chunks = [{"message": {"role": "assistant", "content": "OK"}, "done": True, "done_reason": "stop"}]
    handler = mock_ollama(["completion", "thinking"], chunks, [])
    monkeypatch.setattr("agent4sci.llm.probe.create_provider",
                        lambda profile, key: OllamaProvider(profile, http_client=httpx2.AsyncClient(
                            transport=httpx2.MockTransport(handler))))
    report = await probe_connection(profile_from_preset("ollama", model="deepseek-r1:1.5b"), None)
    assert report["ok"] and report["tools_ok"] is False and "不支持工具" in report["tools_note"]
    assert report["model_capabilities"] == ["completion", "thinking"] and report["context"] == 16384
