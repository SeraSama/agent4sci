"""Shell commands (bash on macOS/Linux, PowerShell on Windows) and background jobs."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from ..agent.permissions import guards_shell
from ..core.envfix import IS_WINDOWS, shell_command, tool_env
from .base import ToolContext, ToolError, tool

DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\bsudo\b", "使用 sudo 提权"),
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|~|\$HOME|\*)(\s|$)", "递归删除根目录/家目录"),
    (r"\bmkfs(\.\w+)?\b|\bdd\s+if=", "格式化/直接写磁盘"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork 炸弹"),
    (r"\b(shutdown|reboot|halt)\b", "关机/重启"),
    (r"(curl|wget|iwr|Invoke-WebRequest)[^|;&]*\|\s*(sudo\s+)?(ba|z|fi)?sh\b", "下载后直接执行脚本"),
    (r"\|\s*iex\b|Invoke-Expression", "下载后直接执行（PowerShell）"),
    (r"\bgit\s+push\b", "推送到远程仓库"),
    (r"\bchmod\s+-R\s+777\s+/", "放开根目录权限"),
    (r"~/\.ssh|\.aws/credentials|id_rsa|\.netrc", "访问密钥/凭据文件"),
    (r"\b(launchctl|crontab)\b|schtasks", "创建系统级持久任务"),
    (r"Remove-Item\s+.*-Recurse.*\s+[A-Za-z]:\\\s*$|\bformat\s+[a-zA-Z]:", "删除/格式化磁盘（Windows）"),
]


def dangerous_reason(command: str) -> str | None:
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return reason
    return None


# Paths a normal command touches all the time: interpreters, package caches, temp dirs. Reaching anywhere
# else outside the project (Documents, Desktop, another repo) is what a researcher wants to be asked about.
_SYSTEM_PREFIXES = ("/usr", "/bin", "/sbin", "/opt", "/Library", "/System", "/Applications", "/private/tmp",
                    "/private/var", "/tmp", "/var", "/dev/null", "/proc", "/etc/ssl")
_CACHE_DIRS = (".cache", ".local/share/uv", ".local/bin", ".npm", ".cargo", ".rustup", ".pyenv", ".nvm")
_PATH_LIKE = re.compile(r"(?<![\w$=.-])(~[\w./-]*|/[\w][\w./-]{2,}|[A-Za-z]:\\[\w.\\-]+)")
_RELATIVE_UP = re.compile(r"(?<![\w$=./-])(\.\.[\w./-]*)")  # only when ".." starts the path


def outside_workspace(command: str, workspace: Path) -> str | None:
    """A path in the command that points outside the project (and is not a system or cache location)."""
    try:
        root = workspace.resolve()
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return None
    for raw in _RELATIVE_UP.findall(command):  # ../somewhere-else can leave the project too
        try:
            target = (workspace / raw).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if target != root and root not in target.parents:
            return raw
    for raw in _PATH_LIKE.findall(command):
        if raw.startswith(_SYSTEM_PREFIXES):
            continue
        try:
            target = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if target == root or root in target.parents:
            continue
        if any(str(target).startswith(str(home / cache)) for cache in _CACHE_DIRS):
            continue
        return raw
    return None


@dataclass
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float

    def render(self) -> str:
        parts = [f"exit code: {self.exit_code}" + ("（超时被终止）" if self.timed_out else "")]
        if self.stdout.strip():
            parts.append(f"stdout:\n{self.stdout.rstrip()}")
        if self.stderr.strip():
            parts.append(f"stderr:\n{self.stderr.rstrip()}")
        return "\n".join(parts)


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _spawn_kwargs() -> dict:
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    return {"start_new_session": True}


async def run_command(
    command: str | list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_s: float = 600.0,
    stdin_text: str | None = None,
) -> CommandResult:
    argv = shell_command(command)[0] if isinstance(command, str) else command
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=env,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **_spawn_kwargs(),
    )
    timed_out = False
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin_text.encode() if stdin_text is not None else None), timeout=timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc)
        out, err = await proc.communicate()
    except asyncio.CancelledError:
        _kill_tree(proc)
        raise
    return CommandResult(proc.returncode, _text(out), _text(err), timed_out, time.monotonic() - started)


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace").lstrip("\ufeff")


async def _guard(command: str, ctx: ToolContext) -> None:
    """Ask a human before risky commands; refuse them when nobody is watching."""
    reason = None
    if guards_shell(ctx.permission_mode):
        reason = dangerous_reason(command)
        if reason is None and ctx.permission_mode != "ask_all":
            # in ask_all the whole exec category already asks, so this check would only duplicate the prompt
            outside = outside_workspace(command, ctx.workspace)
            if outside is not None:
                reason = f"访问项目之外的路径 {outside}"
    if reason is not None:
        if ctx.auto_mode or ctx.interactions is None:
            raise ToolError(f"命令被拦截（{reason}）。全自动模式下不执行此类命令，请换一种安全的做法。"
                            "确实需要时，把权限模式设为「全部允许」。")
        answer = await ctx.interactions.ask(
            "approval", f"允许执行有风险的命令？（{reason}）",
            {"tool": "shell", "category": "exec.shell", "input": {"command": command}, "reason": reason},
            topics=ctx.topics,
        )
        if answer.get("decision") != "allow":
            raise ToolError(f"用户拒绝执行该命令。{answer.get('note') or ''}".strip())
    gate = ctx.services.get("skill_gate")
    if gate is not None:
        denied = await gate(command, ctx)
        if denied:
            raise ToolError(f"命令被拦截：{denied}")


class ShellArgs(BaseModel):
    command: str = Field(description="Command line to run")
    timeout_s: float = Field(600, ge=1, le=7200, description="Kill the command after this many seconds")
    cwd: str = Field(".", description="Working directory (inside the workspace)")


@tool("shell", category="exec.shell", timeout_s=7300)
async def shell(args: ShellArgs, ctx: ToolContext):
    """Run a shell command in the workspace (bash on macOS/Linux, PowerShell on Windows). The workspace
    virtualenv is on PATH, secrets are removed from the environment, and plots use a headless backend.
    For long experiments use job_start instead."""
    await _guard(args.command, ctx)
    cwd = ctx.resolve(args.cwd)
    env = tool_env(ctx.workspace, ctx.env_passthrough)
    result = await run_command(args.command, cwd=cwd, env=env, timeout_s=args.timeout_s)
    return result.render()


# ---- background jobs ---------------------------------------------------------------


@dataclass
class Job:
    id: str
    command: str
    log_path: Path
    proc: asyncio.subprocess.Process
    started: float = field(default_factory=time.time)
    name: str = ""
    waiter: asyncio.Task | None = None

    @property
    def status(self) -> str:
        if self.proc.returncode is None:
            return "running"
        return f"exited({self.proc.returncode})"


class JobManager:
    """Long-running commands whose output goes to ``.a4s/jobs/<id>.log``."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.jobs: dict[str, Job] = {}

    async def start(self, command: str, env: dict[str, str], cwd: Path, name: str = "") -> Job:
        job_id = "job_" + uuid.uuid4().hex[:8]
        log_dir = self.workspace / ".a4s" / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}.log"
        log = open(log_path, "wb")  # noqa: SIM115 - closed when the process exits
        argv = shell_command(command)[0]
        proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=log, stderr=subprocess.STDOUT, **_spawn_kwargs())
        job = Job(job_id, command, log_path, proc, name=name)

        async def wait() -> None:
            await proc.wait()
            log.close()

        job.waiter = asyncio.create_task(wait())
        self.jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise ToolError(f"没有这个任务: {job_id}")
        return self.jobs[job_id]

    def tail(self, job_id: str, lines: int = 100) -> str:
        job = self.get(job_id)
        text = job.log_path.read_text("utf-8", errors="replace") if job.log_path.exists() else ""
        return "\n".join(text.splitlines()[-lines:])

    def kill(self, job_id: str) -> None:
        _kill_tree(self.get(job_id).proc)

    def kill_all(self) -> None:
        for job in self.jobs.values():
            _kill_tree(job.proc)


