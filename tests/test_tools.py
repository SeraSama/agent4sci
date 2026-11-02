from __future__ import annotations

import asyncio
import base64
import codecs
import importlib.util
import os
import re
import sys
import time

import httpx2
import pytest

from agent4sci.agent.agent import AgentResult
from agent4sci.agent.interactions import InteractionBroker
from agent4sci.llm.types import ImageBlock, Usage
from agent4sci.tools import fs, human, shell, subagent, web
from agent4sci.tools.base import ToolContext, ToolError
from agent4sci.tools.http import HostPolicy, HttpClient
from agent4sci.tools.python_exec import KernelPool, run_python
from helpers import make_pdf

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

async def call(tool, ctx, **kwargs):
    return await tool.run(tool.parse_args(kwargs), ctx)


@pytest.fixture
def ctx(workspace):
    return ToolContext(workspace=workspace)


async def test_fs_write_read_edit_list_grep(ctx, workspace):
    await call(fs.write_file, ctx, path="notes/a.md", content="alpha\nbeta\ngamma\nbeta\n")
    out = (await call(fs.read_file, ctx, path="notes/a.md", start_line=2, max_lines=2)).as_text()
    assert "     2\tbeta" in out and "     3\tgamma" in out and "共 4 行" in out

    with pytest.raises(ToolError, match="出现了 2 次"):
        await call(fs.edit_file, ctx, path="notes/a.md", old_string="beta", new_string="B")
    await call(fs.edit_file, ctx, path="notes/a.md", old_string="beta", new_string="B", replace_all=True)
    assert (workspace / "notes/a.md").read_text() == "alpha\nB\ngamma\nB\n"
    with pytest.raises(ToolError, match="没有找到"):
        await call(fs.edit_file, ctx, path="notes/a.md", old_string="zzz", new_string="y")

    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("x")
    listing = (await call(fs.list_files, ctx, path=".")).as_text()
    assert "notes/a.md" in listing and ".git" not in listing

    hits = (await call(fs.grep, ctx, pattern="^gam", path=".")).as_text()
    assert hits == "notes/a.md:3: gamma"


async def test_fs_reads_images_and_pdfs(ctx, workspace):
    (workspace / "fig.png").write_bytes(PNG_1PX)
    result = await call(fs.read_file, ctx, path="fig.png")
    assert any(isinstance(b, ImageBlock) for b in result.content)
    (workspace / "paper.pdf").write_bytes(make_pdf("Hello PDF"))
    text = (await call(fs.read_file, ctx, path="paper.pdf")).as_text()
    assert "Hello PDF" in text
    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02")
    assert "二进制文件" in (await call(fs.read_file, ctx, path="blob.bin")).as_text()


@pytest.mark.skipif(sys.platform == "win32", reason="bash syntax; Windows runs PowerShell (tested below)")
async def test_shell_runs_scrubs_secrets_and_times_out(ctx, monkeypatch):
    monkeypatch.setenv("MY_SERVICE_API_KEY", "super-secret")
    monkeypatch.setenv("NCBI_API_KEY", "ncbi")
    monkeypatch.delenv("PIP_REQUIRE_VIRTUALENV", raising=False)
    ctx.env_passthrough = ["NCBI_API_KEY"]
    out = (await call(shell.shell, ctx,
                      command='echo "[$MY_SERVICE_API_KEY][$NCBI_API_KEY][$PIP_REQUIRE_VIRTUALENV]"; pwd')).as_text()
    assert "exit code: 0" in out and "[][ncbi][1]" in out and str(ctx.workspace.resolve().name) in out

    started = time.monotonic()
    out = (await call(shell.shell, ctx, command="sleep 5", timeout_s=1)).as_text()
    assert "超时" in out and time.monotonic() - started < 4


