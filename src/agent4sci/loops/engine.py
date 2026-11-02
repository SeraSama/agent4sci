"""Loop engine: interprets a LoopGraph.

Semantics
- Within a scope, a node runs once all incoming edges are resolved (fired or dead) and at least one fired
  (dead-path elimination), so condition branches and joins behave like a workflow engine.
- ``loop`` containers repeat their body until ``until`` holds, a limit is hit or a metric stalls; with
  ``keep_discard`` each iteration is committed if the metric improved and reverted otherwise.
- ``parallel`` containers run their body once per item (bounded concurrency).
- Every finished node is recorded under a scope key; resuming a run replays the graph and reuses recorded
  outputs, so crashes, restarts and pauses continue where they stopped.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..agent.budget import Budget
from ..agent.harness import HarnessBuilder, HarnessContext, _role_prompt, tool_groups
from ..agent.interactions import InteractionCancelled
from ..core.envfix import tool_env
from ..core.events import Emitter, TraceWriter
from ..core.projects import Project
from ..core.textio import read_text
from ..tools.base import ToolContext
from ..tools.findings import resolve_pointer
from ..tools.result import ResultSink, SubmitResultTool, result_from_text
from ..tools.shell import dangerous_reason, run_command
from . import gitops
from .checks import run_checks
from .expr import ExpressionError, evaluate, render_template, truthy
from .schema import LoopGraph, Node, has_errors, validate_graph
from .store import LoopStore, NodeRecord, RunState, RunStore
from .tournament import CANDIDATES_SCHEMA, JUDGE_SCHEMA, NOVELTY_SCHEMA, dedupe, run_elo, to_markdown, weighted_score

if TYPE_CHECKING:  # pragma: no cover
    from ..core.app import AppServices


class RunFailed(Exception):
    pass


class RunPaused(Exception):
    pass


class RunAborted(RunFailed):
    pass


@dataclass
class Scope:
    graph: LoopGraph
    parent: str | None
    prefix: str
    extra: dict[str, Any] = field(default_factory=dict)
    budget: Budget | None = None
    keep_discard: bool = False


@dataclass
class RunContext:
    state: RunState
    project: Project
    budget: Budget
    emit: Emitter
    topic: str

    @property
    def workspace(self) -> Path:
        return self.project.workspace


def _token_delta(before: Any, after: Any) -> dict[str, int]:
    """Tokens spent between two budget snapshots, as plain input/output numbers."""
    fields = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")
    delta = {f: max(0, getattr(after, f, 0) - getattr(before, f, 0)) for f in fields}
    inputs = delta["input_tokens"] + delta["cache_read_tokens"] + delta["cache_write_tokens"]
    return {k: v for k, v in {"input": inputs, "output": delta["output_tokens"],
                              "cached": delta["cache_read_tokens"]}.items() if v}


def _preview(value: Any, limit: int = 600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit] + ("…" if len(text) > limit else "")


class LoopEngine:
    def __init__(self, app: "AppServices", builder: HarnessBuilder, loops: LoopStore | None = None,
                 runs: RunStore | None = None) -> None:
        self.app = app
        self.builder = builder
        self.loops = loops or LoopStore(app.db)
        self.runs = runs or RunStore(app.db)
        self.tasks: dict[str, asyncio.Task] = {}
        self._pause: set[str] = set()

    # ---- lifecycle --------------------------------------------------------------------
    def create_run(self, graph: LoopGraph, project: Project, inputs: dict[str, Any], *, auto_mode: bool = False,
                   max_cost_usd: float | None = None, model_id: str | None = None,
                   conversation_id: str | None = None) -> RunState:
        issues = validate_graph(graph)
        if has_errors(issues):
            raise ValueError("Loop 图有错误：" + "；".join(i.message for i in issues if i.level == "error"))
        values = dict(inputs)
        for name, spec in graph.inputs.items():
            if values.get(name) in (None, "") and spec.default is not None:
                values[name] = spec.default
            if spec.required and values.get(name) in (None, ""):
                raise ValueError(f"缺少输入：{spec.label or name}")
        state = RunState(
            loop_id=graph.id, loop_name=graph.name, graph=graph, project_id=project.id, inputs=values,
            auto_mode=auto_mode, max_cost_usd=max_cost_usd or graph.settings.max_cost_usd,
            model_id=model_id or graph.settings.model_id,
            memory_mode=graph.settings.memory_mode or project.settings.default_memory_mode,
            conversation_id=conversation_id,
        )
        self.runs.save(state)
        return state

    def start(self, run_id: str) -> asyncio.Task:
        existing = self.tasks.get(run_id)
        if existing is not None and not existing.done():
            return existing
        self._pause.discard(run_id)
        task = asyncio.create_task(self._execute(run_id), name=f"run:{run_id}")
        self.tasks[run_id] = task
        return task

    async def run_to_completion(self, run_id: str) -> RunState:
        await self.start(run_id)
        state = self.runs.get(run_id)
        assert state is not None
        return state

    def pause(self, run_id: str) -> None:
        self._pause.add(run_id)

    def cancel(self, run_id: str) -> None:
        self.app.interactions.cancel(f"run:{run_id}")
        task = self.tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()

    # ---- execution --------------------------------------------------------------------
    async def _execute(self, run_id: str) -> None:
        state = self.runs.get(run_id)
        if state is None:
            raise KeyError(run_id)
        project = self.app.projects.get(state.project_id)
        if project is None:
            state.status, state.error = "failed", "项目不存在"
            self.runs.save(state)
            return
        topic = f"run:{run_id}"
        trace = TraceWriter(project.workspace / ".a4s" / "runs" / run_id / "trace.jsonl")
        budget = Budget(state.max_cost_usd, name="run")
        budget.spent = state.cost_usd
        ctx = RunContext(state=state, project=project, budget=budget, topic=topic,
                         emit=Emitter(self.app.bus, [topic], trace=trace, labels={"run": run_id}))
        if state.graph.settings.auto_commit:
            gitops.ensure_repo(project.workspace)
        state.status, state.error, state.waiting = "running", None, None
        self._save(ctx)
        ctx.emit("run.started", loop=state.loop_name, inputs=state.inputs, auto_mode=state.auto_mode)
        try:
            await self._run_scope(ctx, Scope(state.graph, None, "", budget=budget))
            state.status = "done"
        except RunPaused:
            state.status = "paused"
        except asyncio.CancelledError:
            state.status = "cancelled"
            self._save(ctx)
            ctx.emit("run.cancelled")
            raise
        except RunFailed as exc:
            state.status, state.error = "failed", str(exc)
        except Exception as exc:  # engine bug or unexpected error: keep the state inspectable
            state.status, state.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            if state.status != "cancelled":
                if state.graph.settings.auto_commit and state.status in ("done", "failed"):
                    gitops.commit_all(project.workspace, f"a4s run {run_id}: {state.status}")
                self._save(ctx)
                ctx.emit(f"run.{state.status}", error=state.error, cost=round(state.cost_usd, 4))

    def _save(self, ctx: RunContext) -> None:
        ctx.state.cost_usd = ctx.budget.spent
        self.runs.save(ctx.state)

    def _names(self, ctx: RunContext, scope: Scope) -> dict[str, Any]:
        state = ctx.state
        nodes = {nid: {"output": rec.output, "status": rec.status, "error": rec.error, "cost": rec.cost}
                 for nid, key in state.latest.items() if (rec := state.records.get(key)) is not None}
        metrics = {name: [m["value"] for m in values] for name, values in state.metrics.items()}
        names: dict[str, Any] = {"inputs": state.inputs, "nodes": nodes, "metrics": metrics,
                                 "run": {"id": state.id, "cost": round(ctx.budget.spent, 4)}}
        names.update(scope.extra)
        return names

    async def _run_scope(self, ctx: RunContext, scope: Scope) -> Any:
        graph = scope.graph
        nodes = graph.children(scope.parent)
        nodes.sort(key=lambda n: (n.position.y, n.position.x))
        edges = graph.edges_in_scope(scope.parent)
        incoming = {n.id: [e for e in edges if e.target == n.id] for n in nodes}
        outgoing = {n.id: [e for e in edges if e.source == n.id] for n in nodes}
        edge_state: dict[str, str | None] = {e.id: None for e in edges}
        pending = [n.id for n in nodes]
        last_output: Any = None
        while pending:
            progressed = False
            for node_id in list(pending):
                ins = incoming[node_id]
                if ins and all(edge_state[e.id] == "dead" for e in ins):
                    for e in outgoing[node_id]:
                        edge_state[e.id] = "dead"
                    pending.remove(node_id)
                    ctx.emit("node.skipped", node=node_id, key=scope.prefix + node_id)
                    progressed = True
                    break
                if ins and any(edge_state[e.id] is None for e in ins):
                    continue
                node = graph.node(node_id)
                output = await self._run_node(ctx, scope, node)
                if node.type not in ("start", "end"):
                    last_output = output
                chosen = self._choose_edges(ctx, scope, node, outgoing[node_id], output)
                for e in outgoing[node_id]:
                    edge_state[e.id] = "fired" if e in chosen else "dead"
                pending.remove(node_id)
                progressed = True
                break
            if not progressed:
                raise RunFailed("流程无法继续：有节点的前驱永远不会完成")
        return last_output

    def _choose_edges(self, ctx: RunContext, scope: Scope, node: Node, outgoing: list[Any], output: Any) -> list[Any]:
        if not outgoing:
            return []
        names = self._names(ctx, scope)
        if node.type == "condition":
            default = None
            for e in outgoing:
                cond = (e.condition or "").strip()
                if cond in ("", "else", "default"):
                    default = default or e
                    continue
                if cond == "true":
                    ok = bool(output)
                elif cond == "false":
                    ok = not bool(output)
                else:
                    ok = truthy(cond, names)
                if ok:
                    return [e]
            return [default] if default is not None else []
        chosen = []
        for e in outgoing:
            cond = (e.condition or "").strip()
            if cond in ("", "true", "else", "default") or truthy(cond, names):
                chosen.append(e)
        return chosen

    async def _run_node(self, ctx: RunContext, scope: Scope, node: Node) -> Any:
        state = ctx.state
        key = scope.prefix + node.id
        record = state.records.get(key)
        if record is not None and record.status == "done":
            state.latest[node.id] = key
            return record.output
        if node.type in ("start", "end", "note"):
            state.records[key] = NodeRecord(key=key, node=node.id, status="done", finished=time.time())
            state.latest[node.id] = key
            return None
        if state.id in self._pause:
            raise RunPaused()
        budget = scope.budget or ctx.budget
        if budget.exceeded():
            raise RunFailed(f"预算已用完（已花费 ${ctx.budget.spent:.2f}）")
        started_cost = ctx.budget.spent
        started_usage = ctx.budget.usage.model_copy()
        state.records[key] = NodeRecord(key=key, node=node.id, status="running")
        state.latest[node.id] = key
        self._save(ctx)
        ctx.emit("node.started", node=node.id, key=key, node_type=node.type, label=node.label)
        try:
            output = await self._dispatch(ctx, scope, node, key)
        except (RunPaused, RunAborted, asyncio.CancelledError):
            state.records.pop(key, None)
            raise
        except InteractionCancelled:
            state.records.pop(key, None)
            raise RunPaused()
        except Exception as exc:
            message = str(exc) if isinstance(exc, (RunFailed, ExpressionError, ValueError, RuntimeError)) else \
                f"{type(exc).__name__}: {exc}"
            state.records[key] = NodeRecord(key=key, node=node.id, status="failed", error=message,
                                            finished=time.time(), cost=ctx.budget.spent - started_cost)
            self._save(ctx)
            ctx.emit("node.failed", node=node.id, key=key, error=message)
            if node.config.get("continue_on_error"):
                return {"error": message}
            raise RunFailed(f"节点「{node.label or node.id}」失败：{message}") from exc
        spent_tokens = _token_delta(started_usage, ctx.budget.usage)
        for kind, value in spent_tokens.items():
            state.tokens[kind] = state.tokens.get(kind, 0) + value
        state.records[key] = NodeRecord(key=key, node=node.id, status="done", output=output, finished=time.time(),
                                        cost=round(ctx.budget.spent - started_cost, 6), tokens=spent_tokens,
                                        started=state.records[key].started)
        self._save(ctx)
        ctx.emit("node.done", node=node.id, key=key, preview=_preview(output), cost=state.records[key].cost)
        if (state.graph.settings.auto_commit and not scope.keep_discard
                and node.type in ("agent", "command", "tournament", "render", "tool")):
            sha = gitops.commit_all(ctx.workspace, f"a4s: {node.label or node.id}")
            if sha:
                ctx.emit("git.commit", sha=sha, node=node.id)
        return output

    async def _dispatch(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        handler = getattr(self, f"_node_{node.type}", None)
        if handler is None:
            raise RunFailed(f"不支持的节点类型: {node.type}")
        return await handler(ctx, scope, node, key)

    # ---- helpers for agent-based nodes ---------------------------------------------------
    def _context_block(self, ctx: RunContext, node: Node) -> str:
        state = ctx.state
        lines = ["## 运行上下文"]
        if state.inputs:
            lines.append("输入：" + json.dumps(state.inputs, ensure_ascii=False))
        summaries = []
        for nid, key in state.latest.items():
            rec = state.records.get(key)
            if rec is None or rec.status != "done" or nid == node.id or not isinstance(rec.output, dict):
                continue
            summary = rec.output.get("summary")
            if summary:
                label = state.graph.node(nid).label if any(n.id == nid for n in state.graph.nodes) else nid
                summaries.append(f"- {label}：{_preview(summary, 800)}")
        if summaries:
            lines.append("前序节点摘要：\n" + "\n".join(summaries[-8:]))
        outputs = node.config.get("outputs") or []
        if outputs:
            lines.append("本节点必须产出这些文件：" + "、".join(outputs))
        return "\n\n".join(lines)

    async def _agent(self, ctx: RunContext, scope: Scope, node: Node, key: str, *, prompt: str,
                     schema: dict[str, Any] | None, harness_id: str | None = None, role: str | None = None,
                     tool_groups_override: list[str] | None = None, max_turns: int | None = None,
                     label: str = "") -> tuple[Any, ResultSink | None, Any]:
        cfg = node.config
        harness = self.builder.harnesses.get(harness_id or cfg.get("harness") or "assistant")
        role = role or cfg.get("role")
        updates: dict[str, Any] = {}
        if role:
            updates.update(role_prompt=_role_prompt(role), role=role)
        groups = tool_groups_override or cfg.get("tool_groups")
        if groups:
            updates["tool_groups"] = list(groups)
        if updates:
            harness = harness.model_copy(update=updates)
        sink = ResultSink(schema) if schema else None
        budget = (scope.budget or ctx.budget).child(cfg.get("max_cost_usd"), name=node.id)
        hctx = HarnessContext(
            workspace=ctx.workspace, project=ctx.project, run_id=ctx.state.id, topics=[ctx.topic],
            memory_mode=ctx.state.memory_mode, auto_mode=ctx.state.auto_mode,
            emit=ctx.emit.child(label or node.label or node.id, node=node.id, key=key), budget=budget,
            stage_id=node.id, model_override=cfg.get("model_id") or ctx.state.model_id,
            extra_tools=[SubmitResultTool(schema)] if schema else [],
            extra_services={"result_sink": sink} if sink else {},
        )
        agent = self.builder.build_agent(harness, hctx, config_overrides={
            "max_turns": int(max_turns or cfg.get("max_turns") or harness.max_turns),
            "stop_on_submit": bool(schema), "effort": cfg.get("effort") or harness.effort})
        result = await agent.run(prompt)
        if schema and sink is not None and not sink.submitted and result.status == "done":
            result = await agent.run("你还没有调用 submit_result 提交结构化结果，请现在提交。")
        if schema and sink is not None and not sink.submitted and result.status == "done":
            salvaged = result_from_text(result.text, schema)
            if salvaged is None:
                raise RunFailed(f"{label or node.label or node.id}：Agent 没有调用 submit_result 提交结构化结果"
                                "（当前模型可能不擅长工具调用；可换一个支持工具调用的模型，或为该节点设置“失败时继续”）")
            sink.value, sink.summary, sink.submitted = salvaged["result"], salvaged["summary"], True
            ctx.emit("node.note", node=node.id, key=key, note="结构化结果取自回复文本（模型没有调用 submit_result）")
        if agent.cost_unknown and not ctx.state.cost_unknown:
            ctx.state.cost_unknown = True
            self._warn(ctx, node, key, "所用模型没有设置价格：运行费用无法统计，预算上限也无法生效（在“模型”页填写单价）")
        if result.budget_wrapped:
            self._warn(ctx, node, key, f"「{label or node.label or node.id}」预算即将用完，Agent 提前收尾：这一步的产出可能不完整")
        return agent, sink, result

    def _warn(self, ctx: RunContext, node: Node, key: str, text: str) -> None:
        """Record something the run's final status would hide, and show it in the timeline."""
        if text not in ctx.state.warnings:
            ctx.state.warnings.append(text)
        ctx.emit("node.note", node=node.id, key=key, note=text)

    async def _node_agent(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        cfg = node.config
        names = self._names(ctx, scope)
        schema = cfg.get("output_schema")
        prompt = render_template(cfg["prompt"], names) + "\n\n" + self._context_block(ctx, node)
        if schema:
            prompt += "\n\n完成后调用 submit_result 提交结构化结果（summary 写清做了什么、发现了什么）。"
        agent, sink, result = await self._agent(ctx, scope, node, key, prompt=prompt, schema=schema)
        missing = [p for p in cfg.get("outputs", []) if not (ctx.workspace / p).exists()]
        if missing and result.status == "done":
            result = await agent.run(f"以下预期产出还不存在：{'、'.join(missing)}。请完成它们再结束。")
            missing = [p for p in cfg.get("outputs", []) if not (ctx.workspace / p).exists()]
        if result.status != "done":
            raise RunFailed(f"Agent 未完成（{result.status}）：{result.error or result.text[:300]}")
        if missing:  # never let later steps (reviews, checks) judge files that were never written
            raise RunFailed(f"Agent 没有产出必须的文件：{'、'.join(missing)}（当前模型可能不擅长工具调用；"
                            "可换一个支持工具调用的模型，或为该节点设置“失败时继续”）")
        output = self._agent_output(sink, result, missing)
        rounds = 0
        while cfg.get("checkpoint") and not ctx.state.auto_mode and rounds < 3:
            decision = await self._ask_checkpoint(ctx, node, key, output)
            if decision.get("action") != "feedback":
                output["review"] = decision
                break
            rounds += 1
            result = await agent.run(f"研究者对这一步的反馈：{decision.get('feedback', '')}\n请据此修改，然后重新提交。")
            if sink is not None:
                sink.submitted = False
            output = self._agent_output(sink, result, missing)
        return output

    @staticmethod
    def _agent_output(sink: ResultSink | None, result: Any, missing: list[str]) -> dict[str, Any]:
        submitted = sink is not None and sink.submitted
        return {"summary": sink.summary if submitted else result.text[:3000],
                "result": sink.value if submitted else None, "text": result.text[:20000], "status": result.status,
                "turns": result.turns, "cost": result.cost_usd, "missing_outputs": missing}

    async def _ask_checkpoint(self, ctx: RunContext, node: Node, key: str, output: dict[str, Any]) -> dict[str, Any]:
        payload = {"run_id": ctx.state.id, "node": node.id, "key": key, "kind": "review",
                   "message": output.get("summary", ""), "files": node.config.get("outputs", []),
                   "options": ["approve", "feedback", "abort"]}
        return await self._wait_human(ctx, node, f"审阅：{node.label or node.id}", payload)

    async def _wait_human(self, ctx: RunContext, node: Node, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        ctx.state.status = "waiting"
        ctx.state.waiting = {"node": node.id, "title": title, **payload}
        self._save(ctx)
        ctx.emit("run.waiting", node=node.id, title=title)
        try:
            answer = await self.app.interactions.ask("checkpoint", title, payload, topics=[ctx.topic])
        finally:
            ctx.state.status, ctx.state.waiting = "running", None
            self._save(ctx)
        if answer.get("action") == "abort":
            raise RunAborted("研究者终止了运行")
        return answer

    # ---- node handlers -------------------------------------------------------------------
    async def _node_human(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        cfg = node.config
        names = self._names(ctx, scope)
        options = cfg.get("options") or []
        if cfg.get("options_expr"):
            options = evaluate(cfg["options_expr"], names) or []
        options = [o if isinstance(o, str) else json.dumps(o, ensure_ascii=False) for o in options]
        if ctx.state.auto_mode:
            return {"action": "approve", "choice": options[0] if options else None, "auto": True}
        payload = {"run_id": ctx.state.id, "node": node.id, "key": key, "kind": cfg.get("kind", "approve"),
                   "message": render_template(cfg.get("message", ""), names), "options": options,
                   "files": cfg.get("show_files", [])}
        answer = await self._wait_human(ctx, node, render_template(cfg.get("title") or node.label or "人工检查点", names),
                                        payload)
        return {"action": answer.get("action", "approve"), "feedback": answer.get("feedback", ""),
                "choice": answer.get("choice"), "input": answer.get("input", "")}

    async def _node_condition(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        expression = node.config.get("expression")
        return evaluate(expression, self._names(ctx, scope)) if expression else True

    async def _node_command(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        cfg = node.config
        names = self._names(ctx, scope)
        command = render_template(cfg["command"], names)
        reason = dangerous_reason(command)
        if reason:
            if ctx.state.auto_mode:
                raise RunFailed(f"命令被拦截（{reason}）")
            await self._wait_human(ctx, node, f"允许执行有风险的命令？（{reason}）",
                                   {"run_id": ctx.state.id, "node": node.id, "kind": "approve", "message": command,
                                    "options": ["approve", "abort"]})
        passthrough = ctx.project.settings.env_passthrough
        result = await run_command(command, cwd=ctx.workspace, env=tool_env(ctx.workspace, passthrough),
                                   timeout_s=float(cfg.get("timeout_s", 1800)))
        metric = None
        if cfg.get("metric"):
            metric = self._read_metric(ctx, cfg["metric"], names)
        output = {"exit_code": result.exit_code, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:],
                  "timed_out": result.timed_out, "metric": metric}
        passed = result.exit_code == 0 and not result.timed_out
        if cfg.get("pass_if"):
            passed = truthy(cfg["pass_if"], {**names, **output})
        output["passed"] = passed
        if cfg.get("fail_on_error") and not passed:
            raise RunFailed(f"验证门未通过（exit {result.exit_code}）：{result.stderr[-300:]}")
        return output

    async def _node_tool(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        cfg = node.config
        names = self._names(ctx, scope)
        tools = {t.name: t for group in tool_groups().values() for t in group}
        tools.update({t.name: t for t in self.app.mcp.tools()})
        tool = tools.get(cfg["tool"])
        if tool is None:
            raise RunFailed(f"没有这个工具: {cfg['tool']}")

        def render_value(v: Any) -> Any:
            if isinstance(v, str):
                return render_template(v, names)
            if isinstance(v, dict):
                return {k: render_value(x) for k, x in v.items()}
            if isinstance(v, list):
                return [render_value(x) for x in v]
            return v

        args = tool.parse_args(render_value(cfg.get("args") or {}))
        tctx = ToolContext(workspace=ctx.workspace, emit=ctx.emit.child(node.label, node=node.id, key=key),
                           interactions=self.app.interactions, auto_mode=ctx.state.auto_mode, run_id=ctx.state.id,
                           topics=[ctx.topic], env_passthrough=ctx.project.settings.env_passthrough,
                           services={"http": self.app.http, "lit_config": self.app.lit_config(),
                                     "kernels": self.app.kernels})
        result = await tool.run(args, tctx)
        if result.meta.get("cost_usd"):
            ctx.budget.charge(float(result.meta["cost_usd"]))
        if result.is_error and not cfg.get("continue_on_error"):
            raise RunFailed(result.as_text()[:500])
        return {"text": result.as_text()[:20000], "is_error": result.is_error}

    def _read_metric(self, ctx: RunContext, spec: dict[str, Any], names: dict[str, Any]) -> float | None:
        try:
            if spec.get("expr"):
                value = evaluate(spec["expr"], names)
            else:
                data = json.loads(read_text(ctx.workspace / spec["file"]))
                value = resolve_pointer(data, spec.get("pointer", ""))
            return float(value)
        except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError, ExpressionError):
            return None

    async def _node_loop(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        cfg = node.config
        state = ctx.state
        max_iter = int(cfg.get("max_iterations", 3))
        metric_spec = cfg.get("metric") or None
        metric_name = (metric_spec or {}).get("name") or node.id
        goal = (metric_spec or {}).get("goal", "max")
        keep_discard = bool(cfg.get("keep_discard")) and gitops.available(ctx.workspace)
        patience = int(cfg.get("stall_patience") or 0)
        loop_budget = (scope.budget or ctx.budget).child(cfg.get("max_cost_usd"), name=node.id)
        if keep_discard:
            gitops.commit_all(ctx.workspace, f"a4s loop {node.label or node.id}: 基线")
        history: list[dict[str, Any]] = []
        best: float | None = None
        stall = 0
        stopped_by = "max_iterations"
        last: Any = None
        iteration_costs: list[float] = []
        for i in range(1, max_iter + 1):
            iter_key = f"{key}#{i}"
            done = state.records.get(iter_key)
            spent_before = loop_budget.spent
            iteration_usage = ctx.budget.usage.model_copy()
            if done is not None and done.status == "done":
                summary = done.output
            else:
                extra = {**scope.extra, "loop": {"iteration": i, "max": max_iter, "history": history, "id": node.id}}
                body_scope = Scope(scope.graph, node.id, f"{key}[{i}]/", extra, loop_budget, keep_discard)
                last = await self._run_scope(ctx, body_scope)
                value = self._read_metric(ctx, metric_spec, self._names(ctx, body_scope)) if metric_spec else None
                improved = value is not None and (best is None or (value > best if goal == "max" else value < best))
                kept = None
                if keep_discard:
                    if improved or (metric_spec is None):
                        gitops.commit_all(ctx.workspace, f"a4s loop {node.id} 第 {i} 轮：保留 ({value})")
                        kept = True
                    else:
                        gitops.discard_changes(ctx.workspace)
                        kept = False
                summary = {"iteration": i, "metric": value, "improved": improved, "kept": kept,
                           "output": _preview(last, 2000)}
                state.records[iter_key] = NodeRecord(key=iter_key, node=node.id, status="done", output=summary,
                                                     finished=time.time(),
                                                     cost=round(loop_budget.spent - spent_before, 6),
                                                     tokens=_token_delta(iteration_usage, ctx.budget.usage))
                if value is not None:
                    state.metrics.setdefault(metric_name, []).append(
                        {"iteration": i, "value": value, "improved": improved, "kept": kept, "loop": node.id})
                self._save(ctx)
                ctx.emit("loop.iteration", node=node.id, key=key, iteration=i, metric=value, metric_name=metric_name,
                         improved=improved, kept=kept)
            history.append(summary)
            value = summary.get("metric")
            if value is not None and (best is None or (value > best if goal == "max" else value < best)):
                best, stall = value, 0
            elif metric_spec is not None:
                stall += 1
            names = {**self._names(ctx, scope), "loop": {"iteration": i, "max": max_iter, "history": history,
                                                         "id": node.id, "best": best}}
            if cfg.get("until") and truthy(cfg["until"], names):
                stopped_by = "until"
                break
            if patience and stall >= patience:
                stopped_by = "stalled"
                break
            if loop_budget.exceeded():
                stopped_by = "budget"
                break
            if loop_budget.spent > spent_before:
                iteration_costs.append(loop_budget.spent - spent_before)
            remaining = loop_budget.remaining
            if (i < max_iter and remaining is not None and iteration_costs
                    and remaining < 0.7 * sum(iteration_costs) / len(iteration_costs)):
                stopped_by = "budget"  # not enough left for a meaningful iteration: stop instead of starving it
                break
        if cfg.get("until") and stopped_by != "until":
            reason = {"max_iterations": f"达到最大轮数 {max_iter}", "budget": "剩余预算不够再跑一轮",
                      "stalled": f"指标连续 {patience} 轮没有改进"}.get(stopped_by, stopped_by)
            self._warn(ctx, node, key, f"「{node.label or node.id}」在 {len(history)} 轮后停止（{reason}），"
                                       f"停止条件没有满足：{cfg['until']}")
        return {"iterations": len(history), "best": best, "stopped_by": stopped_by, "history": history,
                "summary": f"循环 {len(history)} 轮，停止原因：{stopped_by}" + (f"，最佳指标 {best}" if best is not None else "")}

    async def _node_parallel(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        cfg = node.config
        items = evaluate(cfg["items"], self._names(ctx, scope))
        items = list(items or [])[: int(cfg.get("max_items", 10))]
        sem = asyncio.Semaphore(int(cfg.get("max_concurrency", 2)))
        item_var = cfg.get("item_var", "item")

        async def one(index: int, item: Any) -> Any:
            async with sem:
                extra = {**scope.extra, item_var: item, "index": index}
                return await self._run_scope(ctx, Scope(scope.graph, node.id, f"{key}<{index}>/", extra, scope.budget,
                                                        scope.keep_discard))

        results = await asyncio.gather(*(one(i, it) for i, it in enumerate(items)))
        return {"items": items, "results": results, "summary": f"并行执行 {len(items)} 项"}

    async def _node_subflow(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        cfg = node.config
        graph = self.loops.get(cfg["loop_id"])
        if graph is None:
            raise RunFailed(f"找不到子流程 {cfg['loop_id']}")
        names = self._names(ctx, scope)
        inputs = {k: render_template(v, names) if isinstance(v, str) else v for k, v in (cfg.get("inputs") or {}).items()}
        extra = {**scope.extra, "inputs": {**ctx.state.inputs, **inputs}}
        return await self._run_scope(ctx, Scope(graph, None, f"{key}/", extra, scope.budget, scope.keep_discard))

    async def _node_checks(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        report = run_checks(node.config, ctx.workspace, ctx.state.protected)
        if report["protected"]:
            ctx.state.protected.update(report["protected"])
        report["summary"] = ("✅ 检查通过" if report["passed"] else "❌ " + "；".join(report["errors"][:5])) + (
            f"（{len(report['warnings'])} 个警告）" if report["warnings"] else "")
        if node.config.get("fail_on_error") and not report["passed"]:
            raise RunFailed(report["summary"])
        return report

    async def _node_render(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        from ..tools.provenance import lint_bare_numbers, render_text

        source = ctx.workspace / node.config.get("source", "paper/paper.src.md")
        output = ctx.workspace / node.config.get("output", "paper/paper.md")
        if not source.exists():
            raise RunFailed(f"源文件不存在: {source.name}")
        text = read_text(source)
        rendered, missing = render_text(text, ctx.workspace)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, "utf-8")
        bare = lint_bare_numbers(text)  # the tool path reports these too; the node used to skip them
        summary = f"已渲染 {output.name}"
        if missing:
            summary += f"，{len(missing)} 个占位符缺失"
        if bare:
            summary += f"，{len(bare)} 个数字没有出处"
            self._warn(ctx, node, f"render:{key}", f"「{node.label or node.id}」{len(bare)} 个结果数字没有出处（示例：{bare[0]}）")
        return {"output": str(output.relative_to(ctx.workspace)), "missing": missing,
                "bare_numbers": bare[:30], "summary": summary}

    async def _node_memory(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        from ..agent.harness import memory_roots
        from ..memory.consolidate import apply_ops, propose_updates
        from ..memory.store import MemoryAccess

        cfg = node.config
        hctx = HarnessContext(workspace=ctx.workspace, project=ctx.project, run_id=ctx.state.id,
                              conversation_id=ctx.state.conversation_id)
        access = MemoryAccess(ctx.state.memory_mode, memory_roots(hctx))
        names = self._names(ctx, scope)
        if cfg.get("action", "consolidate") == "write":
            item = access.store(cfg.get("scope", "project")).write(
                render_template(cfg.get("name", "note"), names), render_template(cfg.get("body", ""), names),
                description=render_template(cfg.get("description", ""), names), source=f"run:{ctx.state.id}")
            return {"written": [item.file], "summary": f"已写入记忆 {item.name}"}
        from ..skills.distill import trace_digest

        events = self.app.db.list_events(ctx.topic)
        model = self.builder.model_for(self.builder.harnesses.get("assistant"),
                                       HarnessContext(workspace=ctx.workspace, model_override=ctx.state.model_id))
        ops = await propose_updates(self.builder.provider_for(model), access, trace_digest(events),
                                    workspace=ctx.workspace, emit=ctx.emit.child("memory"), budget=ctx.budget.child())
        if cfg.get("auto_apply", True):
            written = apply_ops(access, ops, source=f"run:{ctx.state.id}")
            return {"ops": [o.model_dump() for o in ops], "summary": f"更新了 {len(written)} 条记忆"}
        return {"ops": [o.model_dump() for o in ops], "summary": f"提议 {len(ops)} 条记忆更新（待确认）"}

    async def _node_distill(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        from ..skills.distill import propose_skills, trace_digest, write_learned_skill
        from ..skills.registry import SkillRegistry, default_roots

        events = self.app.db.list_events(ctx.topic)
        registry = SkillRegistry(default_roots(ctx.workspace))
        model = self.builder.model_for(self.builder.harnesses.get("assistant"),
                                       HarnessContext(workspace=ctx.workspace, model_override=ctx.state.model_id))
        proposals = await propose_skills(self.builder.provider_for(model), digest=trace_digest(events),
                                         existing=registry.list(include_inactive=True), workspace=ctx.workspace,
                                         emit=ctx.emit.child("distill"), budget=ctx.budget.child())
        results = [write_learned_skill(ctx.workspace / "skills" / "learned", p, run_id=ctx.state.id) for p in proposals]
        pending = [r["name"] for r in results if r["status"] == "pending"]
        return {"skills": results, "summary": f"沉淀了 {len(pending)} 个待审批技能" + (f"：{', '.join(pending)}" if pending else "")}

    async def _node_tournament(self, ctx: RunContext, scope: Scope, node: Node, key: str) -> Any:
        cfg = node.config
        names = self._names(ctx, scope)
        n = int(cfg.get("n", 6))
        top_k = int(cfg.get("top_k", 3))
        base = render_template(cfg["prompt"], names) + "\n\n" + self._context_block(ctx, node)
        _, sink, result = await self._agent(
            ctx, scope, node, key, schema=CANDIDATES_SCHEMA, role=cfg.get("role", "hypothesizer"),
            harness_id=cfg.get("harness", "librarian"), label="生成候选",
            prompt=base + f"\n\n请提出 {n} 个互不相同的候选假设/想法，并用 submit_result 提交。")
        candidates = list((sink.value or {}).get("candidates", [])) if sink and sink.submitted else []
        if not candidates:
            raise RunFailed("没有生成任何候选想法")
        candidates = dedupe(candidates)[:n]

        sem = asyncio.Semaphore(int(cfg.get("max_concurrency", 3)))

        async def assess(c: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                prompt = (f"评估这个研究想法的新颖性、可行性与影响。先用 search_papers 做 2–4 次检索，找出最相近的已有工作，"
                          f"然后用 submit_result 提交评分（0–10）。可行性要考虑工作区的算力与数据。\n\n"
                          f"标题：{c.get('title')}\n假设：{c.get('hypothesis')}\n理由：{c.get('rationale')}\n"
                          f"检验：{c.get('test')}")
                _, s, _ = await self._agent(ctx, scope, node, key, prompt=prompt, schema=NOVELTY_SCHEMA,
                                            harness_id="librarian", tool_groups_override=["literature"],
                                            max_turns=12, label="新颖性评估")
                scores = s.value if s and s.submitted else {"novelty": 5, "feasibility": 5, "impact": 5,
                                                            "comment": "评估失败，使用默认分"}
                return {**c, "scores": scores, "score": weighted_score(scores)}

        assessed = list(await asyncio.gather(*(assess(c) for c in candidates))) if cfg.get("novelty_check", True) \
            else [{**c, "score": 0} for c in candidates]
        assessed.sort(key=lambda c: -c.get("score", 0))
        finalists = assessed[: max(top_k + 1, min(len(assessed), int(cfg.get("finalists", 4))))]

        async def judge(a: dict[str, Any], b: dict[str, Any]) -> str:
            prompt = ("比较两个研究想法，选出更值得投入算力去做的一个（综合新颖性、可行性、潜在影响、可检验性）。"
                      "用 submit_result 提交 winner（A 或 B）和理由。\n\n"
                      f"A：{a.get('title')} — {a.get('hypothesis')}\n检验：{a.get('test')}\n\n"
                      f"B：{b.get('title')} — {b.get('hypothesis')}\n检验：{b.get('test')}")
            _, s, _ = await self._agent(ctx, scope, node, key, prompt=prompt, schema=JUDGE_SCHEMA,
                                        harness_id="reviewer", tool_groups_override=["fs"], max_turns=3, label="两两评判")
            return (s.value or {}).get("winner", "A") if s and s.submitted else "A"

        ranked = await run_elo(finalists, judge, seed=int(cfg.get("seed", 0))) if len(finalists) > 1 else finalists
        ranked += [c for c in assessed if c not in finalists]
        out_path = ctx.workspace / cfg.get("output_file", "plan/hypotheses.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(to_markdown(ranked, top_k), "utf-8")
        top = ranked[:top_k]
        return {"ranked": ranked, "top": top, "options": [c.get("title") for c in top],
                "summary": "排名前列：" + "；".join(f"{c.get('title')}（Elo {c.get('elo', '—')}）" for c in top),
                "file": str(out_path.relative_to(ctx.workspace))}