def _jobs(ctx: ToolContext) -> JobManager:
    manager = ctx.services.get("jobs")
    if manager is None:
        manager = ctx.services["jobs"] = JobManager(ctx.workspace)
    return manager


class JobStartArgs(BaseModel):
    command: str
    name: str = Field("", description="Short label for the job")
    cwd: str = "."


@tool("job_start", category="exec.shell")
async def job_start(args: JobStartArgs, ctx: ToolContext):
    """Start a long-running command in the background (e.g. a training run). Returns a job id; use
    job_status / job_logs to follow it and job_kill to stop it."""
    await _guard(args.command, ctx)
    job = await _jobs(ctx).start(args.command, tool_env(ctx.workspace, ctx.env_passthrough),
                                 ctx.resolve(args.cwd), args.name)
    ctx.emit("job.started", job_id=job.id, command=args.command, name=args.name)
    return f"已启动后台任务 {job.id}，日志: {ctx.display_path(job.log_path)}"


class JobIdArgs(BaseModel):
    job_id: str | None = Field(None, description="Job id; omit to list all jobs")
    lines: int = Field(80, ge=1, le=2000)


@tool("job_status", category="exec.read", parallel_safe=True)
async def job_status(args: JobIdArgs, ctx: ToolContext):
    """Show the status of background jobs (and the last log lines of one job)."""
    manager = _jobs(ctx)
    if not args.job_id:
        if not manager.jobs:
            return "没有后台任务。"
        return "\n".join(f"{j.id}  {j.status}  {j.name or j.command[:60]}  已运行 {time.time() - j.started:.0f}s"
                         for j in manager.jobs.values())
    job = manager.get(args.job_id)
    return f"{job.id}: {job.status}\n--- 最后 {args.lines} 行日志 ---\n{manager.tail(job.id, args.lines)}"


@tool("job_logs", category="exec.read", parallel_safe=True)
async def job_logs(args: JobIdArgs, ctx: ToolContext):
    """Return the last lines of a background job's log."""
    if not args.job_id:
        raise ToolError("需要 job_id")
    return _jobs(ctx).tail(args.job_id, args.lines)


class JobKillArgs(BaseModel):
    job_id: str


@tool("job_kill", category="exec.shell")
async def job_kill(args: JobKillArgs, ctx: ToolContext):
    """Stop a background job and its child processes."""
    _jobs(ctx).kill(args.job_id)
    ctx.emit("job.killed", job_id=args.job_id)
    return f"已终止 {args.job_id}"


SHELL_TOOLS = [shell, job_start, job_status, job_logs, job_kill]
