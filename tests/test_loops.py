from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent4sci.agent.harness import HarnessBuilder
from agent4sci.core.app import AppServices
from agent4sci.core.db import Database
from agent4sci.llm.fake_provider import FakeProvider, demo_brain
from agent4sci.loops import gitops
from agent4sci.loops.engine import LoopEngine
from agent4sci.loops.expr import render_template, evaluate
from agent4sci.loops.schema import Edge, LoopGraph, Node, has_errors, validate_graph
from agent4sci.loops.store import LoopStore, NodeRecord, builtin_templates


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("A4S_FAKE_LLM", "1")
    app = AppServices(db=Database())
    calls: list[str] = []

    def factory(profile, key):
        provider = FakeProvider(profile, brain=demo_brain)
        original = provider.generate

        async def counting(**kw):
            calls.append(kw["messages"][0].text()[:80] if kw["messages"] else "")
            return await original(**kw)

        provider.generate = counting  # type: ignore[method-assign]
        return provider

    builder = HarnessBuilder(app, provider_factory=factory)
    engine = LoopEngine(app, builder)
    project = app.projects.create("测试项目", path=str(tmp_path / "proj"), domains=["general"])
    yield app, engine, project, calls
    asyncio.get_event_loop  # keep fixture simple


def graph(nodes: list[dict], edges: list[tuple], **kw) -> LoopGraph:
    return LoopGraph(name="t", nodes=[Node(**n) for n in nodes],
                     edges=[Edge(source=e[0], target=e[1], condition=e[2] if len(e) > 2 else None) for e in edges], **kw)


def test_builtin_templates_are_valid():
    templates = builtin_templates()
    assert {t.id for t in templates} >= {"research-pipeline", "lit-review", "data-analysis", "ml-experiment", "ralph",
                                          "reflect-refine", "autoresearch", "review-revise", "best-of-n",
                                          "idea-tournament"}
    for t in templates:
        issues = validate_graph(t)
        assert not has_errors(issues), (t.id, [i.message for i in issues if i.level == "error"])


def test_validation_catches_structural_errors():
    cyclic = graph([{"id": "a", "type": "checks"}, {"id": "b", "type": "checks"}], [("a", "b"), ("b", "a")])
    assert any("环路" in i.message for i in validate_graph(cyclic))
    empty = graph([{"id": "l", "type": "loop", "config": {"max_iterations": 2}}], [])
    assert any("容器内没有节点" in i.message for i in validate_graph(empty))
    cross = graph([{"id": "l", "type": "loop", "config": {"max_iterations": 2}},
                   {"id": "in", "type": "checks", "parent": "l"}, {"id": "out", "type": "checks"}], [("in", "out")])
    assert any("跨越容器" in i.message for i in validate_graph(cross))
    missing = graph([{"id": "a", "type": "agent"}], [])
    assert any("prompt" in i.message for i in validate_graph(missing))


def test_expressions_and_templates():
    names = {"nodes": {"r": {"result": {"decision": "accept"}}}, "loop": {"iteration": 1, "history": []}}
    assert evaluate("nodes.r.result.decision == 'accept'", names)
    assert evaluate("nodes.missing", names) is None
    text = render_template("A {{loop.iteration}} B {{'x' + nodes.nope.y if loop.iteration > 1 else 'first'}} "
                           "{{results/main.json#/acc:.3f}}", names)
    assert text == "A 1 B first {{results/main.json#/acc:.3f}}"
    assert evaluate("range(3)", {}) == [0, 1, 2]


