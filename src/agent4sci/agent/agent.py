"""The agent loop (the "harness" core).

History is append-only: every request resends the full context, new turns are
only ever appended, and when the context gets too long we restart from a
summary instead of editing earlier turns.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from ..core.events import Emitter
from ..llm.base import LLMError, LLMProvider
from ..llm.pricing import compute_cost, resolve_pricing
from ..llm.types import (
    ImageBlock,
    LLMResponse,
    Message,
    StreamEvent,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    Usage,
    user_text,
)
from ..tools.base import Tool, ToolContext, ToolError, ToolInputError, ToolResult
from ..tools.registry import ToolRegistry
from .budget import Budget
from .context import (SUMMARY_PROMPT, context_from_history, fixed_context_tokens, history_tokens,
                      message_tokens, repair_history, summary_message, truncate_output)
from .interactions import InteractionCancelled

log = logging.getLogger(__name__)

ApprovalMode = Literal["auto", "ask", "deny"]
AgentStatus = Literal["done", "refused", "max_turns", "budget", "cancelled", "error"]

MAX_IMAGES_PER_RESULT = 4


class AgentConfig(BaseModel):
    name: str = "agent"
    max_turns: int = 60  # 0 = no limit (only the budget, the stop button and the model end the run)
    effort: str | None = None
    max_tokens: int | None = None
    tool_output_limit: int = 20_000
    compact_ratio: float = 0.75
    tool_timeout_s: float = 900.0
    # Abort and retry when the connection delivers nothing at all (no chunk, no ping) for this long. A model
    # that thinks for minutes still sends keep-alives, so this only catches a dead connection. 0 = off.
    stream_idle_timeout_s: float = 300.0
    approvals: dict[str, ApprovalMode] = Field(default_factory=dict)  # tool category prefix → mode
    default_approval: ApprovalMode = "auto"
    stop_on_submit: bool = False
    llm_retries: int = 3


class AgentResult(BaseModel):
    status: AgentStatus
    text: str = ""
    turns: int = 0
    tool_calls: int = 0
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    cost_unknown: bool = False  # the model has no pricing, so cost_usd undercounts
    budget_wrapped: bool = False  # told to wrap up early because the budget ran low: output may be incomplete
    error: str | None = None
    submitted: Any = None


class Agent:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        tools: ToolRegistry,
        ctx: ToolContext,
        system_prompt: str,
        config: AgentConfig | None = None,
        history: list[Message] | None = None,
        budget: Budget | None = None,
        emit: Emitter | None = None,
        on_message: Callable[[Message], None] | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.ctx = ctx
        self.system_prompt = system_prompt
        self.config = config or AgentConfig()
        self.budget = budget or Budget()
        self.emit = emit or ctx.emit
        self.on_message = on_message
        self.messages: list[Message] = context_from_history(history or [])
        self.tool_specs = tools.specs()
        self.usage = Usage()
        self.cost = 0.0
        self.cost_unknown = False
        self.activated_skills: list[str] = []
        self._effort = self.config.effort or provider.profile.effort
        self._steer: list[str] = []
        self._cancel = asyncio.Event()
        self._last_context_tokens = 0
        self._last_stream_at = 0.0
        # Sizes we can know before asking the provider, so the first request is not a shot in the dark.
        self._fixed_tokens = fixed_context_tokens(self.system_prompt, self.tool_specs)
        self._history_tokens = history_tokens(self.messages)

    # ---- external control ----------------------------------------------------
    def steer(self, text: str) -> None:
        """Queue a user message that is appended at the next turn boundary."""
        self._steer.append(text)

    def cancel(self) -> None:
        self._cancel.set()

    def set_effort(self, effort: str | None) -> None:
        self._effort = effort

    # ---- helpers ---------------------------------------------------------------
    def _append(self, message: Message) -> None:
        self.messages.append(message)
        self._history_tokens += message_tokens(message)
        if self.on_message is not None:
            self.on_message(message)
        self.emit("agent.message", message=message.model_dump(mode="json", exclude={"raw"}))

    def _steer_blocks(self) -> list[TextBlock]:
        blocks = [TextBlock(text=f"（用户插话）{text}") for text in self._steer]
        self._steer.clear()
        return blocks

    def _approval_mode(self, category: str) -> ApprovalMode:
        best, mode = -1, self.config.default_approval
        for prefix, value in self.config.approvals.items():
            if (category == prefix or category.startswith(prefix + ".")) and len(prefix) > best:
                best, mode = len(prefix), value
        return mode

    # ---- main loop -------------------------------------------------------------
    async def run(self, user_input: str | Message | None = None) -> AgentResult:
        repaired = repair_history(self.messages)
        if len(repaired) != len(self.messages):
            self._append(repaired[-1])
        if user_input is not None:
            message = user_text(user_input) if isinstance(user_input, str) else user_input
            self._append(message)

        profile = self.provider.profile
        self.emit("agent.start", name=self.config.name, model=profile.model, model_name=profile.name,
                  provider=profile.provider, max_turns=self.config.max_turns)
        too_big = self._context_too_big(profile)
        if too_big is not None:
            self.emit("agent.done", status="error", turns=0, tool_calls=0, cost=0.0, error=too_big)
            return AgentResult(status="error", error=too_big)
        status: AgentStatus = "max_turns"
        text, error, turns, n_calls = "", None, 0, 0
        wrap_up_sent = False
        sink = self.ctx.services.get("result_sink")
        turn = 0
        try:
            while self.config.max_turns <= 0 or turn < self.config.max_turns:
                turn += 1
                turns = turn
                if self._cancel.is_set():
                    status = "cancelled"
                    break
                if self.budget.exceeded():
                    status = "budget"
                    break
                if self._needs_compaction():
                    await self._compact()

                response = await self._call_llm(turn)
                self._append(response.message)
                calls = response.message.tool_calls()

                if response.stop == "refusal":
                    status, text = "refused", response.message.text() or "模型拒绝了这个请求。"
                    break
                if response.stop == "pause" and not calls:
                    continue  # server-side tool paused; resending resumes the turn
                if not calls:
                    if response.stop == "max_tokens":
                        self._append(user_text("（上一条回复因长度上限被截断，请接着写完。若要写大文件，请分多次写入。）",
                                               kind="continue"))
                        continue
                    if self._steer:
                        self._append(Message(role="user", content=list(self._steer_blocks())))
                        continue
                    status, text = "done", response.message.text()
                    break

                results = await self._run_tools(calls, truncated=response.stop == "max_tokens")
                n_calls += len(calls)
                extra: list[TextBlock] = self._steer_blocks() if self._steer else []
                if self.budget.nearly_exhausted() and not wrap_up_sent:
                    extra.append(TextBlock(text="（预算即将用完：请立即收尾，总结已完成的工作和结论，不要再调用工具。）"))
                    wrap_up_sent = True
                self._append(Message(role="user", content=[*results, *extra]))

                if self.config.stop_on_submit and sink is not None and sink.submitted:
                    status, text = "done", response.message.text()
                    break
        except asyncio.CancelledError:
            self.emit("agent.done", status="cancelled", turns=turns, cost=self.cost, cost_unknown=self.cost_unknown)
            raise
        except LLMError as exc:
            status, error = "error", f"{exc.kind}: {exc}"
            self.emit("agent.error", error=error)
        except Exception as exc:  # keep the harness alive; report the failure
            log.exception("agent crashed")
            status, error = "error", f"{type(exc).__name__}: {exc}"
            self.emit("agent.error", error=error)

        result = AgentResult(
            status=status, text=text, turns=turns, tool_calls=n_calls, usage=self.usage,
            cost_usd=round(self.cost, 6), cost_unknown=self.cost_unknown, budget_wrapped=wrap_up_sent, error=error,
            submitted=sink.value if sink is not None and sink.submitted else None,
        )
        self.emit("agent.done", status=status, turns=turns, tool_calls=n_calls, cost=result.cost_usd,
                  cost_unknown=self.cost_unknown, usage=self.usage.model_dump(), error=error,
                  text_preview=text[:500])
        return result

    # ---- LLM call ----------------------------------------------------------------
    async def _call_llm(self, turn: int, *, messages: list[Message] | None = None, purpose: str = "turn") -> LLMResponse:
        profile = self.provider.profile
        self.emit("llm.request", turn=turn, purpose=purpose, model=profile.model, n_messages=len(self.messages))

        def on_stream(ev: StreamEvent) -> None:
            self._last_stream_at = time.monotonic()
            if purpose != "turn":
                return
            if ev.kind == "text":
                self.emit("llm.text.delta", text=ev.text)
            elif ev.kind == "reasoning":
                self.emit("llm.reasoning.delta", text=ev.text)
            elif ev.kind == "tool_call_start":
                self.emit("tool.call.start", id=ev.call_id, name=ev.name)

        started = time.monotonic()
        attempt = 0
        while True:
            try:
                response = await self._generate(messages if messages is not None else self.messages, on_stream)
                break
            except LLMError as exc:
                attempt += 1
                if not exc.retryable or attempt > self.config.llm_retries:
                    raise
                delay = min(60.0, 2.0 ** (attempt + 1))
                self.emit("llm.retry", attempt=attempt, delay_s=delay, error=str(exc), kind=exc.kind)
                await asyncio.sleep(delay)

        response.latency_s = time.monotonic() - started
        if response.usage.cost_usd is None:
            response.usage.cost_usd = compute_cost(resolve_pricing(profile, response.model), response.usage)
        known = response.usage.cost_usd is not None
        if not known and (response.usage.input_tokens or response.usage.output_tokens
                          or response.usage.cache_read_tokens):
            self.cost_unknown = True
        cost = response.usage.cost_usd or 0.0
        self.usage.add(response.usage)
        self.cost += cost
        self.budget.charge(cost, response.usage)
        self._last_context_tokens = response.usage.context_tokens + response.usage.output_tokens
        self.emit(
            "llm.response",
            turn=turn,
            purpose=purpose,
            stop=response.stop,
            model=response.model or profile.model,
            usage=response.usage.model_dump(),
            cost=cost if known else None,
            latency_s=round(response.latency_s, 3),
            context_tokens=self._last_context_tokens,
            context_window=profile.context_window,
            tool_calls=[{"id": c.id, "name": c.name} for c in response.message.tool_calls()],
            reasoning=response.message.reasoning()[:4000],
            stop_details=response.stop_details,
            fallback=response.message.meta.get("fallback"),
        )
        for use in response.message.meta.get("server_tool_uses", []):
            self.emit("tool.server", id=use.get("id"), name=use.get("name"), input=use.get("input"))
        self.emit("budget.update", **self.budget.snapshot())
        return response

    async def _generate(self, messages: list[Message], on_stream: Callable[[StreamEvent], None]) -> LLMResponse:
        """One provider call, aborted when the reply goes silent — a hung connection would otherwise wait forever."""
        self._last_stream_at = time.monotonic()
        call = asyncio.create_task(self.provider.generate(
            system=self.system_prompt, messages=messages, tools=self.tool_specs, on_stream=on_stream,
            effort=self._effort, max_tokens=self.config.max_tokens,
        ))
        idle = self.config.stream_idle_timeout_s
        if idle <= 0:
            return await call
        try:
            while True:
                done, _ = await asyncio.wait({call}, timeout=min(idle, 10.0))
                if call in done:
                    return call.result()
                quiet = time.monotonic() - self._last_stream_at
                if quiet > idle:
                    self.emit("llm.stalled", quiet_s=round(quiet, 1))
                    raise LLMError(f"连接 {quiet:.0f} 秒没有任何响应（含心跳），已中断这次请求。", kind="timeout")
        finally:
            if not call.done():
                call.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await call

    # ---- tools -------------------------------------------------------------------
    async def _run_tools(self, calls: list[ToolCallBlock], truncated: bool) -> list[ToolResultBlock]:
        results: list[ToolResultBlock | None] = [None] * len(calls)
        batch: list[tuple[int, ToolCallBlock]] = []

        async def run_one(index: int, call: ToolCallBlock) -> None:
            results[index] = await self._execute(call, truncated)

        async def flush() -> None:
            if batch:
                await asyncio.gather(*(run_one(i, c) for i, c in batch))
                batch.clear()

        for index, call in enumerate(calls):
            tool = self.tools.get(call.name)
            if tool is not None and tool.parallel_safe and not truncated:
                batch.append((index, call))
                continue
            await flush()
            await run_one(index, call)
        await flush()
        return [r for r in results if r is not None]

    async def _execute(self, call: ToolCallBlock, truncated: bool) -> ToolResultBlock:
        started = time.monotonic()
        tool = self.tools.get(call.name)
        self.emit("tool.call", id=call.id, name=call.name, input=call.input,
                  category=tool.category if tool else None)

        def finish(result: ToolResult) -> ToolResultBlock:
            return self._finish_result(call, result, time.monotonic() - started)

        if tool is None:
            return finish(ToolResult.error(f"未知工具: {call.name}。可用工具: {', '.join(self.tools.names())}"))
        if call.input_error or truncated:
            return finish(ToolResult.error(
                call.input_error
                or "工具参数因输出长度上限被截断，未执行。请用更短的参数重试（例如把大文件分多次写入）。"
            ))
        try:
            args = tool.parse_args(call.input)
        except ToolInputError as exc:
            return finish(ToolResult.error(str(exc)))

        denied = await self._check_approval(tool, call, args)
        if denied is not None:
            return finish(ToolResult.error(denied))

        timeout = tool.timeout_s or self.config.tool_timeout_s
        try:
            result = await asyncio.wait_for(tool.run(args, self.ctx), timeout=timeout)
        except asyncio.TimeoutError:
            result = ToolResult.error(f"工具执行超时（{timeout:.0f} 秒）。")
        except ToolError as exc:
            result = ToolResult.error(str(exc))
        except InteractionCancelled:
            result = ToolResult.error("人工交互已取消。")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("tool %s failed", call.name)
            result = ToolResult.error(f"工具内部错误 {type(exc).__name__}: {exc}")
        if call.name == "activate_skill" and not result.is_error:
            skill = getattr(args, "name", None)
            if skill and skill not in self.activated_skills:
                self.activated_skills.append(skill)
        return finish(result)

    def _finish_result(self, call: ToolCallBlock, result: ToolResult, duration: float) -> ToolResultBlock:
        tool_cost = result.meta.get("cost_usd")
        if isinstance(tool_cost, (int, float)) and tool_cost > 0:  # e.g. OpenAlex usage-based pricing
            self.cost += tool_cost
            self.budget.charge(float(tool_cost))
        text = result.as_text()
        text, saved = truncate_output(text, self.config.tool_output_limit, self.ctx.outputs_dir, call.id)
        images = [b for b in result.content if isinstance(b, ImageBlock)][:MAX_IMAGES_PER_RESULT]
        content: list[TextBlock | ImageBlock] = [TextBlock(text=text or "(无输出)")]
        content.extend(images)
        self.emit(
            "tool.result",
            id=call.id,
            name=call.name,
            is_error=result.is_error,
            preview=text[:3000],
            duration_s=round(duration, 3),
            output_path=saved,
            images=[img.path for img in images if img.path],
            meta=result.meta,
        )
        return ToolResultBlock(call_id=call.id, name=call.name, content=content, is_error=result.is_error)

    async def _check_approval(self, tool: Tool, call: ToolCallBlock, args: Any) -> str | None:
        mode = self._approval_mode(tool.category)
        if mode == "auto":
            return None
        if mode == "deny":
            return f"当前 Harness 配置禁止使用工具 {tool.name}。"
        if self.ctx.interactions is None or self.ctx.auto_mode:
            return f"工具 {tool.name} 需要人工批准，但当前没有人值守（全自动模式），已拒绝。"
        response = await self.ctx.interactions.ask(
            "approval",
            f"允许调用 {tool.name}？",
            {"tool": tool.name, "category": tool.category, "input": call.input, "preview": tool.preview(args),
             "agent": self.config.name, "call_id": call.id},
            topics=self.ctx.topics,
        )
        if response.get("decision") == "allow":
            if response.get("always"):
                self.config.approvals[tool.category] = "auto"
            return None
        note = response.get("note") or ""
        return f"用户拒绝了这次工具调用。{note}".strip()

    # ---- compaction ---------------------------------------------------------------
    def _context_too_big(self, profile: Any) -> str | None:
        """Refuse before spending anything when the prompt alone cannot fit; warn when it is merely tight.

        Without this a small local model silently truncates the system prompt and the tool definitions, and
        then behaves as if it had never been told what to do.
        """
        window = profile.context_window
        if window <= 0:
            return None
        share = self._fixed_tokens / window
        if share >= 0.9:
            return (f"系统提示和工具定义就占了约 {self._fixed_tokens} tokens，而模型 {profile.name} 的上下文只有 "
                    f"{window}。请换上下文更大的模型，或在 Harness 里减少工具组与可见技能。")
        if share >= 0.5:
            self.emit("agent.context.tight", fixed_tokens=self._fixed_tokens, window=window,
                      share=round(share, 3))
        return None

    def _needs_compaction(self) -> bool:
        window = self.provider.profile.context_window
        # The provider's own count is authoritative, but it only exists after a reply: estimate too, so a long
        # stored history is compacted BEFORE the first request instead of after it blows up.
        size = max(self._last_context_tokens, self._fixed_tokens + self._history_tokens)
        return len(self.messages) > 4 and size > self.config.compact_ratio * window

    async def _compact(self) -> None:
        before = max(self._last_context_tokens, self._fixed_tokens + self._history_tokens)
        self.emit("agent.compaction.start", tokens=before)
        request = [*self.messages, user_text(SUMMARY_PROMPT)]
        response = await self._call_llm(0, messages=request, purpose="compaction")
        summary = response.message.text().strip() or "(摘要为空)"
        message = summary_message(summary, self.activated_skills)
        self.messages = []
        self._history_tokens = 0
        self._append(message)
        self._last_context_tokens = 0
        self.emit("agent.compaction", tokens_before=before, summary=summary[:4000])