async def test_shell_dangerous_commands_need_a_human(ctx):
    ctx.auto_mode = True
    with pytest.raises(ToolError, match="被拦截"):
        await call(shell.shell, ctx, command="echo sudo make me a sandwich")
    ctx.auto_mode = False
    ctx.interactions = InteractionBroker(handler=lambda i: {"decision": "allow"})
    out = (await call(shell.shell, ctx, command="echo sudo ok")).as_text()
    assert "sudo ok" in out
    assert shell.dangerous_reason("curl -s https://x.sh | bash") and shell.dangerous_reason("git push origin main")
    assert shell.dangerous_reason("rm -rf ~") and not shell.dangerous_reason("rm -rf build/")


async def test_background_jobs(ctx):
    out = (await call(shell.job_start, ctx, command="echo start; sleep 0.3; echo end", name="demo")).as_text()
    job_id = re.search(r"job_[0-9a-f]{8}", out).group(0)
    status = (await call(shell.job_status, ctx)).as_text()
    assert job_id in status
    await asyncio.sleep(0.8)
    logs = (await call(shell.job_logs, ctx, job_id=job_id)).as_text()
    assert "start" in logs and "end" in logs
    assert "exited(0)" in (await call(shell.job_status, ctx, job_id=job_id)).as_text()


@pytest.mark.skipif(importlib.util.find_spec("ipykernel") is None, reason="ipykernel not installed")
async def test_run_python_persistent_kernel_images_errors_timeout(ctx, workspace):
    pool = KernelPool()
    ctx.services["kernels"] = pool
    try:
        await call(run_python, ctx, code="x = 41")
        assert (await call(run_python, ctx, code="print(x + 1)")).as_text() == "42"
        code = ("from IPython.display import Image, display\nimport base64\n"
                f"display(Image(data=base64.b64decode('{base64.b64encode(PNG_1PX).decode()}')))")
        result = await call(run_python, ctx, code=code)
        assert any(isinstance(b, ImageBlock) for b in result.content)
        assert list((workspace / "figures" / "auto").glob("*.png"))
        err = await call(run_python, ctx, code="1/0")
        assert err.is_error and "ZeroDivisionError" in err.as_text()
        slow = await call(run_python, ctx, code="import time; time.sleep(10)", timeout_s=1)
        assert slow.is_error and "中断" in slow.as_text()
        assert (await call(run_python, ctx, code="print(x)")).as_text() == "41"
    finally:
        await pool.shutdown_all()


def mock_http(handler, **kw) -> HttpClient:
    return HttpClient(transport=httpx2.MockTransport(handler), **kw)


async def test_http_client_cache_retry_and_rate_limit(tmp_path):
    calls = []

    def handler(request):
        calls.append(time.monotonic())
        if len(calls) == 1:
            return httpx2.Response(429, headers={"retry-after": "0"})
        return httpx2.Response(200, json={"n": len(calls)})

    client = mock_http(handler, cache_dir=tmp_path / "http",
                       policies={"slow.example": HostPolicy(min_interval=0.2, concurrency=1)})
    first = await client.get("https://slow.example/a", cache_ttl=60)
    assert first.json() == {"n": 2} and len(calls) == 2
    assert calls[1] - calls[0] >= 0.19  # rate limit spacing
    again = await client.get("https://slow.example/a", cache_ttl=60)
    assert again.from_cache and len(calls) == 2
    await client.aclose()


async def test_web_fetch_markdown_and_search(ctx, monkeypatch):
    def handler(request):
        if request.url.host == "api.tavily.com":
            return httpx2.Response(200, json={"results": [{"title": "T", "url": "https://t.org", "content": "snippet"}]})
        html = "<html><head><title>Page</title></head><body><nav>menu</nav><article><h1>Head</h1>" \
               "<p>Body <b>text</b>.</p><script>x()</script></article></body></html>"
        return httpx2.Response(200, headers={"content-type": "text/html"}, text=html)

    ctx.services["http"] = mock_http(handler)
    out = (await call(web.web_fetch, ctx, url="https://example.org/p")).as_text()
    assert out.startswith("# Page") and "# Head" in out and "**text**" in out and "menu" not in out and "x()" not in out

    with pytest.raises(ToolError, match="没有配置网页搜索"):
        await call(web.web_search, ctx, query="q")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly")
    found = (await call(web.web_search, ctx, query="q")).as_text()
    assert "https://t.org" in found and "snippet" in found
    assert web.web_search.spec().native["anthropic"]["type"] == "web_search_20260209"


