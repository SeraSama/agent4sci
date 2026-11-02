from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx2
import pytest

from agent4sci.agent.agent import Agent
from agent4sci.llm.fake_provider import FakeProvider, FakeTurn
from agent4sci.mcp.manager import MCPManager, MCPServerConfig, check_server, expand_refs, missing_refs, sanitize_name
from agent4sci.mcp.registry_api import config_from_registry, parse_registry, search_registry
from agent4sci.tools.base import ToolContext, ToolError
from agent4sci.tools.http import HttpClient
from agent4sci.tools.registry import ToolRegistry

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")


def echo_config(**kw) -> MCPServerConfig:
    return MCPServerConfig(name="echo", command=sys.executable, args=[SERVER], **kw)


async def test_stdio_server_tools_through_agent(workspace):
    manager = MCPManager()
    try:
        await manager.start([echo_config(), MCPServerConfig(name="broken", command="definitely-not-a-command-xyz")])
        status = {s["name"]: s for s in manager.status()}
        assert status["echo"]["ok"] and set(status["echo"]["tools"]) == {"add", "shout", "fail"}
        assert not status["broken"]["ok"] and status["broken"]["error"]
        tools = manager.tools()
        names = {t.name for t in tools}
        assert names == {"mcp__echo__add", "mcp__echo__shout", "mcp__echo__fail"}
        add = next(t for t in tools if t.name == "mcp__echo__add")
        assert add.input_schema()["properties"]["a"]["type"] == "integer"

        provider = FakeProvider(script=[
            FakeTurn(tool_calls=[("mcp__echo__add", {"a": 2, "b": 40}), ("mcp__echo__fail", {})]),
            FakeTurn(text="done"),
        ])
        # tool calls happen from a different task than the one that opened the connection
        agent = Agent(provider=provider, tools=ToolRegistry(tools), ctx=ToolContext(workspace=workspace),
                      system_prompt="S")
        await asyncio.create_task(agent.run("use mcp"))
        ok, failed = agent.messages[2].tool_results()
        assert ok.text() == "42" and not ok.is_error
        assert failed.is_error and "boom" in failed.text()
    finally:
        await manager.aclose()
    with pytest.raises(ToolError):
        await add.run({"a": 1, "b": 2}, ToolContext(workspace=workspace))


async def test_allowlist_and_test_server():
    report = await check_server(echo_config(tools=["shout"]))
    assert report["ok"] and report["tools"] == ["shout"]


def test_env_refs_and_names(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "t0k")
    assert expand_refs("Bearer ${MY_TOKEN}") == "Bearer t0k"
    cfg = MCPServerConfig(name="x", url="https://x", headers={"Authorization": "Bearer ${MY_TOKEN}"},
                          env={"K": "${MISSING_KEY}"})
    assert missing_refs(cfg) == ["MISSING_KEY"]
    assert sanitize_name("my.server", "do thing") == "mcp__my_server__do_thing"
    assert len(sanitize_name("s" * 50, "t" * 50)) == 64


