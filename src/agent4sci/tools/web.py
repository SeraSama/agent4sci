"""Web tools: fetch a page as Markdown, and web search through a pluggable backend.

On Anthropic models with native web tools enabled, the server-side
``web_search`` / ``web_fetch`` tools replace these declarations.
"""

from __future__ import annotations

import io
import os
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..core.secrets import get_secret_store
from .base import ToolContext, ToolError, tool
from .http import HttpClient, HttpError

WEB_SEARCH_NATIVE = {"anthropic": {"type": "web_search_20260209", "name": "web_search"}}
WEB_FETCH_NATIVE = {"anthropic": {"type": "web_fetch_20260209", "name": "web_fetch"}}


def _http(ctx: ToolContext) -> HttpClient:
    client = ctx.services.get("http")
    if client is None:
        client = ctx.services["http"] = HttpClient()
    return client


def html_to_markdown(html: str, base_url: str = "") -> tuple[str, str]:
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header", "aside"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = markdownify(str(main), heading_style="ATX", strip=["img"])
    lines = [line.rstrip() for line in text.splitlines()]
    compact: list[str] = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    return title, "\n".join(compact).strip()


class FetchArgs(BaseModel):
    url: str = Field(description="http(s) URL to fetch")
    max_chars: int = Field(20000, ge=500, le=200000)


@tool("web_fetch", category="web", parallel_safe=True, native=WEB_FETCH_NATIVE)
async def web_fetch(args: FetchArgs, ctx: ToolContext):
    """Fetch a web page (or PDF) and return its main content as Markdown."""
    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError("只支持 http/https 链接")
    try:
        resp = await _http(ctx).get(args.url, cache_ttl=3600, max_bytes=15_000_000)
    except HttpError as exc:
        raise ToolError(str(exc)) from exc
    ctype = resp.headers.get("content-type", "")
    if "pdf" in ctype or args.url.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(resp.content))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:40])
        title = args.url
    elif "html" in ctype or resp.text.lstrip().startswith("<"):
        title, text = html_to_markdown(resp.text, resp.url)
    else:
        title, text = args.url, resp.text
    if len(text) > args.max_chars:
        text = text[: args.max_chars] + f"\n\n…（已截断，原文 {len(text)} 个字符）"
    return f"# {title}\nURL: {resp.url}\n\n{text}"


def search_backend() -> tuple[str, str] | None:
    store = get_secret_store()
    for name, env in (("tavily", "TAVILY_API_KEY"), ("brave", "BRAVE_API_KEY")):
        key = store.get(f"service:{name}") or os.environ.get(env)
        if key:
            return name, key
    return None


class SearchArgs(BaseModel):
    query: str
    max_results: int = Field(8, ge=1, le=20)


@tool("web_search", category="web", parallel_safe=True, native=WEB_SEARCH_NATIVE)
async def web_search(args: SearchArgs, ctx: ToolContext):
    """Search the web. Returns titles, URLs and snippets; use web_fetch to read a result."""
    backend = search_backend()
    if backend is None:
        raise ToolError("没有配置网页搜索服务（在设置里填写 Tavily 或 Brave 的 API Key）。可以改用 search_papers 检索文献。")
    name, key = backend
    http = _http(ctx)
    try:
        if name == "tavily":
            resp = await http.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                                   json_body={"query": args.query, "max_results": args.max_results})
            items = [(r.get("title"), r.get("url"), r.get("content")) for r in resp.json().get("results", [])]
        else:
            resp = await http.get("https://api.search.brave.com/res/v1/web/search",
                                  params={"q": args.query, "count": args.max_results},
                                  headers={"X-Subscription-Token": key, "Accept": "application/json"})
            items = [(r.get("title"), r.get("url"), r.get("description"))
                     for r in resp.json().get("web", {}).get("results", [])]
    except HttpError as exc:
        raise ToolError(f"搜索失败: {exc}") from exc
    if not items:
        return "没有搜索结果。"
    return "\n\n".join(f"{i}. {t}\n   {u}\n   {(s or '').strip()[:400]}" for i, (t, u, s) in enumerate(items, 1))


WEB_TOOLS = [web_fetch, web_search]
