from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent4sci.agent.agent import Agent, AgentConfig
from agent4sci.agent.budget import Budget
from agent4sci.agent.context import SUMMARY_PROMPT
from agent4sci.agent.interactions import InteractionBroker
from agent4sci.core.events import Emitter
from agent4sci.core.models import ModelProfile
from agent4sci.llm.fake_provider import FakeProvider, FakeTurn, demo_brain, example_from_schema
from agent4sci.llm.types import Message, StreamEvent, TextBlock, ToolCallBlock, ToolResultBlock, user_text
from agent4sci.tools.base import ToolContext, tool
from agent4sci.tools.registry import ToolRegistry
from agent4sci.tools.result import ResultSink, SubmitResultTool


class EchoArgs(BaseModel):
    text: str


class PathArgs(BaseModel):
    path: str


class SleepArgs(BaseModel):
    seconds: float


class WriteArgs(BaseModel):
    path: str
    content: str


@tool("echo", "Echo text back.", parallel_safe=True)
async def echo(args: EchoArgs, ctx: ToolContext):
    return f"echo:{args.text}"


@tool("read", "Read a file.", category="fs.read", parallel_safe=True)
async def read(args: PathArgs, ctx: ToolContext):
    return ctx.resolve(args.path).read_text()


@tool("sleep", "Sleep.", parallel_safe=True)
async def sleep(args: SleepArgs, ctx: ToolContext):
    await asyncio.sleep(args.seconds)
    return "slept"


@tool("big", "Return a lot of text.")
async def big(args: EchoArgs, ctx: ToolContext):
    return "x" * 50_000


@tool("danger", "Run something risky.", category="exec.shell")
async def danger(args: EchoArgs, ctx: ToolContext):
    return "ran"


@tool("list_files", "List files.", category="fs.read", parallel_safe=True)
async def list_files(args: PathArgs, ctx: ToolContext):
    return "\n".join(sorted(p.name for p in ctx.resolve(args.path).iterdir()))


@tool("write_file", "Write a file.", category="fs.write")
async def write_file(args: WriteArgs, ctx: ToolContext):
    target = ctx.resolve(args.path, write=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.content)
    return "written"


TOOLS = [echo, read, sleep, big, danger, list_files, write_file]


def make_agent(workspace: Path, provider: FakeProvider, *, tools=TOOLS, config=None, history=None,
               budget=None, interactions=None, auto_mode=False, services=None, events=None):
    emitter = Emitter(None)
    if events is not None:
        emitter.listeners.append(lambda t, d: events.append((t, d)))
    ctx = ToolContext(workspace=workspace, emit=emitter, interactions=interactions, auto_mode=auto_mode,
                      services=services or {})
    return Agent(provider=provider, tools=ToolRegistry(tools), ctx=ctx, system_prompt="SYS",
                 config=config or AgentConfig(), history=history, budget=budget)