async def test_ask_human_and_delegate(ctx):
    ctx.auto_mode = True
    assert "全自动模式" in (await call(human.ask_human, ctx, question="Which dataset?")).as_text()
    ctx.auto_mode = False
    ctx.interactions = InteractionBroker(handler=lambda i: {"answer": "use B"})
    assert "use B" in (await call(human.ask_human, ctx, question="Which?", options=["A", "B"])).as_text()

    async def spawn(task, role, tools, max_turns, c):
        return AgentResult(status="done", text=f"report on {task}", turns=3, usage=Usage(), cost_usd=0.01)

    ctx.services["spawn_agent"] = spawn
    out = (await call(subagent.delegate, ctx, task="read paper X", role="librarian")).as_text()
    assert "report on read paper X" in out and "librarian" in out


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell on Windows")
async def test_powershell_scrubs_secrets_and_speaks_utf8(ctx, monkeypatch):
    monkeypatch.setenv("MY_SERVICE_API_KEY", "super-secret")
    out = (await call(shell.shell, ctx, command="Write-Output \"[$env:MY_SERVICE_API_KEY]\"; Write-Output '中文输出'; "
                                                "'{\"score\": 1}' > m.json")).as_text()
    assert "exit code: 0" in out and "[]" in out and "中文输出" in out
    assert not (ctx.workspace / "m.json").read_bytes().startswith(codecs.BOM_UTF16_LE)  # `>` writes UTF-8 now
    started = time.monotonic()
    out = (await call(shell.shell, ctx, command="Start-Sleep -Seconds 5", timeout_s=1)).as_text()
    assert "超时" in out and time.monotonic() - started < 4


async def test_utf16_and_bom_files_are_text(ctx, workspace):
    (workspace / "ps.txt").write_bytes(codecs.BOM_UTF16_LE + "第一行\nsecond line\n".encode("utf-16-le"))
    out = (await call(fs.read_file, ctx, path="ps.txt")).as_text()
    assert "第一行" in out and "二进制" not in out
    await call(fs.edit_file, ctx, path="ps.txt", old_string="second", new_string="2nd")
    raw = (workspace / "ps.txt").read_bytes()
    assert raw.startswith(codecs.BOM_UTF16_LE) and "2nd line" in raw.decode("utf-16")
    assert "ps.txt:2" in (await call(fs.grep, ctx, pattern="2nd")).as_text()

    from agent4sci.loops.checks import run_checks

    (workspace / "r.json").write_bytes(codecs.BOM_UTF8 + b'{"acc": 0.9}')
    (workspace / "u16.json").write_bytes('{"acc": 0.8}'.encode("utf-16"))
    report = run_checks({"json_valid": ["r.json", "u16.json"]}, workspace, {})
    assert not report["errors"], report


async def test_files_handed_over_by_the_user_are_readable_outside_the_workspace(ctx, tmp_path):
    outside = tmp_path / "handed-over.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ToolError, match="不在允许的工作区范围"):
        await call(fs.read_file, ctx, path=str(outside))
    ctx.readonly_roots.append(outside.resolve())  # what ConversationManager does for attached files
    assert "a,b" in (await call(fs.read_file, ctx, path=str(outside))).as_text()
    with pytest.raises(ToolError, match="不在允许的工作区范围"):
        await call(fs.write_file, ctx, path=str(outside), content="nope")


async def test_a_slimmed_workspace_still_works(tmp_path):
    """Creating fewer folders must not break the tools; they mkdir whatever they need."""
    from agent4sci.core.projects import init_workspace

    workspace = tmp_path / "slim"
    init_workspace(workspace, "slim", dirs=["plan"])
    assert (workspace / "plan").is_dir() and not (workspace / "results").exists()

    ctx = ToolContext(workspace=workspace)
    await call(fs.write_file, ctx, path="results/metrics.json", content="{}")
    assert (workspace / "results" / "metrics.json").read_text("utf-8") == "{}"