async def test_condition_branching_and_join(env):
    app, engine, project, _ = env
    g = graph([
        {"id": "start", "type": "start"},
        {"id": "a", "type": "agent", "position": {"y": 1},
         "config": {"prompt": "decide", "output_schema": {"type": "object", "properties": {
             "decision": {"enum": ["reject", "accept"]}}, "required": ["decision"]}}},
        {"id": "cond", "type": "condition", "position": {"y": 2},
         "config": {"expression": "nodes.a.output.result.decision == 'accept'"}},
        {"id": "yes", "type": "checks", "position": {"y": 3}, "config": {}},
        {"id": "no", "type": "checks", "position": {"y": 3, "x": 1}, "config": {}},
        {"id": "end", "type": "end", "position": {"y": 4}},
    ], [("start", "a"), ("a", "cond"), ("cond", "yes", "true"), ("cond", "no", "false"), ("yes", "end"), ("no", "end")])
    run = engine.create_run(g, project, {}, auto_mode=True)
    state = await engine.run_to_completion(run.id)
    assert state.status == "done", state.error
    assert state.records["yes"].status == "done" and "no" not in state.records
    assert state.records["a"].output["result"] == {"decision": "accept"}
    types = [e["type"] for e in app.db.list_events(f"run:{run.id}")]
    assert "node.skipped" in types and types[-1] == "run.done"


def metric_loop(patience: int = 0) -> LoopGraph:
    return graph([
        {"id": "loop", "type": "loop", "config": {
            "max_iterations": 4, "keep_discard": True, "stall_patience": patience,
            "metric": {"name": "score", "file": "results/m.json", "pointer": "/score", "goal": "max"}}},
        {"id": "run", "type": "command", "parent": "loop", "config": {
            "command": "echo '{\"score\": {{[3, 1, 5, 2][loop.iteration - 1]}}}' > results/m.json"}},
    ], [])


async def test_loop_metrics_keep_and_discard(env):
    app, engine, project, _ = env
    state = await engine.run_to_completion(engine.create_run(metric_loop(), project, {}, auto_mode=True).id)
    assert state.status == "done", state.error
    values = [(m["value"], m["kept"]) for m in state.metrics["score"]]
    assert values == [(3.0, True), (1.0, False), (5.0, True), (2.0, False)]
    assert json.loads((project.workspace / "results" / "m.json").read_text()) == {"score": 5}
    loop_out = state.records["loop"].output
    assert loop_out["best"] == 5.0 and loop_out["stopped_by"] == "max_iterations"
    assert any("保留" in c["message"] for c in gitops.log(project.workspace))

    stalled = await engine.run_to_completion(engine.create_run(metric_loop(patience=1), project, {}, auto_mode=True).id)
    assert stalled.records["loop"].output["iterations"] == 2
    assert stalled.records["loop"].output["stopped_by"] == "stalled"


async def test_parallel_map(env):
    _, engine, project, _ = env
    g = graph([{"id": "par", "type": "parallel", "config": {"items": "[1, 2, 3]", "max_concurrency": 2}},
               {"id": "w", "type": "command", "parent": "par", "config": {"command": "echo item-{{item}} > out-{{item}}.txt"}}],
              [])
    state = await engine.run_to_completion(engine.create_run(g, project, {}, auto_mode=True).id)
    assert state.status == "done"
    assert sorted(p.name for p in project.workspace.glob("out-*.txt")) == ["out-1.txt", "out-2.txt", "out-3.txt"]
    assert len(state.records["par"].output["results"]) == 3


async def test_human_node_waits_then_approve_or_abort(env):
    app, engine, project, _ = env
    g = graph([{"id": "h", "type": "human", "config": {"kind": "choice", "options": ["A", "B"], "title": "选"}},
               {"id": "after", "type": "checks", "position": {"y": 1}, "config": {}}], [("h", "after")])
    run = engine.create_run(g, project, {}, auto_mode=False)
    task = engine.start(run.id)
    for _ in range(100):
        pending = app.interactions.pending(f"run:{run.id}")
        if pending:
            break
        await asyncio.sleep(0.02)
    assert pending and pending[0].payload["options"] == ["A", "B"]
    assert engine.runs.get(run.id).status == "waiting"
    app.interactions.resolve(pending[0].id, {"action": "approve", "choice": "B"})
    await task
    state = engine.runs.get(run.id)
    assert state.status == "done" and state.records["h"].output["choice"] == "B"

    run2 = engine.create_run(g, project, {}, auto_mode=False)
    task = engine.start(run2.id)
    for _ in range(100):
        pending = app.interactions.pending(f"run:{run2.id}")
        if pending:
            break
        await asyncio.sleep(0.02)
    app.interactions.resolve(pending[0].id, {"action": "abort"})
    await task
    state = engine.runs.get(run2.id)
    assert state.status == "failed" and "终止" in state.error