REGISTRY_RESPONSE = {"servers": [
    {"server": {"name": "io.github.blazickjp/arxiv-mcp-server", "description": "arXiv search", "version": "0.3.1",
                "repository": {"url": "https://github.com/blazickjp/arxiv-mcp-server"},
                "packages": [{"registryType": "pypi", "identifier": "arxiv-mcp-server", "version": "0.3.1",
                              "transport": {"type": "stdio"},
                              "environmentVariables": [{"name": "ARXIV_STORAGE_PATH", "isRequired": False},
                                                       {"name": "ARXIV_TOKEN", "isRequired": True, "isSecret": True}]}]},
     "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "isLatest": True}}},
    {"server": {"name": "com.example/remote", "description": "remote only", "version": "1.0.0",
                "remotes": [{"type": "streamable-http", "url": "https://mcp.example.com/mcp",
                             "headers": [{"name": "X-API-Key", "isSecret": True}]}]}},
    {"server": {"name": "com.example/npm-tool", "version": "2.0.0",
                "packages": [{"registryType": "npm", "identifier": "@ex/tool", "version": "2.0.0",
                              "transport": {"type": "stdio"}}]},
     "_meta": {"io.modelcontextprotocol.registry/official": {"status": "deprecated"}}},
]}


async def test_registry_search_and_configs():
    def handler(request):
        assert request.url.params["search"] == "arxiv"
        return httpx2.Response(200, json=REGISTRY_RESPONSE)

    servers = await search_registry(HttpClient(transport=httpx2.MockTransport(handler)), "arxiv")
    assert [s.name for s in servers] == ["io.github.blazickjp/arxiv-mcp-server", "com.example/remote"]
    cfg = config_from_registry(servers[0])
    assert (cfg.command, cfg.args, cfg.name) == ("uvx", ["arxiv-mcp-server==0.3.1"], "arxiv-mcp-server")
    assert cfg.env == {"ARXIV_TOKEN": "${ARXIV_TOKEN}"}  # optional variables are left to the server's defaults
    assert [v.name for v in servers[0].env_vars()] == ["ARXIV_STORAGE_PATH", "ARXIV_TOKEN"]
    remote = config_from_registry(servers[1])
    assert remote.url == "https://mcp.example.com/mcp" and remote.headers == {"X-API-Key": "${X_API_KEY}"}
    npm = config_from_registry(parse_registry(REGISTRY_RESPONSE)[2])
    assert npm.command == "npx" and npm.args == ["-y", "@ex/tool@2.0.0"]


async def test_a_slow_install_is_waited_out_instead_of_killed(tmp_path, monkeypatch):
    """A server that keeps writing to its log is still installing: extend the wait rather than restart it."""
    from agent4sci.mcp import manager as mcp_manager

    monkeypatch.setattr(mcp_manager, "PROGRESS_SLICE_S", 0.02)
    handle = mcp_manager.ServerHandle.__new__(mcp_manager.ServerHandle)
    handle.config = MCPServerConfig(name="slow", command="uvx", args=["x"])
    handle.error = None
    handle._ready = asyncio.Event()
    handle._stop = asyncio.Event()
    handle._errlog = None
    handle.log_path = tmp_path / "slow.log"
    handle.log_path.write_text("\n--- start slow\n", encoding="utf-8")

    async def fake_run() -> None:
        # Four bursts of download output, then ready — each burst must buy another window.
        for i in range(4):
            await asyncio.sleep(0.03)
            with handle.log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"Downloading pkg-{i} (10MiB)\n")
        handle._ready.set()

    handle._task = None
    monkeypatch.setattr(mcp_manager.asyncio, "create_task", lambda coro, name=None: asyncio.get_event_loop().create_task(coro))
    handle._run = fake_run  # type: ignore[method-assign]

    await handle.start(timeout=0.05)  # shorter than the install, but progress keeps extending it
    assert handle.error is None and handle._ready.is_set()


async def test_a_stalled_install_gives_up_and_says_so(tmp_path, monkeypatch):
    from agent4sci.mcp import manager as mcp_manager

    monkeypatch.setattr(mcp_manager, "PROGRESS_SLICE_S", 0.02)
    monkeypatch.setattr(mcp_manager, "INSTALL_CEILING_S", 0.3)  # the real ceiling is 15 minutes
    handle = mcp_manager.ServerHandle.__new__(mcp_manager.ServerHandle)
    handle.config = MCPServerConfig(name="stalled", command="uvx", args=["x"])
    handle.error = None
    handle._ready = asyncio.Event()
    handle._stop = asyncio.Event()
    handle._errlog = None
    handle._task = None
    handle.log_path = tmp_path / "stalled.log"
    handle.log_path.write_text("\n--- start stalled\nDownloading pyarrow (34.7MiB)\n", encoding="utf-8")

    async def never() -> None:
        await handle._stop.wait()  # mirrors the real task: it only ends when stop() asks it to

    monkeypatch.setattr(mcp_manager.asyncio, "create_task", lambda coro, name=None: asyncio.get_event_loop().create_task(coro))
    handle._run = never  # type: ignore[method-assign]
    await handle.start(timeout=0.05)
    # A big wheel downloads silently, so a log that says "Downloading" keeps the wait alive to the ceiling.
    assert "依赖还没装完" in handle.error and "重新连接" in handle.error
