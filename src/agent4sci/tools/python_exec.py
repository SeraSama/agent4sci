"""Persistent Python sessions backed by a Jupyter kernel running in the workspace venv.

Plots displayed by the code come back to the model as images (so it can check
its own figures) and are saved under ``figures/auto/``. Without ipykernel the
code runs as a one-off script instead.
"""

from __future__ import annotations

import asyncio
import base64
import queue
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..core.envfix import tool_env, venv_python
from ..llm.types import ImageBlock, TextBlock
from .base import ToolContext, ToolError, ToolResult, tool
from .shell import run_command

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def workspace_python(workspace: Path) -> str | None:
    venv = venv_python(workspace)
    if venv is not None:
        return str(venv)
    if not getattr(sys, "frozen", False):
        return sys.executable  # development: the backend's own interpreter
    return None


class KernelSession:
    def __init__(self, workspace: Path, python: str, env: dict[str, str]) -> None:
        self.workspace = workspace
        self.python = python
        self.env = env
        self.km: Any = None
        self.kc: Any = None
        self.lock = asyncio.Lock()
        self.figure_count = 0

    async def start(self) -> None:
        from jupyter_client.kernelspec import KernelSpec, KernelSpecManager
        from jupyter_client.manager import AsyncKernelManager

        python = self.python

        class _Specs(KernelSpecManager):
            def get_kernel_spec(self, kernel_name: str) -> KernelSpec:  # type: ignore[override]
                return KernelSpec(argv=[python, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                                  display_name="agent4sci", language="python", env={})

        self.km = AsyncKernelManager(kernel_name="agent4sci", kernel_spec_manager=_Specs())
        await self.km.start_kernel(cwd=str(self.workspace), env=self.env)
        self.kc = self.km.client()
        self.kc.start_channels()
        try:
            await self.kc.wait_for_ready(timeout=60)
        except RuntimeError:
            await self.shutdown()
            raise

    async def execute(self, code: str, timeout_s: float, figures_dir: Path) -> tuple[list[Any], bool]:
        async with self.lock:
            msg_id = self.kc.execute(code, store_history=True, allow_stdin=False)
            blocks: list[Any] = []
            stdout: list[str] = []
            error = False
            deadline = time.monotonic() + timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    await self.km.interrupt_kernel()
                    stdout.append(f"\n[已超过 {timeout_s:.0f} 秒，执行被中断]")
                    error = True
                    await self._drain(msg_id, 10)
                    break
                try:
                    msg = await self.kc.get_iopub_msg(timeout=min(remaining, 5))
                except (queue.Empty, asyncio.TimeoutError, TimeoutError):
                    if not await self.km.is_alive():
                        stdout.append("\n[内核已退出]")
                        error = True
                        break
                    continue
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                mtype, content = msg["msg_type"], msg["content"]
                if mtype == "stream":
                    stdout.append(content.get("text", ""))
                elif mtype in ("execute_result", "display_data"):
                    data = content.get("data", {})
                    if "image/png" in data:
                        self.figure_count += 1
                        figures_dir.mkdir(parents=True, exist_ok=True)
                        path = figures_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{self.figure_count}.png"
                        raw = data["image/png"]
                        path.write_bytes(base64.b64decode(raw))
                        blocks.append(ImageBlock(media_type="image/png", data=raw.replace("\n", ""), path=str(path)))
                        stdout.append(f"[图像已保存: {path.relative_to(self.workspace).as_posix()}]\n")
                    elif "text/plain" in data:
                        stdout.append(str(data["text/plain"]) + "\n")
                elif mtype == "error":
                    error = True
                    stdout.append(_ANSI.sub("", "\n".join(content.get("traceback", []))) + "\n")
                elif mtype == "status" and content.get("execution_state") == "idle":
                    break
            text = "".join(stdout).strip() or "(无输出)"
            return [TextBlock(text=text), *blocks], error

    async def _drain(self, msg_id: str, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                msg = await self.kc.get_iopub_msg(timeout=1)
            except (queue.Empty, asyncio.TimeoutError, TimeoutError):
                continue
            if (msg.get("parent_header", {}).get("msg_id") == msg_id and msg["msg_type"] == "status"
                    and msg["content"].get("execution_state") == "idle"):
                return

    async def shutdown(self) -> None:
        try:
            if self.kc is not None:
                self.kc.stop_channels()
            if self.km is not None and await self.km.is_alive():
                await self.km.shutdown_kernel(now=True)
        except Exception:
            pass


class KernelPool:
    def __init__(self) -> None:
        self.sessions: dict[str, KernelSession] = {}
        self.unavailable: set[str] = set()

    async def get(self, key: str, workspace: Path, env_passthrough: list[str]) -> KernelSession | None:
        if key in self.sessions:
            return self.sessions[key]
        if key in self.unavailable:
            return None
        python = workspace_python(workspace)
        if python is None:
            raise ToolError("工作区还没有 Python 环境。请在项目设置里创建（或运行 `uv venv --seed`，再 `uv pip install ipykernel`）。")
        session = KernelSession(workspace, python, tool_env(workspace, env_passthrough, headless_plots=False))
        try:
            await session.start()
        except Exception:
            self.unavailable.add(key)
            return None
        self.sessions[key] = session
        return session

    async def restart(self, key: str) -> None:
        session = self.sessions.pop(key, None)
        if session is not None:
            await session.shutdown()
        self.unavailable.discard(key)

    async def shutdown_all(self) -> None:
        for session in list(self.sessions.values()):
            await session.shutdown()
        self.sessions.clear()


def _pool(ctx: ToolContext) -> KernelPool:
    pool = ctx.services.get("kernels")
    if pool is None:
        pool = ctx.services["kernels"] = KernelPool()
    return pool


def _session_key(ctx: ToolContext) -> str:
    return ctx.conversation_id or ctx.run_id or "default"


class PythonArgs(BaseModel):
    code: str = Field(description="Python code to execute")
    timeout_s: float = Field(600, ge=1, le=7200)
    restart: bool = Field(False, description="Restart the session (clears all variables) before running")


@tool("run_python", category="exec.python", timeout_s=7300)
async def run_python(args: PythonArgs, ctx: ToolContext):
    """Run Python code in a persistent session (variables survive between calls) inside the workspace
    virtualenv. Figures shown with matplotlib (plt.show()) are returned to you as images and saved in
    figures/auto/. Save important results to files under results/ as well."""
    pool = _pool(ctx)
    key = _session_key(ctx)
    if args.restart:
        await pool.restart(key)
    session = await pool.get(key, ctx.workspace, ctx.env_passthrough)
    figures = ctx.workspace / "figures" / "auto"
    if session is not None:
        blocks, error = await session.execute(args.code, args.timeout_s, figures)
        return ToolResult(content=blocks, is_error=error)

    # Fallback: no ipykernel available — run as a script (no persistence).
    python = workspace_python(ctx.workspace)
    cell_dir = ctx.workspace / ".a4s" / "tmp"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cell = cell_dir / f"cell_{uuid.uuid4().hex[:8]}.py"
    cell.write_text(args.code, encoding="utf-8")
    result = await run_command([python or "python", str(cell)], cwd=ctx.workspace,
                               env=tool_env(ctx.workspace, ctx.env_passthrough), timeout_s=args.timeout_s)
    note = "（未找到 ipykernel：代码作为独立脚本运行，变量不会保留。可用 `uv pip install ipykernel` 启用持久会话。）\n"
    return ToolResult(content=[TextBlock(text=note + result.render())], is_error=result.exit_code != 0)


PYTHON_TOOLS = [run_python]