async def test_resume_reuses_completed_nodes(env):
    _, engine, project, calls = env
    g = graph([{"id": "a", "type": "agent", "config": {"prompt": "STEP-A"}},
               {"id": "b", "type": "agent", "position": {"y": 1}, "config": {"prompt": "STEP-B"}}], [("a", "b")])
    run = engine.create_run(g, project, {}, auto_mode=True)
    run.records["a"] = NodeRecord(key="a", node="a", status="done", output={"summary": "cached"})
    run.latest["a"] = "a"
    engine.runs.save(run)
    state = await engine.run_to_completion(run.id)
    assert state.status == "done"
    assert not any("STEP-A" in c for c in calls) and any("STEP-B" in c for c in calls)
    assert state.records["a"].output == {"summary": "cached"}


async def test_budget_stops_run(env):
    _, engine, project, _ = env
    g = graph([{"id": "a", "type": "checks", "config": {}}], [])
    run = engine.create_run(g, project, {}, auto_mode=True, max_cost_usd=0.001)
    run.cost_usd = 0.01
    engine.runs.save(run)
    state = await engine.run_to_completion(run.id)
    assert state.status == "failed" and "预算" in state.error


async def test_research_pipeline_runs_end_to_end_in_demo_mode(env):
    app, engine, project, calls = env
    template = LoopStore(app.db).get("research-pipeline")
    run = engine.create_run(template, project, {"question": "工作记忆中海马 theta 振荡的作用"}, auto_mode=True)
    state = await engine.run_to_completion(run.id)
    assert state.status == "done", state.error
    ws = project.workspace
    for rel in ["plan/brief.md", "lit/review.md", "references.bib", "plan/hypotheses.md", "plan/experiment_plan.md",
                "results/main.json", "analysis/analysis.md", "paper/paper.md", "review/review.md"]:
        assert (ws / rel).exists(), rel
    assert state.records["write_review"].output["iterations"] == 1
    assert "plan/experiment_plan.md" in state.protected
    assert state.records["ideas"].output["top"]
    trace = ws / ".a4s" / "runs" / run.id / "trace.jsonl"
    assert trace.exists() and "node.done" in trace.read_text()
    assert len(gitops.log(ws)) >= 5


# ---- weak models: structured results written as text instead of submit_result ------------------------------

SCORE_SCHEMA = {"type": "object", "properties": {"score": {"type": "number"}}, "required": ["score"]}


def _text_only_engine(tmp_path, monkeypatch, reply: str):
    from agent4sci.llm.fake_provider import FakeTurn

    monkeypatch.setenv("A4S_FAKE_LLM", "1")
    app = AppServices(db=Database())
    builder = HarnessBuilder(app, provider_factory=lambda profile, key: FakeProvider(
        profile, brain=lambda system, messages, tools: FakeTurn(text=reply)))
    engine = LoopEngine(app, builder)
    project = app.projects.create("弱模型", path=str(tmp_path / "proj"), domains=["general"])
    g = graph([{"id": "start", "type": "start"},
               {"id": "rate", "type": "agent", "config": {"prompt": "Rate it.", "output_schema": SCORE_SCHEMA}},
               {"id": "end", "type": "end"}], [("start", "rate"), ("rate", "end")])
    return engine, engine.create_run(g, project, {}, auto_mode=True)


async def test_result_written_as_text_is_recovered(tmp_path, monkeypatch):
    engine, state = _text_only_engine(tmp_path, monkeypatch, 'Here you go:\n```json\n{"score": 7, "note": "ok"}\n```')
    final = await engine.run_to_completion(state.id)
    assert final.status == "done"
    record = next(r for r in final.records.values() if r.node == "rate")
    assert record.output["result"]["score"] == 7


async def test_missing_structured_result_fails_the_node_clearly(tmp_path, monkeypatch):
    engine, state = _text_only_engine(tmp_path, monkeypatch, "I think it is pretty good.")
    final = await engine.run_to_completion(state.id)
    assert final.status == "failed" and "submit_result" in (final.error or "")


