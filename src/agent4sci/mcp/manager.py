"""MCP client integration (``mcp`` SDK 2.x).

Each server connection lives in its own *owner task*: the SDK's ``Client`` uses
anyio task groups, which must be entered and exited in the same task, while
tool calls may come from any agent task.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..core.envfix import find_executable, login_shell_path
from ..core.paths import logs_dir
from ..core.secrets import get_secret_store
from ..llm.types import ImageBlock, TextBlock
from ..tools.base import Tool, ToolContext, ToolError, ToolResult

CONNECT_TIMEOUT_S = 180.0  # the first start of a uvx/npx server downloads its packages, which is slow on a proxy
# …and some servers pull 100 MB of wheels, which no fixed timeout covers. So the wait is extended for as long as
# the server keeps writing to its log — killing a download half-way only makes the next attempt start over.
PROGRESS_SLICE_S = 15.0
INSTALL_CEILING_S = 900.0
INSTALLING = re.compile(r"Downloading|Installing|Resolving|Preparing|Building|Prepared|Downloaded")

# A server starts in a clean environment (no API keys leak into third-party code), but without these it cannot
# reach the network at all: `uvx`/`npx` download packages, and the servers themselves call APIs.
INHERITED_ENV = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
    "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "XDG_CACHE_HOME", "npm_config_cache",
    "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
)
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MCPServerConfig(BaseModel):
    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    tools: list[str] | None = None  # allowlist of remote tool names
    description: str = ""
    origin: str = ""  # catalog / registry / manual


def expand_refs(value: str) -> str:
    """Replace ${VAR} with an env var or a stored secret ``env:VAR``."""
    store = get_secret_store()

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        return os.environ.get(name) or store.get(f"env:{name}") or ""

    return _ENV_REF.sub(sub, value)


def missing_refs(config: MCPServerConfig) -> list[str]:
    store = get_secret_store()
    refs = set()
    for value in [*config.env.values(), *config.headers.values(), *config.args]:
        refs.update(_ENV_REF.findall(value))
    return sorted(r for r in refs if not (os.environ.get(r) or store.get(f"env:{r}")))


def sanitize_name(server: str, tool: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{server}__{tool}")
    return name[:64]


def convert_result(result: Any) -> ToolResult:
    from mcp import types as t

    blocks: list[TextBlock | ImageBlock] = []
    for item in getattr(result, "content", None) or []:
        if isinstance(item, t.TextContent):
            blocks.append(TextBlock(text=item.text))
        elif isinstance(item, t.ImageContent):
            blocks.append(ImageBlock(media_type=item.mime_type or "image/png", data=item.data))
        elif isinstance(item, t.EmbeddedResource):
            res = item.resource
            text = getattr(res, "text", None)
            blocks.append(TextBlock(text=f"[资源 {getattr(res, 'uri', '')}]\n{text}" if text else
                                    f"[二进制资源 {getattr(res, 'uri', '')}]"))
        elif isinstance(item, t.ResourceLink):
            blocks.append(TextBlock(text=f"[资源链接] {item.name}: {item.uri}"))
        else:
            blocks.append(TextBlock(text=f"[{getattr(item, 'type', 'content')}]"))
    structured = getattr(result, "structured_content", None)
    if not blocks and structured is not None:
        import json

        blocks.append(TextBlock(text=json.dumps(structured, ensure_ascii=False, indent=2)))
    if not blocks:
        blocks.append(TextBlock(text="(无输出)"))
    return ToolResult(content=blocks, is_error=bool(getattr(result, "is_error", False)))


class MCPTool(Tool):
    category = "mcp"
    parallel_safe = False

    def __init__(self, server: "ServerHandle", remote: Any) -> None:
        self.server = server
        self.remote_name = remote.name
        self.name = sanitize_name(server.config.name, remote.name)
        desc = remote.description or remote.title or remote.name
        self.description = f"[MCP · {server.config.name}] {desc}"
        schema = dict(remote.input_schema or {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        self._schema = schema

    def input_schema(self) -> dict[str, Any]:
        return self._schema

    def parse_args(self, data: dict[str, Any]) -> dict[str, Any]:
        return data  # the server validates its own arguments

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ctx.emit("mcp.call", server=self.server.config.name, tool=self.remote_name)
        return await self.server.call(self.remote_name, args)


class ServerHandle:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.client: Any = None
        self.remote_tools: list[Any] = []
        self.error: str | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._errlog: Any = None
        self.log_path: Path | None = None

    def _target(self) -> Any:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        cfg = self.config
        if cfg.url:
            url = expand_refs(cfg.url)
            if cfg.headers:
                import httpx2
                from mcp.client.streamable_http import streamable_http_client

                headers = {k: expand_refs(v) for k, v in cfg.headers.items()}
                http_client = httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30.0, read=300.0))
                return streamable_http_client(url, http_client=http_client)
            return url
        if not cfg.command:
            raise ValueError("需要 command 或 url")
        command = find_executable(cfg.command) or cfg.command
        env = {"PATH": login_shell_path(), "HOME": os.environ.get("HOME", ""), "LANG": os.environ.get("LANG", "en_US.UTF-8")}
        for key in INHERITED_ENV:
            if key in os.environ:
                env[key] = os.environ[key]
        env.update({k: expand_refs(v) for k, v in cfg.env.items()})
        params = StdioServerParameters(command=command, args=[expand_refs(a) for a in cfg.args], env=env)
        # The server's stderr goes to its own log file: useful for diagnosing failed starts, and it works when
        # this process has no usable stderr (windowed app, test runners).
        log_path = logs_dir() / "mcp" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', cfg.name)}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = log_path
        self._errlog = open(log_path, "a", encoding="utf-8", errors="replace")  # noqa: SIM115 - closed in _run
        self._errlog.write(f"\n--- start {cfg.name}: {command} {' '.join(cfg.args)}\n")
        self._errlog.flush()
        return stdio_client(params, errlog=self._errlog)

    async def _run(self) -> None:
        from mcp import Client

        try:
            async with Client(self._target()) as client:
                self.client = client
                listed = await client.list_tools()
                tools = list(listed.tools)
                if self.config.tools is not None:
                    allowed = set(self.config.tools)
                    tools = [t for t in tools if t.name in allowed]
                self.remote_tools = tools
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:  # connection or protocol failure
            leaf: BaseException = exc
            while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:  # anyio task groups wrap the real error
                leaf = leaf.exceptions[0]
            self.error = f"{type(leaf).__name__}: {leaf}"[:500]
            hint = self._hint()
            tail = self._log_tail()
            if tail:
                self.error += f"\n服务器输出：{tail}"
            if hint:
                self.error += f"\n怎么办：{hint}"
        finally:
            self.client = None
            self._ready.set()
            if self._errlog is not None:
                self._errlog.close()
                self._errlog = None

    def _hint(self) -> str:
        """Turn the usual launch failures into one line the user can act on."""
        text = f"{self.error or ''}\n{self._log_tail(1200)}"
        command = self.config.command or ""
        missing = re.search(r"executable `([^`]+)` was not found", text) or re.search(r"Failed to spawn: `([^`]+)`", text)
        if missing:
            other = re.search(r"an executable with the name `([^`]+)`", text, re.I)
            fix = (f"试试把启动参数改成 --from {missing.group(1)} {other.group(1)} …"
                   if other else "包名和可执行文件名可能不一致，查一下这个包真正安装的命令名。")
            return f"{command} 里没有名为 {missing.group(1)} 的可执行文件。{fix}"
        if re.search(r"Failed to acquire lock|Timeout \(\d+s\) when waiting for lock", text):
            return "另一个安装进程占着 uv 的缓存锁（同时启动两个服务器，或上一次没退干净）。等它装完，或重启应用后再试。"
        if re.search(r"No solution found|Could not find a version|No matching distribution", text):
            return "依赖装不上：可能是包名写错了，或者它不支持当前的 Python 版本。"
        if re.search(r"Unsupported engine|EBADENGINE", text):
            return "这个服务器要求更新的 Node 版本，升级 Node 后再试。"
        if re.search(r"(?:command not found|ENOENT|No such file or directory).*" + re.escape(command), text) or \
                (command and f"No such file or directory: '{command}'" in text):
            return f"系统里找不到 {command}。uvx 来自 uv，npx 来自 Node.js —— 先装上对应的运行时。"
        if re.search(r"(?:SSLError|CERTIFICATE_VERIFY_FAILED|ProxyError|Temporary failure in name resolution)", text):
            return "网络或代理问题：首次启动需要联网下载依赖。"
        return ""

    def _log_tail(self, limit: int = 600) -> str:
        if self._errlog is not None:
            self._errlog.flush()
        if self.log_path is None or not self.log_path.exists():
            return ""
        text = self.log_path.read_text("utf-8", errors="replace")
        return text[text.rfind("\n--- start "):].split("\n", 2)[-1].strip()[-limit:]

    def _log_size(self) -> int:
        if self._errlog is not None:
            self._errlog.flush()
        try:
            return self.log_path.stat().st_size if self.log_path else 0
        except OSError:
            return 0

    async def start(self, timeout: float = CONNECT_TIMEOUT_S) -> None:
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.config.name}")
        started = time.monotonic()
        deadline = started + timeout
        ceiling = started + max(timeout, INSTALL_CEILING_S)
        progress = -1
        while True:
            remaining = min(deadline, ceiling) - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._ready.wait(), min(remaining, PROGRESS_SLICE_S))
                return
            except asyncio.TimeoutError:
                size = self._log_size()
                if size > progress:  # new output: still working, so give it another full window
                    progress = size
                    deadline = time.monotonic() + timeout
                elif INSTALLING.search(self._log_tail(200)):
                    # One big wheel can download for minutes without printing anything. Keep waiting until
                    # the ceiling rather than killing it — a half-finished download has to start over.
                    deadline = time.monotonic() + timeout
        waited = time.monotonic() - started
        tail = self._log_tail(300)
        if INSTALLING.search(tail):
            self.error = (f"等了 {waited:.0f} 秒，依赖还没装完就停住了。这类服务器首次启动要下载上百 MB，"
                          f"网络慢或走代理时很容易卡住。已下载的部分有缓存，点“重新连接”会接着来。")
        else:
            self.error = f"连接超时（{waited:.0f} 秒）。首次启动要下载依赖，网络慢或需要代理时会超时，可以再试一次。"
        if tail:
            self.error += f"\n服务器输出：{tail}"
        hint = self._hint()
        if hint:
            self.error += f"\n怎么办：{hint}"
        await self.stop()

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        if self.client is None:
            raise ToolError(f"MCP 服务器 {self.config.name} 未连接: {self.error or '已断开'}")
        try:
            result = await self.client.call_tool(name, args)
        except Exception as exc:
            raise ToolError(f"MCP 调用失败 {self.config.name}.{name}: {exc}") from exc
        return convert_result(result)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 10)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()

    @property
    def ok(self) -> bool:
        return self.client is not None and self.error is None


class MCPManager:
    def __init__(self) -> None:
        self.handles: dict[str, ServerHandle] = {}

    async def start(self, configs: list[MCPServerConfig]) -> None:
        wanted = [c for c in configs if c.enabled and c.name not in self.handles]
        handles = [ServerHandle(c) for c in wanted]
        await asyncio.gather(*(h.start() for h in handles))
        for h in handles:
            self.handles[h.config.name] = h

    def tools(self, servers: list[str] | None = None) -> list[MCPTool]:
        out = []
        for name, handle in self.handles.items():
            if servers is not None and name not in servers:
                continue
            if handle.ok:
                out.extend(MCPTool(handle, remote) for remote in handle.remote_tools)
        return out

    def status(self) -> list[dict[str, Any]]:
        return [{"name": n, "ok": h.ok, "error": h.error, "tools": [t.name for t in h.remote_tools]}
                for n, h in self.handles.items()]

    async def restart(self, config: MCPServerConfig) -> None:
        old = self.handles.pop(config.name, None)
        if old is not None:
            await old.stop()
        await self.start([config])

    async def aclose(self) -> None:
        await asyncio.gather(*(h.stop() for h in self.handles.values()), return_exceptions=True)
        self.handles.clear()


async def check_server(config: MCPServerConfig) -> dict[str, Any]:
    manager = MCPManager()
    try:
        await manager.start([config])
        return manager.status()[0]
    finally:
        await manager.aclose()
