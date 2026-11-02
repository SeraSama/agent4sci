"""a4s — command-line interface.

Shares the desktop app's data directory, keychain entries, projects, loops and memory, so anything started
here shows up in the app and vice versa. Human checkpoints and tool approvals are answered in the terminal.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.prompt import Confirm, Prompt
from rich.table import Table

if TYPE_CHECKING:  # pragma: no cover
    from .core.app import AppServices
    from .core.events import Event
    from .core.projects import Project

app = typer.Typer(help="Agent4Sci：自动化科研 Agent（命令行）", no_args_is_help=True, add_completion=False,
                  rich_markup_mode="rich")
projects_app = typer.Typer(help="研究项目", no_args_is_help=True)
models_app = typer.Typer(help="模型档案（base URL、API Key、参数）", no_args_is_help=True)
skills_app = typer.Typer(help="技能：检索、安装、扫描", no_args_is_help=True)
mcp_app = typer.Typer(help="MCP 服务器", no_args_is_help=True)
memory_app = typer.Typer(help="记忆", no_args_is_help=True)
loops_app = typer.Typer(help="Loop 模板与运行", no_args_is_help=True)
for sub, name in ((projects_app, "projects"), (models_app, "models"), (skills_app, "skills"), (mcp_app, "mcp"),
                  (memory_app, "memory"), (loops_app, "loops")):
    app.add_typer(sub, name=name)

console = Console()
err = Console(stderr=True)
MEMORY_MODES = ("global", "project", "conversation", "none")


# ---- shared helpers ------------------------------------------------------------------------------------------

def services() -> "AppServices":
    from .core.app import AppServices
    from .core.envfix import apply_system_proxy

    apply_system_proxy()
    svc = AppServices()
    svc.models.ensure_env_profiles()
    return svc


def fail(message: str, code: int = 1) -> None:
    err.print(f"[red]✗[/red] {escape(message)}")
    raise typer.Exit(code)


def run_async(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        err.print("\n[yellow]已中断[/yellow]")
        raise typer.Exit(130) from None


def find_project(svc: "AppServices", ref: str | None, *, required: bool = True) -> "Project | None":
    """Resolve a project by id, name or folder; default to the project containing the current directory."""
    projects = svc.projects.list()
    if ref:
        for p in projects:
            if ref in (p.id, p.name):
                return p
        path = Path(ref).expanduser()
        if path.is_dir():
            return svc.projects.open_existing(str(path))
        fail(f"找不到项目：{ref}（可用 `a4s projects list` 查看）")
    cwd = Path.cwd().resolve()
    for p in projects:
        root = Path(p.path).resolve()
        if cwd == root or root in cwd.parents:
            return p
    if (cwd / ".a4s" / "project.json").exists():
        return svc.projects.open_existing(str(cwd))
    if required:
        fail("需要一个项目：用 --project 指定，或在项目文件夹里运行（`a4s init` 创建）")
    return None


def parse_kv(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        if "=" not in item:
            fail(f"参数格式应为 KEY=VALUE：{item}")
        key, value = item.split("=", 1)
        out[key.strip()] = value
    return out


class Printer:
    """Renders bus events for one conversation or run in the terminal."""

    def __init__(self, prefix: str, *, thinking: bool = False, verbose: bool = False) -> None:
        self.prefix = prefix
        self.thinking = thinking
        self.verbose = verbose
        self.streaming = False

    def _newline(self) -> None:
        if self.streaming:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.streaming = False

    def __call__(self, event: "Event") -> None:
        if not event.topic.startswith(self.prefix):
            return
        d, kind = event.data, event.type
        nested = d.get("parent_span") is not None
        indent = "    " if nested else ""
        if kind == "llm.text.delta" and not nested:
            sys.stdout.write(d.get("text", ""))
            sys.stdout.flush()
            self.streaming = True
        elif kind == "llm.reasoning.delta" and self.thinking and not nested:
            console.print(d.get("text", ""), style="dim italic", end="")
            self.streaming = True
        elif kind == "tool.call":
            self._newline()
            args = json.dumps(d.get("input", {}), ensure_ascii=False)
            console.print(f"{indent}[cyan]→ {escape(str(d.get('name')))}[/cyan] [dim]{escape(args[:160])}[/dim]", highlight=False)
        elif kind == "tool.result" and (self.verbose or d.get("is_error")):
            mark = "[red]✗[/red]" if d.get("is_error") else "[green]✓[/green]"
            console.print(f"{indent}  {mark} [dim]{escape(str(d.get('preview', ''))[:200])}[/dim]", highlight=False)
        elif kind == "skill.activated":
            self._newline()
            console.print(f"{indent}[magenta]✦ 激活技能 {escape(str(d.get('name')))}[/magenta]")
        elif kind == "agent.compaction":
            self._newline()
            console.print("[yellow]… 上下文已压缩为摘要[/yellow]")
        elif kind == "agent.error":
            self._newline()
            console.print(f"[red]错误：{escape(str(d.get('error')))}[/red]")
        elif kind == "agent.done" and not nested:
            self._newline()
            if d.get("status") not in ("done", None):
                console.print(f"[yellow]（{d.get('status')}）[/yellow]")
            if self.verbose:
                usage = d.get("usage") or {}
                tokens = f"输入 {usage.get('input_tokens', 0) + usage.get('cache_read_tokens', 0):,}" \
                         f"（缓存 {usage.get('cache_read_tokens', 0):,}）· 输出 {usage.get('output_tokens', 0):,} tokens"
                cost = "费用未知（模型没有设置价格）" if d.get("cost_unknown") else f"${d.get('cost', 0):.4f}"
                console.print(f"[dim]{d.get('turns')} 轮 · {d.get('tool_calls', 0)} 次工具 · {tokens} · {cost}[/dim]")
        elif kind == "node.started":
            self._newline()
            console.print(f"[bold blue]▶ {escape(str(d.get('label') or d.get('node')))}[/bold blue] [dim]{escape(str(d.get('key', '')))}[/dim]")
        elif kind == "node.done":
            console.print(f"[green]✓ {d.get('node')}[/green] [dim]${d.get('cost', 0):.4f}[/dim]")
        elif kind == "node.note":
            console.print(f"[yellow]  · {escape(str(d.get('note', '')))}[/yellow]")
        elif kind == "node.failed":
            console.print(f"[red]✗ {escape(str(d.get('node')))}：{escape(str(d.get('error')))}[/red]")
        elif kind == "loop.iteration":
            metric = d.get("metric")
            extra = f" · {d.get('metric_name')} = {metric}" if metric is not None else ""
            kept = {True: " · 保留", False: " · 回滚"}.get(d.get("kept"), "")
            console.print(f"[blue]⟳ {d.get('node')} 第 {d.get('iteration')} 轮{extra}{kept}[/blue]")
        elif kind in ("run.done", "run.failed", "run.cancelled", "run.paused"):
            self._newline()
            style = {"run.done": "green", "run.failed": "red"}.get(kind, "yellow")
            console.print(f"[{style}]{kind}[/{style}] {escape(str(d.get('error') or ''))}")


async def terminal_handler(interaction: Any) -> dict[str, Any]:
    """Answer approvals, questions and checkpoints in the terminal (runs the prompt off the event loop)."""
    p = interaction.payload

    def ask() -> dict[str, Any]:
        console.print()
        if interaction.kind == "approval":
            console.print(f"[bold yellow]需要批准[/bold yellow] {escape(interaction.title)}")
            console.print(json.dumps(p.get("input", {}), ensure_ascii=False, indent=2)[:1500], style="dim", highlight=False)
            choice = Prompt.ask("允许？（y 允许 / n 拒绝 / a 本次都允许）", choices=["y", "n", "a"], default="y")
            if choice == "n":
                return {"decision": "deny", "note": Prompt.ask("拒绝理由（可空）", default="")}
            return {"decision": "allow", "always": choice == "a"}
        if interaction.kind == "question":
            console.print(f"[bold yellow]Agent 提问[/bold yellow] {escape(interaction.title)}")
            if p.get("context"):
                console.print(escape(str(p["context"])), style="dim")
            options = p.get("options") or []
            for i, opt in enumerate(options, 1):
                console.print(f"  {i}. {escape(str(opt))}")
            answer = Prompt.ask("回答")
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                answer = options[int(answer) - 1]
            return {"answer": answer}
        # checkpoint
        console.print(f"[bold yellow]人工检查点[/bold yellow] {escape(interaction.title)}")
        if p.get("message"):
            console.print(Markdown(str(p["message"])[:4000]))
        for f in p.get("files") or []:
            console.print(f"  [dim]文件：{escape(str(f))}[/dim]")
        options = p.get("options") or []
        if p.get("kind") == "choose" and options:
            for i, opt in enumerate(options, 1):
                console.print(f"  {i}. {escape(str(opt))}")
            idx = Prompt.ask("选择编号", default="1")
            choice = options[int(idx) - 1] if idx.isdigit() and 1 <= int(idx) <= len(options) else options[0]
            return {"action": "approve", "choice": choice}
        if p.get("kind") == "input":
            return {"action": "approve", "input": Prompt.ask("输入")}
        action = Prompt.ask("操作", choices=["approve", "feedback", "abort"], default="approve")
        feedback = Prompt.ask("反馈") if action == "feedback" else ""
        if action == "abort" and not Confirm.ask("确定终止整个运行？", default=False):
            action = "approve"
        return {"action": action, "feedback": feedback}

    def ask_or_default() -> dict[str, Any]:
        try:
            return ask()
        except EOFError:  # stdin closed (piped / non-interactive): never approve silently
            err.print("[yellow]标准输入已结束，无法回答；非交互环境请使用 --auto[/yellow]")
            return {"approval": {"decision": "deny", "note": "无人值守"}, "question": {"answer": ""}}.get(
                interaction.kind, {"action": "abort"})

    return await asyncio.to_thread(ask_or_default)


# ---- init / projects ---------------------------------------------------------------------------------------

@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="项目文件夹（不存在则创建）"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="项目名称（默认取文件夹名）"),
    domain: list[str] = typer.Option([], "--domain", "-d", help="领域：general / ai-ml / bio-chem-materials / neuro"),
    venv: bool = typer.Option(False, "--venv", help="用 uv 创建 Python 环境（numpy、pandas、scipy、matplotlib）"),
    description: str = typer.Option("", "--description"),
) -> None:
    """把一个文件夹初始化为研究项目工作区。"""
    from .core.projects import create_venv

    svc = services()
    folder = path.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    project = svc.projects.open_existing(str(folder))
    project.name = name or project.name
    project.description = description or project.description
    project.domains = domain or project.domains
    svc.projects.save(project)
    (folder / ".a4s" / "project.json").write_text(project.model_dump_json(indent=2), "utf-8")
    console.print(f"[green]✓[/green] 项目 [bold]{project.name}[/bold]（{project.id}）：{folder}")
    if venv:
        with console.status("正在创建 Python 环境（uv）…"):
            result = run_async(create_venv(folder))
        if result.get("ok"):
            console.print(f"[green]✓[/green] Python 环境：{result['python']}")
        else:
            console.print(f"[yellow]Python 环境未创建：{result.get('error')}[/yellow]")


@projects_app.command("list")
def projects_list() -> None:
    """列出项目。"""
    table = Table("ID", "名称", "领域", "路径")
    for p in services().projects.list():
        table.add_row(p.id, p.name, ", ".join(p.domains), p.path)
    console.print(table)


# ---- chat ---------------------------------------------------------------------------------------------------

@app.command()
def chat(
    message: Optional[str] = typer.Argument(None, help="只发送这一条消息并退出；省略则进入交互模式"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="项目 ID、名称或路径"),
    memory: Optional[str] = typer.Option(None, "--memory", "-m", help="记忆模式：global / project / conversation / none"),
    temporary: bool = typer.Option(False, "--temporary", "-t", help="临时对话：不保存、不读写记忆"),
    harness: str = typer.Option("assistant", "--harness", help="Harness ID（见 `a4s loops harnesses`）"),
    model: Optional[str] = typer.Option(None, "--model", help="模型档案 ID（默认用默认模型）"),
    resume: Optional[str] = typer.Option(None, "--resume", help="继续一个已有对话（对话 ID）"),
    thinking: bool = typer.Option(False, "--thinking", help="显示推理摘要"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示工具结果与用量"),
) -> None:
    """与研究助手对话（流式输出，审批与提问在终端里回答）。"""
    if memory and memory not in MEMORY_MODES:
        fail(f"记忆模式应为 {' / '.join(MEMORY_MODES)}")
    run_async(_chat(message, project, memory, temporary, harness, model, resume, thinking, verbose))


async def _chat(message: str | None, project_ref: str | None, memory: str | None, temporary: bool, harness: str,
                model: str | None, resume: str | None, thinking: bool, verbose: bool) -> None:
    from .agent.harness import HarnessBuilder
    from .server.conversations import ConversationManager

    svc = services()
    if not svc.models.list():
        fail("还没有模型：先运行 `a4s models add`，或设置 ANTHROPIC_API_KEY / OPENAI_API_KEY 环境变量")
    svc.interactions.handler = terminal_handler
    proj = find_project(svc, project_ref, required=False)
    convs = ConversationManager(svc, HarnessBuilder(svc))
    if resume:
        conv = convs.get(resume)
    else:
        conv = convs.create(project_id=proj.id if proj else None, memory_mode=memory, harness_id=harness,
                            model_id=model, temporary=temporary)
    printer = Printer(f"conv:{conv.id}", thinking=thinking, verbose=verbose)
    svc.bus.add_sink(printer)
    mode = "临时 · 无记忆" if conv.temporary else f"记忆：{conv.memory_mode}"
    console.print(f"[dim]对话 {conv.id} · {mode}{' · 项目 ' + proj.name if proj else ''} · Ctrl+C 停止当前回复，Ctrl+D 退出[/dim]")

    async def turn(text: str) -> None:
        await convs.send(conv.id, text)
        task = convs.tasks.get(conv.id)
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass
        printer._newline()

    try:
        if message:
            await turn(message)
            return
        while True:
            try:
                text = await asyncio.to_thread(Prompt.ask, "\n[bold green]你[/bold green]")
            except EOFError:
                break
            if not text.strip():
                continue
            if text.strip() in ("/exit", "/quit"):
                break
            try:
                await turn(text)
            except KeyboardInterrupt:
                convs.stop(conv.id)
    finally:
        await convs.aclose()
        await svc.aclose()
        if conv.temporary:
            console.print("[dim]临时对话已丢弃[/dim]")
        else:
            console.print(f"[dim]对话已保存：a4s chat --resume {conv.id}[/dim]")


# ---- loops & runs ---------------------------------------------------------------------------------------------

@loops_app.command("list")
def loops_list() -> None:
    """列出 Loop（内置模板与自建）。"""
    from .loops.store import LoopStore

    table = Table("ID", "名称", "分类", "节点数")
    for g in LoopStore(services().db).list():
        table.add_row(g.id, g.name, g.category, str(len(g.nodes)))
    console.print(table)


@loops_app.command("harnesses")
def harnesses_list() -> None:
    """列出 Harness 档案。"""
    from .agent.harness import HarnessStore

    table = Table("ID", "名称", "说明")
    for h in HarnessStore(services().db).list():
        table.add_row(h.id, h.name, h.description)
    console.print(table)


@app.command()
def run(
    loop: str = typer.Argument(..., help="Loop ID 或名称（见 `a4s loops list`），或 YAML 文件路径"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="项目 ID、名称或路径（默认当前目录所在项目）"),
    input: list[str] = typer.Option([], "--input", "-i", help="Loop 输入 KEY=VALUE，可重复"),
    auto: bool = typer.Option(False, "--auto", help="全自动：跳过所有人工检查点"),
    budget: Optional[float] = typer.Option(None, "--budget", help="预算上限（美元）"),
    model: Optional[str] = typer.Option(None, "--model", help="模型档案 ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """运行一个 Loop；人工检查点在终端里回答（--auto 则自动批准）。"""
    run_async(_run(loop, project, parse_kv(input), auto, budget, model, verbose, None))


@app.command()
def resume(run_id: str = typer.Argument(..., help="运行 ID"), verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """继续一个暂停或中断的运行（已完成的节点直接复用结果）。"""
    run_async(_run(None, None, {}, False, None, None, verbose, run_id))


async def _run(loop_ref: str | None, project_ref: str | None, inputs: dict[str, str], auto: bool,
               budget: float | None, model: str | None, verbose: bool, run_id: str | None) -> None:
    from .agent.harness import HarnessBuilder
    from .loops.engine import LoopEngine
    from .loops.schema import LoopGraph

    svc = services()
    svc.interactions.handler = terminal_handler
    engine = LoopEngine(svc, HarnessBuilder(svc))
    if run_id is None:
        proj = find_project(svc, project_ref)
        assert proj is not None
        graph = None
        path = Path(loop_ref or "")
        if path.suffix in (".yaml", ".yml") and path.exists():
            import yaml

            graph = LoopGraph(**yaml.safe_load(path.read_text("utf-8")))
        else:
            graph = next((g for g in engine.loops.list() if loop_ref in (g.id, g.name)), None)
        if graph is None:
            fail(f"找不到 Loop：{loop_ref}")
        assert graph is not None
        for name, spec in graph.inputs.items():
            if name not in inputs and spec.required and spec.default is None:
                inputs[name] = Prompt.ask(spec.label or name)
        try:
            state = engine.create_run(graph, proj, inputs, auto_mode=auto, max_cost_usd=budget, model_id=model)
        except ValueError as exc:
            fail(str(exc))
        run_id = state.id
        console.print(f"[dim]运行 {run_id} · {graph.name} · 项目 {proj.name}{' · 全自动' if auto else ''}[/dim]")
    printer = Printer(f"run:{run_id}", verbose=verbose)
    svc.bus.add_sink(printer)
    configs = [c for c in svc.mcp_configs() if c.enabled]
    try:
        if configs:
            await svc.mcp.start(configs)
        final = await engine.run_to_completion(run_id)
    except KeyboardInterrupt:
        engine.cancel(run_id)
        raise
    finally:
        await svc.aclose()
    style = {"done": "green", "failed": "red"}.get(final.status, "yellow")
    cost = "费用未知（模型没有设置价格）" if final.cost_unknown else f"${final.cost_usd:.4f}"
    console.print(f"[{style}]运行结束：{final.status}[/{style}] · {cost}"
                  + (f" · {escape(final.error)}" if final.error else ""))
    for warning in final.warnings:
        console.print(f"[yellow]⚠ {escape(warning)}[/yellow]")
    if final.status != "done":
        raise typer.Exit(1)


@app.command()
def status(run_id: Optional[str] = typer.Argument(None, help="运行 ID；省略则列出最近的运行")) -> None:
    """查看运行状态。"""
    from .loops.store import RunStore

    runs = RunStore(services().db)
    if run_id is None:
        table = Table("ID", "Loop", "状态", "成本", "更新")
        import datetime as dt

        for s in runs.list(limit=30):
            table.add_row(s.id, s.loop_name, s.status, f"${s.cost_usd:.4f}",
                          dt.datetime.fromtimestamp(s.updated).strftime("%m-%d %H:%M"))
        console.print(table)
        return
    state = runs.get(run_id)
    if state is None:
        fail(f"找不到运行：{run_id}")
    assert state is not None
    console.print(f"[bold]{state.loop_name}[/bold] · {state.status} · ${state.cost_usd:.4f}")
    if state.error:
        console.print(f"[red]{state.error}[/red]")
    table = Table("节点", "状态", "成本", "错误")
    for node, info in state.node_status().items():
        table.add_row(node, info["status"], f"${info.get('cost', 0):.4f}", (info.get("error") or "")[:80])
    console.print(table)


@app.command()
def rollback(
    commit: Optional[str] = typer.Argument(None, help="要回到的提交；省略则列出最近提交"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
) -> None:
    """列出或回滚项目工作区的 git 提交（回滚前会先保存当前改动）。"""
    from .loops import gitops

    proj = find_project(services(), project)
    assert proj is not None
    ws = Path(proj.path)
    if commit is None:
        import datetime as dt

        table = Table("提交", "时间", "说明")
        for c in gitops.log(ws, 20):
            table.add_row(c["sha"][:10], dt.datetime.fromtimestamp(c["time"]).strftime("%m-%d %H:%M"), c["message"])
        console.print(table)
        return
    if not Confirm.ask(f"把 {proj.name} 回滚到 {commit}？", default=False):
        raise typer.Exit()
    gitops.commit_all(ws, "a4s: rollback 前自动保存")
    ok = gitops.rollback(ws, commit)
    console.print("[green]✓ 已回滚[/green]" if ok else "[red]回滚失败[/red]")


# ---- models ---------------------------------------------------------------------------------------------------

@models_app.command("list")
def models_list() -> None:
    """列出模型档案。"""
    svc = services()
    default = svc.models.default_id()
    table = Table("ID", "名称", "类型", "模型", "Base URL", "Key", "默认")
    for m in svc.models.list():
        key = svc.models.key_status(m)
        table.add_row(m.id, m.name, m.provider, m.model, m.base_url or "（官方）",
                      key.get("masked") or ("env" if key.get("resolved") else "—"), "★" if m.id == default else "")
    console.print(table)


@models_app.command("presets")
def models_presets() -> None:
    """列出内置服务商预设。"""
    from .core.models import PRESETS

    table = Table("预设", "名称", "类型", "Base URL", "环境变量")
    for p in PRESETS.values():
        table.add_row(p.id, p.label, p.provider, p.base_url or "（官方）", p.api_key_env or "")
    console.print(table)


@models_app.command("add")
def models_add(
    preset: str = typer.Option("anthropic", "--preset", help="服务商预设（见 `a4s models presets`）"),
    model: Optional[str] = typer.Option(None, "--model", help="模型名，例如 claude-opus-5、gpt-5、deepseek-chat"),
    name: Optional[str] = typer.Option(None, "--name", help="显示名称"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="自定义 base URL"),
    context: Optional[int] = typer.Option(None, "--context", help="上下文长度（tokens）；Ollama 用它设置 num_ctx"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="不填则交互输入（保存在系统钥匙串）"),
    default: bool = typer.Option(False, "--default", help="设为默认模型"),
    test: bool = typer.Option(True, "--test/--no-test", help="保存后测试连接"),
) -> None:
    """添加一个模型档案。"""
    from .core.models import PRESETS, profile_from_preset

    if preset not in PRESETS:
        fail(f"未知预设：{preset}")
    overrides: dict[str, Any] = {}
    if model:
        overrides["model"] = model
    if name:
        overrides["name"] = name
    if base_url:
        overrides["base_url"] = base_url
    if context:
        overrides["context_window"] = context
    profile = profile_from_preset(preset, **overrides)
    if not profile.model:
        profile.model = Prompt.ask("模型名")
    svc = services()
    svc.models.save(profile)
    needs_key = PRESETS[preset].needs_key
    if api_key is None and needs_key and not (profile.api_key_env and os.environ.get(profile.api_key_env)):
        api_key = Prompt.ask("API Key（输入不显示）", password=True) or None
    if api_key:
        svc.models.set_api_key(profile.id, api_key)
    if default or svc.models.default_id() in (None, profile.id):
        svc.models.set_default(profile.id)
    console.print(f"[green]✓[/green] 已保存模型 {profile.name}（{profile.id}）")
    if test:
        _models_test(svc, profile.id)


@models_app.command("test")
def models_test(model_id: str = typer.Argument(..., help="模型档案 ID")) -> None:
    """测试模型连接与工具调用。"""
    _models_test(services(), model_id)


def _models_test(svc: "AppServices", model_id: str) -> None:
    from .llm.probe import test_connection

    profile = svc.models.get(model_id)
    if profile is None:
        fail(f"找不到模型：{model_id}")
    assert profile is not None
    with console.status(f"测试 {profile.name}…"):
        report = run_async(test_connection(profile, svc.models.resolve_api_key(profile)))
    if report.get("ok"):
        tools = {True: "支持", False: "未触发", None: "未测"}[report.get("tools_ok")]
        console.print(f"[green]✓ 连接成功[/green] {report.get('served_model')} · {report.get('latency_s')} 秒 · 工具调用：{tools}")
    else:
        fail(f"连接失败：{report.get('error')}")


@models_app.command("default")
def models_default(model_id: str = typer.Argument(...)) -> None:
    """设置默认模型。"""
    svc = services()
    if svc.models.get(model_id) is None:
        fail(f"找不到模型：{model_id}")
    svc.models.set_default(model_id)
    console.print(f"[green]✓[/green] 默认模型：{model_id}")


@models_app.command("remove")
def models_remove(model_id: str = typer.Argument(...)) -> None:
    """删除模型档案（同时删除钥匙串里的 Key）。"""
    svc = services()
    svc.models.set_api_key(model_id, None)
    console.print("[green]✓ 已删除[/green]" if svc.models.delete(model_id) else "[yellow]不存在[/yellow]")


# ---- skills ---------------------------------------------------------------------------------------------------

def _skill_target(svc: "AppServices", project_ref: str | None, to_project: bool) -> tuple[Path, Any]:
    from .core.paths import user_skills_dir
    from .skills.lock import LockFile

    if to_project:
        proj = find_project(svc, project_ref)
        assert proj is not None
        return Path(proj.path) / "skills" / "installed", LockFile(Path(proj.path) / "skills.lock")
    root = user_skills_dir()
    return root, LockFile(root / "skills.lock")


@skills_app.command("list")
def skills_list(project: Optional[str] = typer.Option(None, "--project", "-p")) -> None:
    """列出可用技能（项目、全局、内置）。"""
    from .skills.registry import SkillRegistry, default_roots

    svc = services()
    proj = find_project(svc, project, required=False)
    reg = SkillRegistry(default_roots(Path(proj.path) if proj else None))
    table = Table("名称", "来源", "信任", "状态", "许可证", "说明")
    for r in reg.list(include_inactive=True):
        table.add_row(r.name, r.root_kind, r.trust, r.status, r.license or "", r.description[:70])
    console.print(table)
    for e in reg.errors:
        err.print(f"[yellow]无法解析：{e}[/yellow]")


@skills_app.command("search")
def skills_search(query: str = typer.Argument(""), domain: Optional[str] = typer.Option(None, "--domain"),
                  limit: int = typer.Option(20, "--limit")) -> None:
    """在技能目录索引中检索（先运行 `a4s skills index` 建立索引）。"""
    from .skills.catalog import install_spec_for

    svc = services()
    if not svc.skill_index.entries:
        fail("技能索引为空：先运行 `a4s skills index`（需联网）")
    table = Table("名称", "许可证", "领域", "安装来源", "说明")
    for e in svc.skill_index.search(query, limit, domain):
        table.add_row(e.name, e.license or "?", ",".join(e.domains), install_spec_for(svc.catalog, e), e.description[:60])
    console.print(table)


@skills_app.command("index")
def skills_index(only: list[str] = typer.Option([], "--only", help="只索引这些来源 ID")) -> None:
    """联网下载目录中各技能仓库的清单并建立本地索引（只读元数据，不执行代码）。"""
    svc = services()

    async def build() -> Any:
        try:
            return await svc.skill_index.build(svc.catalog, svc.skill_hub, only=only or None,
                                               progress=lambda sid, msg: console.print(f"[dim]{sid}：{msg}[/dim]"))
        finally:
            await svc.aclose()

    info = run_async(build())
    for sid, item in (info or {}).items():
        if item.get("error"):
            console.print(f"[red]✗ {sid}：{item['error']}[/red]")
        else:
            console.print(f"[green]✓ {sid}[/green] {item.get('count', 0)} 个技能")


@skills_app.command("install")
def skills_install(
    source: str = typer.Argument(..., help="owner/repo[/path][@ref]、GitHub 链接、git 地址或本地路径"),
    select: list[str] = typer.Option([], "--select", "-s", help="只安装这些技能"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    to_project: bool = typer.Option(False, "--to-project", help="安装到项目（默认全局）"),
    allow_license: bool = typer.Option(False, "--allow-license", help="允许非开源许可证"),
    force: bool = typer.Option(False, "--force", help="忽略高风险扫描结果（不推荐）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只检查不安装"),
) -> None:
    """安装技能（检查许可证、安全扫描、固定 commit 并写入 skills.lock）。"""
    svc = services()
    root, lock = _skill_target(svc, project, to_project)

    async def go() -> list[dict[str, Any]]:
        try:
            return await svc.skill_hub.install(source, target_dir=root, lock=lock, select=select or None,
                                               allow_license=allow_license, force=force, dry_run=dry_run)
        finally:
            await svc.aclose()

    try:
        results = run_async(go())
    except Exception as exc:
        fail(str(exc))
    for r in results:
        status_ = r.get("status")
        style = {"installed": "green", "ok": "green"}.get(status_, "red")
        risk = r.get("scan", {}).get("risk") if isinstance(r.get("scan"), dict) else r.get("risk")
        console.print(f"[{style}]{status_:9}[/{style}] {r.get('name') or r.get('path')} "
                      f"[dim]{r.get('license') or ''} {('风险 ' + risk) if risk else ''} {r.get('reason') or ''}[/dim]")


@skills_app.command("remove")
def skills_remove(name: str, project: Optional[str] = typer.Option(None, "--project", "-p"),
                  to_project: bool = typer.Option(False, "--from-project", help="从项目中删除（默认全局）")) -> None:
    """删除已安装的技能。"""
    svc = services()
    root, lock = _skill_target(svc, project, to_project)
    console.print("[green]✓ 已删除[/green]" if svc.skill_hub.remove(name, target_dir=root, lock=lock) else "[yellow]不存在[/yellow]")


@skills_app.command("scan")
def skills_scan(path: Path = typer.Argument(..., help="技能文件夹")) -> None:
    """对一个技能文件夹做安全扫描。"""
    from .skills.scanner import scan_skill

    report = scan_skill(path)
    console.print(f"风险：[bold]{report.risk}[/bold] · {report.files} 个文件")
    for f in report.findings:
        console.print(f"  [{ {'high': 'red', 'medium': 'yellow'}.get(f.severity, 'dim') }]{f.severity}[/] {f.file}:{f.line or ''} {f.message}")


# ---- MCP ------------------------------------------------------------------------------------------------------

@mcp_app.command("list")
def mcp_list() -> None:
    """列出已配置的 MCP 服务器。"""
    from .mcp.manager import missing_refs

    table = Table("名称", "启用", "命令 / URL", "缺少变量")
    for c in services().mcp_configs():
        table.add_row(c.name, "✓" if c.enabled else "", c.url or " ".join([c.command or "", *c.args]),
                      ", ".join(missing_refs(c)))
    console.print(table)


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="名称（工具前缀）"),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="stdio：启动命令，如 uvx / npx"),
    arg: list[str] = typer.Option([], "--arg", "-a", help="命令参数，可重复"),
    env: list[str] = typer.Option([], "--env", "-e", help="环境变量 KEY=VALUE，值可写 ${VAR}"),
    url: Optional[str] = typer.Option(None, "--url", help="远程 HTTP 服务器地址"),
    header: list[str] = typer.Option([], "--header", help="请求头 Name=value"),
    catalog: bool = typer.Option(False, "--catalog", help="从精选列表按名称添加"),
) -> None:
    """添加 MCP 服务器并测试连接。"""
    from .mcp.manager import MCPServerConfig

    svc = services()
    if catalog:
        entry = next((m for m in svc.catalog.mcp_servers if m.name == name), None)
        if entry is None:
            fail(f"精选列表里没有 {name}：{', '.join(m.name for m in svc.catalog.mcp_servers)}")
        assert entry is not None
        cfg = MCPServerConfig(name=name, command=entry.command, args=entry.args, url=entry.url,
                              description=entry.label, origin="catalog")
    else:
        if not command and not url:
            fail("需要 --command 或 --url")
        cfg = MCPServerConfig(name=name, command=command, args=arg, env=parse_kv(env), url=url,
                              headers=parse_kv(header), origin="manual")
    svc.db.put("mcp", cfg.name, cfg.model_dump())
    console.print(f"[green]✓[/green] 已添加 {cfg.name}")
    _mcp_test(svc, cfg.name)


@mcp_app.command("test")
def mcp_test(name: str) -> None:
    """启动一次 MCP 服务器并列出工具。"""
    _mcp_test(services(), name)


def _mcp_test(svc: "AppServices", name: str) -> None:
    from .mcp.manager import MCPServerConfig, check_server

    data = svc.db.get("mcp", name)
    if data is None:
        fail(f"找不到 MCP 服务器：{name}")
    with console.status(f"连接 {name}…"):
        result = run_async(check_server(MCPServerConfig(**data)))
    if result.get("ok"):
        console.print(f"[green]✓ 已连接[/green] {len(result['tools'])} 个工具：{', '.join(result['tools'])}")
    else:
        fail(f"连接失败：{result.get('error')}")


@mcp_app.command("remove")
def mcp_remove(name: str) -> None:
    """删除 MCP 服务器配置。"""
    console.print("[green]✓ 已删除[/green]" if services().db.delete("mcp", name) else "[yellow]不存在[/yellow]")


@mcp_app.command("secret")
def mcp_secret(var: str = typer.Argument(..., help="变量名（配置中写作 ${VAR}）")) -> None:
    """把 ${VAR} 的值保存到系统钥匙串。"""
    value = Prompt.ask(f"{var} 的值（输入不显示，留空则删除）", password=True)
    svc = services()
    if value:
        svc.secrets.set(f"env:{var}", value)
    else:
        svc.secrets.delete(f"env:{var}")
    console.print("[green]✓ 已保存[/green]")


@mcp_app.command("search")
def mcp_search(query: str) -> None:
    """在官方 MCP 注册表中按名称检索。"""
    from .mcp.registry_api import config_from_registry, search_registry

    svc = services()

    async def go() -> list[Any]:
        try:
            return await search_registry(svc.http, query)
        finally:
            await svc.aclose()

    table = Table("名称", "版本", "启动方式", "说明")
    for s in run_async(go()):
        try:
            cfg = config_from_registry(s)
            how = cfg.url or " ".join([cfg.command or "", *cfg.args])
        except ValueError:
            how = "—"
        table.add_row(s.name, s.version, how, s.description[:60])
    console.print(table)


# ---- memory ---------------------------------------------------------------------------------------------------

def _memory_access(svc: "AppServices", project_ref: str | None) -> Any:
    from .agent.harness import HarnessContext, memory_roots
    from .memory.store import MemoryAccess

    proj = find_project(svc, project_ref, required=False)
    ctx = HarnessContext(workspace=Path(proj.path) if proj else Path.cwd(), project=proj)
    return MemoryAccess("global", memory_roots(ctx))


@memory_app.command("list")
def memory_list(project: Optional[str] = typer.Option(None, "--project", "-p")) -> None:
    """列出全局与项目记忆。"""
    acc = _memory_access(services(), project)
    for scope, store in acc.stores.items():
        console.print(f"[bold]{scope}[/bold] [dim]{store.root}[/dim]")
        for item in store.list():
            flag = " [yellow]⚠[/yellow]" if item.flagged else ""
            console.print(f"  {item.file}{flag} [dim]({item.type}) {item.description}[/dim]")


@memory_app.command("show")
def memory_show(scope: str, file: str, project: Optional[str] = typer.Option(None, "--project", "-p")) -> None:
    """显示一条记忆。"""
    store = _memory_access(services(), project).store(scope)
    path = store.root / file
    if not path.exists():
        fail(f"不存在：{path}")
    console.print(Markdown(path.read_text("utf-8")))


@memory_app.command("add")
def memory_add(scope: str = typer.Argument(..., help="global / project"), name: str = typer.Argument(...),
               body: str = typer.Argument(...), description: str = typer.Option("", "--description"),
               type: str = typer.Option("fact", "--type"), project: Optional[str] = typer.Option(None, "--project", "-p")) -> None:
    """写入一条记忆。"""
    item = _memory_access(services(), project).store(scope).write(name, body, description=description, type=type,
                                                                  source="cli")
    console.print(f"[green]✓[/green] {item.file}" + (f" [yellow]（被标记：{item.flagged}）[/yellow]" if item.flagged else ""))


@memory_app.command("delete")
def memory_delete(scope: str, file: str, project: Optional[str] = typer.Option(None, "--project", "-p")) -> None:
    """删除一条记忆。"""
    ok = _memory_access(services(), project).store(scope).delete(file)
    console.print("[green]✓ 已删除[/green]" if ok else "[yellow]不存在[/yellow]")


# ---- doctor ---------------------------------------------------------------------------------------------------

@app.command()
def doctor(network: bool = typer.Option(False, "--network", help="同时检查能否访问 arXiv / Crossref / GitHub")) -> None:
    """检查运行环境：Python、uv、git、Node、钥匙串、模型与服务密钥。"""
    from . import __version__
    from .core.app import SERVICE_KEYS
    from .core.envfix import find_executable
    from .core.paths import app_data_dir, default_projects_dir

    svc = services()
    rows: list[tuple[str, bool | None, str]] = [
        ("Agent4Sci", True, __version__),
        ("Python", sys.version_info >= (3, 11), platform.python_version()),
        ("系统", True, f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ("数据目录", os.access(app_data_dir(), os.W_OK), str(app_data_dir())),
        ("项目目录", True, str(default_projects_dir())),
        ("密钥存储", True, type(svc.secrets).__name__),
        ("网络代理", True if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") else None,
         re.sub(r"^\w+://(?:[^@/]*@)?", "", os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "")
         or "未使用（直连；系统代理也会自动采用）"),
    ]
    for exe, why in (("uv", "创建项目 Python 环境、运行技能脚本、uvx 启动 MCP"), ("git", "工作区版本记录与回滚"),
                     ("npx", "启动 Node 版 MCP 服务器（可选）")):
        path = find_executable(exe)
        rows.append((exe, bool(path) if exe != "npx" else (True if path else None), path or f"未找到：{why}"))
    models = svc.models.list()
    rows.append(("模型档案", bool(models), f"{len(models)} 个，默认 {svc.models.default_id() or '未设置'}"))
    for m in models:
        key = svc.models.key_status(m)
        rows.append((f"  {m.name}", bool(key.get("resolved") or m.is_local or m.provider == "fake"),
                     f"{m.provider} · {m.model} · Key "
                     + ("已配置" if key.get("resolved") else "无需" if m.is_local or m.provider == "fake" else "缺失")))
    for sid, (env, label) in SERVICE_KEYS.items():
        rows.append((f"  {label}", True if svc.service_key(sid) else None, "已配置" if svc.service_key(sid) else f"未配置（可选，{env}）"))
    rows.append(("联系邮箱", True if svc.contact_email() else None, svc.contact_email() or "未设置（Crossref/PubMed 礼貌池，可选）"))
    if network:
        async def ping() -> list[tuple[str, bool | None, str]]:
            out = []
            for label, url in (("arXiv", "https://export.arxiv.org/api/query?search_query=all:test&max_results=1"),
                               ("Crossref", "https://api.crossref.org/works?rows=1"),
                               ("GitHub", "https://api.github.com/rate_limit")):
                try:
                    resp = await svc.http.get(url, cache_ttl=0)
                    out.append((f"网络 {label}", resp.status_code < 400, f"HTTP {resp.status_code}"))
                except Exception as exc:
                    out.append((f"网络 {label}", False, str(exc)[:80]))
            await svc.aclose()
            return out

        rows += run_async(ping())
    table = Table("检查项", "", "详情")
    bad = 0
    for name, ok, detail in rows:
        mark = "[green]✓[/green]" if ok else ("[dim]–[/dim]" if ok is None else "[red]✗[/red]")
        bad += ok is False
        table.add_row(name, mark, detail)
    console.print(table)
    if bad:
        raise typer.Exit(1)


@app.command()
def serve(port: int = typer.Option(8765, "--port"), token: Optional[str] = typer.Option(None, "--token"),
          demo: bool = typer.Option(False, "--demo", help="演示模式（离线脚本模型）")) -> None:
    """启动后端服务（桌面 App 会自动启动它；这里用于浏览器开发与调试）。"""
    from .server.main import main as server_main

    args = ["--port", str(port)] + (["--token", token] if token else []) + (["--demo"] if demo else [])
    server_main(args)


if __name__ == "__main__":  # pragma: no cover
    app()