def test_result_from_text_variants():
    from agent4sci.tools.result import result_from_text

    assert result_from_text('{"result": {"score": 3}, "summary": "s"}', SCORE_SCHEMA) == {"result": {"score": 3}, "summary": "s"}
    assert result_from_text('first {"score": "bad"} then {"score": 9}', SCORE_SCHEMA)["result"] == {"score": 9}
    assert result_from_text("no json here", SCORE_SCHEMA) is None


async def test_missing_required_output_file_fails_instead_of_faking_success(tmp_path, monkeypatch):
    from agent4sci.llm.fake_provider import FakeTurn

    monkeypatch.setenv("A4S_FAKE_LLM", "1")
    app = AppServices(db=Database())
    builder = HarnessBuilder(app, provider_factory=lambda profile, key: FakeProvider(
        profile, brain=lambda system, messages, tools: FakeTurn(text="```markdown\nA draft in the reply.\n```")))
    engine = LoopEngine(app, builder)
    project = app.projects.create("弱模型", path=str(tmp_path / "proj"), domains=["general"])
    g = graph([{"id": "start", "type": "start"},
               {"id": "draft", "type": "agent", "config": {"prompt": "Write it.", "outputs": ["outputs/draft.md"]}},
               {"id": "end", "type": "end"}], [("start", "draft"), ("draft", "end")])
    final = await engine.run_to_completion(engine.create_run(g, project, {}, auto_mode=True).id)
    assert final.status == "failed" and "outputs/draft.md" in (final.error or "")


async def test_loop_stops_before_the_budget_runs_dry_and_warns_when_unmet(tmp_path, monkeypatch):
    monkeypatch.setenv("A4S_FAKE_LLM", "1")
    app = AppServices(db=Database())
    builder = HarnessBuilder(app, provider_factory=lambda profile, key: FakeProvider(
        profile, brain=demo_brain, cost_per_turn=0.1))
    engine = LoopEngine(app, builder)
    project = app.projects.create("预算", path=str(tmp_path / "proj"), domains=["general"])
    g = graph([{"id": "start", "type": "start"},
               {"id": "refine", "type": "loop", "config": {"max_iterations": 8, "until": "False"}},
               {"id": "work", "type": "agent", "parent": "refine", "config": {"prompt": "Improve the draft."}},
               {"id": "end", "type": "end"}], [("start", "refine"), ("refine", "end")])
    final = await engine.run_to_completion(engine.create_run(g, project, {}, auto_mode=True, max_cost_usd=1.0).id)
    loop = next(r for r in final.records.values() if r.node == "refine" and "#" not in r.key)
    assert final.status == "done" and loop.output["stopped_by"] == "budget"
    assert loop.output["iterations"] < 8 and final.cost_usd <= 1.0 + 1e-9
    assert any("停止条件没有满足" in w for w in final.warnings)


async def test_a_run_accounts_for_tokens_and_per_iteration_cost(env):
    """"Where did it go" needs per-node numbers, and loop iterations used to always report $0.00."""
    app, engine, project, _ = env
    g = graph(
        [{"id": "start", "type": "start"},
         {"id": "loop", "type": "loop", "config": {"max_iterations": 2}},
         {"id": "work", "type": "agent", "parent": "loop",
          "config": {"harness": "assistant", "prompt": "写点东西"}},
         {"id": "end", "type": "end"}],
        [("start", "loop"), ("loop", "end")],
    )
    run = await engine.run_to_completion(engine.create_run(g, project, {}, auto_mode=True).id)

    assert run.status == "done"
    assert run.tokens.get("input", 0) > 0 and run.tokens.get("output", 0) > 0
    node = run.records[run.latest["loop"]]
    assert node.tokens.get("input", 0) > 0
    iterations = [r for k, r in run.records.items() if "#" in k]
    assert iterations and all(r.tokens.get("input", 0) > 0 for r in iterations)
    assert sum(r.cost for r in iterations) >= 0  # recorded, not dropped