async def test_tool_call_then_answer(workspace):
    provider = FakeProvider(script=[FakeTurn(text="calling", tool_calls=[("echo", {"text": "hi"})]),
                                    FakeTurn(text="done")])
    events: list = []
    agent = make_agent(workspace, provider, events=events)
    result = await agent.run("please echo")
    assert result.status == "done" and result.text == "done" and result.turns == 2
    roles = [m.role for m in agent.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    tool_result = agent.messages[2].content[0]
    assert isinstance(tool_result, ToolResultBlock) and tool_result.text() == "echo:hi"
    types = [t for t, _ in events]
    for expected in ("agent.start", "llm.request", "llm.text.delta", "llm.response", "tool.call",
                     "tool.result", "agent.message", "agent.done"):
        assert expected in types
    assert provider.calls[1]["messages"][-1].tool_results()[0].call_id == agent.messages[1].tool_calls()[0].id


async def test_parallel_safe_tools_run_concurrently_and_results_share_one_message(workspace):
    provider = FakeProvider(script=[
        FakeTurn(tool_calls=[("sleep", {"seconds": 0.3}), ("sleep", {"seconds": 0.3}), ("echo", {"text": "a"})]),
        FakeTurn(text="ok"),
    ])
    agent = make_agent(workspace, provider)
    started = time.monotonic()
    await agent.run("go")
    assert time.monotonic() - started < 0.55
    results = agent.messages[2].tool_results()
    assert len(results) == 3 and [r.text() for r in results] == ["slept", "slept", "echo:a"]


async def test_unknown_tool_and_invalid_args_become_error_results(workspace):
    provider = FakeProvider(script=[FakeTurn(tool_calls=[("nope", {}), ("echo", {})]), FakeTurn(text="ok")])
    agent = make_agent(workspace, provider)
    result = await agent.run("go")
    assert result.status == "done"
    r1, r2 = agent.messages[2].tool_results()
    assert r1.is_error and "未知工具" in r1.text()
    assert r2.is_error and "参数校验失败" in r2.text()


async def test_max_tokens_continuation_and_refusal(workspace):
    provider = FakeProvider(script=[FakeTurn(text="part1", stop="max_tokens"), FakeTurn(text="part2")])
    agent = make_agent(workspace, provider)
    result = await agent.run("write")
    assert result.text == "part2"
    assert agent.messages[2].meta.get("kind") == "continue"

    provider = FakeProvider(script=[FakeTurn(text="", stop="refusal")])
    result = await make_agent(workspace, provider).run("bad")
    assert result.status == "refused"


async def test_truncated_tool_call_is_not_executed(workspace):
    provider = FakeProvider(script=[FakeTurn(tool_calls=[("write_file", {"path": "a.txt", "content": "x"})],
                                             stop="max_tokens"), FakeTurn(text="ok")])
    agent = make_agent(workspace, provider)
    await agent.run("go")
    assert not (workspace / "a.txt").exists()
    assert agent.messages[2].tool_results()[0].is_error


async def test_budget_wrap_up_then_stop(workspace):
    provider = FakeProvider(brain=lambda s, m, t: FakeTurn(tool_calls=[("echo", {"text": "x"})]), cost_per_turn=0.1)
    agent = make_agent(workspace, provider, budget=Budget(0.25))
    result = await agent.run("loop forever")
    assert result.status == "budget"
    assert result.cost_usd == pytest.approx(0.3)
    wrap = [b for m in agent.messages for b in m.content if isinstance(b, TextBlock) and "预算即将用完" in b.text]
    assert len(wrap) == 1


async def test_long_output_truncated_and_saved(workspace):
    provider = FakeProvider(script=[FakeTurn(tool_calls=[("big", {"text": "a"})]), FakeTurn(text="ok")])
    agent = make_agent(workspace, provider, config=AgentConfig(tool_output_limit=1000))
    await agent.run("go")
    text = agent.messages[2].tool_results()[0].text()
    assert "已截断" in text and len(text) < 1300
    saved = list((workspace / ".a4s" / "outputs").glob("*.txt"))
    assert saved and len(saved[0].read_text()) == 50_000


async def test_path_confinement_blocks_traversal_and_symlink_escape(workspace, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    try:
        (workspace / "link.txt").symlink_to(secret)
    except OSError:
        pytest.skip("creating symlinks needs admin rights or Developer Mode on Windows")
    provider = FakeProvider(script=[FakeTurn(tool_calls=[("read", {"path": "../secret.txt"}),
                                                         ("read", {"path": "link.txt"}),
                                                         ("write_file", {"path": ".a4s/x", "content": "y"})]),
                                    FakeTurn(text="ok")])
    agent = make_agent(workspace, provider)
    await agent.run("go")
    results = agent.messages[2].tool_results()
    assert all(r.is_error for r in results)
    assert "TOP SECRET" not in "".join(r.text() for r in results)


async def test_approvals_ask_allow_deny_and_auto_mode(workspace):
    answers = iter([{"decision": "allow"}, {"decision": "deny", "note": "不行"}])
    asked = []

    def handler(interaction):
        asked.append(interaction)
        return next(answers)

    broker = InteractionBroker(handler=handler)
    provider = FakeProvider(script=[FakeTurn(tool_calls=[("danger", {"text": "1"})]),
                                    FakeTurn(tool_calls=[("danger", {"text": "2"})]),
                                    FakeTurn(text="ok")])
    agent = make_agent(workspace, provider, config=AgentConfig(approvals={"exec": "ask"}), interactions=broker)
    await agent.run("go")
    assert agent.messages[2].tool_results()[0].text() == "ran"
    denied = agent.messages[4].tool_results()[0]
    assert denied.is_error and "不行" in denied.text()
    assert asked[0].kind == "approval" and asked[0].payload["tool"] == "danger"

    provider = FakeProvider(script=[FakeTurn(tool_calls=[("danger", {"text": "1"})]), FakeTurn(text="ok")])
    agent = make_agent(workspace, provider, config=AgentConfig(approvals={"exec": "ask"}), interactions=broker,
                       auto_mode=True)
    await agent.run("go")
    assert "全自动" in agent.messages[2].tool_results()[0].text()

    provider = FakeProvider(script=[FakeTurn(tool_calls=[("danger", {"text": "1"})]), FakeTurn(text="ok")])
    agent = make_agent(workspace, provider, config=AgentConfig(approvals={"exec.shell": "deny", "exec": "ask"}))
    await agent.run("go")
    assert "禁止" in agent.messages[2].tool_results()[0].text()


async def test_interaction_broker_resolves_pending_requests():
    broker = InteractionBroker()
    task = asyncio.create_task(broker.ask("question", "Which?", {"options": ["a", "b"]}, topics=["conv:1"]))
    await asyncio.sleep(0.01)
    pending = broker.pending("conv:1")
    assert len(pending) == 1
    assert broker.resolve(pending[0].id, {"answer": "b"})
    assert (await task) == {"answer": "b"}
    assert broker.pending() == []


async def test_steering_is_appended_after_tool_results(workspace):
    provider = FakeProvider(script=[FakeTurn(tool_calls=[("echo", {"text": "x"})]), FakeTurn(text="ok")])
    agent = make_agent(workspace, provider)
    agent.steer("also check y")
    await agent.run("go")
    last = provider.calls[1]["messages"][-1]
    assert isinstance(last.content[0], ToolResultBlock)
    assert any(isinstance(b, TextBlock) and "also check y" in b.text for b in last.content)


async def test_compaction_restarts_from_summary(workspace):
    profile = ModelProfile(name="tiny", provider="fake", model="demo", context_window=1000)
    long = "y" * 3000
    provider = FakeProvider(profile=profile, script=[
        FakeTurn(tool_calls=[("echo", {"text": long})]),
        FakeTurn(tool_calls=[("echo", {"text": "again"})]),
        FakeTurn(text="SUMMARY OF WORK"),
        FakeTurn(text="done"),
    ])
    events: list = []
    agent = make_agent(workspace, provider, config=AgentConfig(compact_ratio=0.5), events=events)
    result = await agent.run("long task")
    assert result.status == "done" and result.text == "done"
    assert "agent.compaction" in [t for t, _ in events]
    first = provider.calls[3]["messages"][0]
    assert "<context_summary>" in first.text() and "SUMMARY OF WORK" in first.text()
    assert agent.messages[0].meta["kind"] == "summary"


async def test_submit_result_validates_and_stops(workspace):
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    sink = ResultSink(schema)
    provider = FakeProvider(script=[
        FakeTurn(tool_calls=[("submit_result", {"result": {}, "summary": "s"})]),
        FakeTurn(tool_calls=[("submit_result", {"result": {"answer": "42"}, "summary": "s"})]),
        FakeTurn(text="should not be called"),
    ])
    agent = make_agent(workspace, provider, tools=[echo, SubmitResultTool(schema)],
                       config=AgentConfig(stop_on_submit=True), services={"result_sink": sink})
    result = await agent.run("answer")
    assert result.status == "done" and result.submitted == {"answer": "42"}
    assert len(provider.calls) == 2
    assert "不符合" in agent.messages[2].tool_results()[0].text()


async def test_interrupted_history_is_repaired_by_appending(workspace):
    history = [user_text("start"),
               Message(role="assistant", content=[ToolCallBlock(id="c1", name="echo", input={"text": "a"})])]
    provider = FakeProvider(script=[FakeTurn(text="resumed")])
    agent = make_agent(workspace, provider, history=history)
    await agent.run("continue")
    sent = provider.calls[0]["messages"]
    assert sent[2].tool_results()[0].call_id == "c1" and sent[2].meta["kind"] == "repair"
    assert sent[3].text() == "continue"


async def test_demo_brain_chat_and_submit(workspace):
    (workspace / "data.csv").write_text("a,b\n")
    provider = FakeProvider(brain=demo_brain)
    agent = make_agent(workspace, provider)
    result = await agent.run("研究一下这个数据")
    assert result.status == "done" and "演示模式" in result.text
    assert (workspace / "notes" / "demo-notes.md").exists()

    schema = {"type": "object", "properties": {"decision": {"enum": ["reject", "accept"]},
                                               "score": {"type": "number"}}, "required": ["decision"]}
    sink = ResultSink(schema)
    agent = make_agent(workspace, FakeProvider(brain=demo_brain), tools=[SubmitResultTool(schema)],
                       config=AgentConfig(stop_on_submit=True), services={"result_sink": sink})
    result = await agent.run("review")
    assert result.submitted == {"decision": "accept", "score": 0.9}


def test_example_from_schema_handles_refs_and_arrays():
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/I"}}},
              "$defs": {"I": {"type": "object", "properties": {"n": {"type": "integer"}}}}}
    assert example_from_schema(schema) == {"items": [{"n": 1}]}


async def test_models_without_prices_report_unknown_cost_but_local_ones_are_free(workspace):
    from agent4sci.core.models import ModelProfile

    remote = ModelProfile(id="r", name="remote", provider="openai-compatible", model="some-model",
                          base_url="https://api.example.com")
    events: list = []
    agent = make_agent(workspace, FakeProvider(remote, script=[FakeTurn(text="hi")], cost_per_turn=None),
                       events=events)
    result = await agent.run("go")
    assert result.cost_unknown and result.cost_usd == 0
    response = next(d for t, d in events if t == "llm.response")
    assert response["cost"] is None
    assert next(d for t, d in events if t == "agent.done")["cost_unknown"] is True

    local = ModelProfile(id="l", name="local", provider="ollama", model="qwen3:0.6b", base_url="http://localhost:11434")
    agent = make_agent(workspace, FakeProvider(local, script=[FakeTurn(text="hi")], cost_per_turn=None))
    assert (await agent.run("go")).cost_unknown is False


async def test_turn_limit_and_unlimited_turns_stopped_by_the_budget(workspace):
    keep_calling = FakeProvider(brain=lambda *_: FakeTurn(tool_calls=[("echo", {"text": "a"})]))
    agent = make_agent(workspace, keep_calling, config=AgentConfig(max_turns=3))
    result = await agent.run("go")
    assert result.status == "max_turns" and result.turns == 3 and result.text == ""

    spendy = FakeProvider(brain=lambda *_: FakeTurn(tool_calls=[("echo", {"text": "a"})]), cost_per_turn=0.4)
    agent = make_agent(workspace, spendy, config=AgentConfig(max_turns=0), budget=Budget(1.0))
    result = await agent.run("go")  # no turn limit: only the budget can stop it
    assert result.status == "budget" and result.turns > 3


async def test_a_dead_connection_is_aborted_but_a_thinking_model_is_not(workspace):
    events: list = []
    stalling = FakeProvider(script=[FakeTurn(text="starts then goes quiet")], delay=3.0)
    agent = make_agent(workspace, stalling, events=events,
                       config=AgentConfig(stream_idle_timeout_s=0.2, llm_retries=0))
    started = time.monotonic()
    result = await agent.run("go")
    assert time.monotonic() - started < 2.5
    assert result.status == "error" and "没有任何响应" in (result.error or "")
    assert any(t == "llm.stalled" for t, _ in events)

    class Thinking(FakeProvider):
        """Long silent reasoning, but the connection keeps sending keep-alives."""

        async def generate(self, **kw):
            for _ in range(8):
                if kw.get("on_stream"):
                    kw["on_stream"](StreamEvent(kind="keepalive"))
                await asyncio.sleep(0.1)
            return await super().generate(**kw)

    patient = Thinking(script=[FakeTurn(text="thought about it")])
    agent = make_agent(workspace, patient, config=AgentConfig(stream_idle_timeout_s=0.25, llm_retries=0))
    result = await agent.run("go")
    assert result.status == "done" and result.text == "thought about it"


def test_notices_are_shown_to_the_user_but_never_sent_to_the_model():
    from agent4sci.agent.context import context_from_history, notice_message

    history = [user_text("hi"), notice_message("⚠ 已达到最大轮数"), user_text("继续")]
    assert [m.role for m in context_from_history(history)] == ["user", "user"]
    assert notice_message("x").meta["kind"] == "notice"


async def test_a_prompt_too_big_for_the_window_fails_before_spending_anything(workspace):
    from agent4sci.core.models import ModelProfile

    tiny = ModelProfile(id="t", name="tiny", provider="openai-compatible", model="m",
                        base_url="https://api.example.com", context_window=800)
    provider = FakeProvider(tiny, script=[FakeTurn(text="never asked")])
    agent = Agent(provider=provider, tools=ToolRegistry(TOOLS), ctx=ToolContext(workspace=workspace),
                  system_prompt="x" * 6000)
    result = await agent.run("go")
    assert result.status == "error" and "上下文只有 800" in (result.error or "")
    assert provider.calls == []  # not a single request was sent


async def test_a_long_stored_history_is_compacted_before_the_first_request(workspace):
    history = []
    for i in range(12):
        history.append(user_text("问题 " + "详细背景 " * 400))
        history.append(Message(role="assistant", content=[TextBlock(text="回答 " + "分析 " * 400)]))
    from agent4sci.core.models import ModelProfile

    small = ModelProfile(id="s", name="small", provider="ollama", model="qwen3:0.6b",
                         base_url="http://localhost:11434", context_window=16_384)
    provider = FakeProvider(small, script=[FakeTurn(text="摘要"), FakeTurn(text="继续工作")])
    agent = make_agent(workspace, provider, history=history,
                       config=AgentConfig(compact_ratio=0.5, max_turns=2))
    await agent.run("接着做")
    # the first provider call is the compaction request, not a turn that would have overflowed the window
    assert SUMMARY_PROMPT in provider.calls[0]["messages"][-1].text()
