"""Literature tools exposed to agents."""

from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..base import ToolContext, ToolError, ToolResult, tool
from ..http import HttpClient, HttpError
from . import sources as S
from .models import Paper, merge_papers, norm_arxiv, norm_doi, parse_id

SOURCES = ("openalex", "s2", "arxiv", "pubmed", "crossref")


def _http(ctx: ToolContext) -> HttpClient:
    client = ctx.services.get("http")
    if client is None:
        client = ctx.services["http"] = HttpClient()
    return client


def _cfg(ctx: ToolContext) -> S.LitConfig:
    return ctx.services.get("lit_config") or S.LitConfig()


def default_sources(cfg: S.LitConfig) -> list[str]:
    chosen = ["s2", "arxiv", "pubmed", "crossref"]
    if cfg.openalex_api_key:
        chosen.insert(0, "openalex")
    return chosen


def _save_papers(ctx: ToolContext, papers: list[Paper]) -> None:
    path = ctx.workspace / "lit" / "papers.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if path.exists():
        for line in path.read_text("utf-8").splitlines():
            try:
                seen.add(json.loads(line)["ident"])
            except (json.JSONDecodeError, KeyError):
                continue
    with path.open("a", encoding="utf-8") as f:
        for p in papers:
            if p.ident not in seen:
                seen.add(p.ident)
                f.write(json.dumps({"ident": p.ident, **p.model_dump(exclude_none=True)}, ensure_ascii=False) + "\n")


class SearchPapersArgs(BaseModel):
    query: str = Field(description="Search query (keywords or a natural-language description)")
    sources: list[Literal["openalex", "s2", "arxiv", "pubmed", "crossref"]] | None = Field(
        None, description="Sources to query; default: all configured")
    limit: int = Field(8, ge=1, le=50, description="Results per source")
    year_from: int | None = None
    year_to: int | None = None


@tool("search_papers", category="literature", parallel_safe=True)
async def search_papers(args: SearchPapersArgs, ctx: ToolContext):
    """Search scholarly literature across OpenAlex, Semantic Scholar, arXiv, PubMed and Crossref at once.
    Results are merged and de-duplicated; each has an id (doi:/arxiv:/pmid:) usable with get_paper,
    paper_graph, fetch_fulltext and add_reference. Results are also saved to lit/papers.jsonl."""
    http, cfg = _http(ctx), _cfg(ctx)
    chosen = list(args.sources or default_sources(cfg))
    funcs = {"openalex": S.openalex_search, "s2": S.s2_search, "arxiv": S.arxiv_search, "pubmed": S.pubmed_search,
             "crossref": S.crossref_search}
    tasks = [funcs[name](http, args.query, args.limit, args.year_from, args.year_to, cfg=cfg) for name in chosen]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    lists: list[list[Paper]] = []
    errors: list[str] = []
    cost = 0.0
    for name, outcome in zip(chosen, outcomes):
        if isinstance(outcome, Exception):
            errors.append(f"{name}: {str(outcome)[:160]}")
            continue
        if isinstance(outcome, tuple):
            outcome, c = outcome
            cost += c
        lists.append(outcome)
    papers = merge_papers(lists)
    _save_papers(ctx, papers)
    lines = [f"共 {len(papers)} 篇（来源：{', '.join(chosen)}）"]
    lines += [p.render(i, abstract_chars=300) for i, p in enumerate(papers, 1)]
    if errors:
        lines.append("部分来源失败：" + "；".join(errors))
    return ToolResult.text("\n".join(lines), cost_usd=cost, count=len(papers))


async def resolve_paper(ctx: ToolContext, identifier: str) -> Paper:
    http, cfg = _http(ctx), _cfg(ctx)
    kind, value = parse_id(identifier)
    candidates: list[Paper] = []
    if kind in ("doi", "arxiv", "pmid", "s2"):
        paper = await S.s2_get(http, S.s2_id(kind, value), cfg)
        if paper:
            candidates.append(paper)
    try:
        if kind == "arxiv":
            p = await S.arxiv_get(http, value)
            candidates += [p] if p else []
        elif kind == "pmid":
            candidates += await S.pubmed_fetch(http, [value], cfg)
        elif kind == "doi":
            p = await S.crossref_get(http, value, cfg)
            candidates += [p] if p else []
        elif kind == "openalex":
            p = await S.openalex_get(http, value, cfg)
            candidates += [p] if p else []
    except HttpError:
        pass
    # APIs occasionally return a different record than requested; keep only exact identifier matches.
    matching = [c for c in candidates if _matches(c, kind, value)]
    if not matching:
        raise ToolError(f"找不到这篇文献: {identifier}（支持 doi:…、arxiv:…、pmid:…、OpenAlex W…）")
    merged = merge_papers([[c] for c in matching])
    return merged[0]


def _matches(paper: Paper, kind: str, value: str) -> bool:
    if kind == "doi":
        return paper.doi == norm_doi(value)
    if kind == "arxiv":
        wanted = (norm_arxiv(value) or "").lower()
        return (paper.arxiv_id or "").lower() == wanted or (paper.doi or "") == f"10.48550/arxiv.{wanted}"
    if kind == "pmid":
        return paper.pmid == value
    if kind == "s2":
        return paper.s2_id == value
    if kind == "openalex":
        return paper.openalex_id == value
    return True


class GetPaperArgs(BaseModel):
    id: str = Field(description="doi:10.x/…, arxiv:2504.08066, pmid:123456, OpenAlex W…, or a bare DOI/arXiv id")


@tool("get_paper", category="literature", parallel_safe=True)
async def get_paper(args: GetPaperArgs, ctx: ToolContext):
    """Get full metadata and the abstract of one paper."""
    paper = await resolve_paper(ctx, args.id)
    body = paper.render(abstract_chars=0)
    if paper.tldr:
        body += f"\nTL;DR: {paper.tldr}"
    if paper.abstract:
        body += f"\n\n摘要：{paper.abstract}"
    return body


class GraphArgs(BaseModel):
    id: str
    relation: Literal["citations", "references", "recommendations"] = "references"
    limit: int = Field(20, ge=1, le=200)


@tool("paper_graph", category="literature", parallel_safe=True)
async def paper_graph(args: GraphArgs, ctx: ToolContext):
    """Walk the citation graph: papers citing this one, papers it cites, or recommended similar papers
    (Semantic Scholar)."""
    kind, value = parse_id(args.id)
    try:
        papers = await S.s2_graph(_http(ctx), S.s2_id(kind, value), args.relation, args.limit, _cfg(ctx))
    except HttpError as exc:
        raise ToolError(f"Semantic Scholar 请求失败（未配置 S2_API_KEY 时经常限流）: {exc}") from exc
    if not papers:
        return "没有结果。"
    return "\n".join(p.render(i, abstract_chars=0) for i, p in enumerate(papers, 1))


class FulltextArgs(BaseModel):
    id: str
    max_chars: int = Field(6000, ge=1000, le=100000,
                           description="How much of the text to return now; the whole text is saved to a file")


def _safe_name(ident: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", ident)[:120]


FULLTEXT_MIN_CHARS = 6000  # shorter HTML is a landing page (title + abstract), not the paper


def _citation_pdf_url(html: str, base: str) -> str | None:
    """The PDF link publishers put in <meta name="citation_pdf_url"> (Highwire / Google Scholar tags)."""
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    meta = BeautifulSoup(html, "html.parser").find("meta", attrs={"name": "citation_pdf_url"})
    content = meta.get("content") if meta is not None else None
    return urljoin(base, str(content)) if content else None


def _save_fulltext(ctx: ToolContext, paper: Paper, url: str, text: str, max_chars: int, what: str) -> str:
    out = ctx.workspace / "lit" / "text" / f"{_safe_name(paper.ident)}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"{paper.title}\n{paper.ident}\n来源: {url}\n\n{text}", encoding="utf-8")
    return (f"已提取全文（{what}，{len(text)} 字符）→ {ctx.display_path(out)}\n\n{text[:max_chars]}"
            + ("\n…（用 read_file 继续阅读）" if len(text) > max_chars else ""))


@tool("fetch_fulltext", category="literature", parallel_safe=True, timeout_s=300)
async def fetch_fulltext(args: FulltextArgs, ctx: ToolContext):
    """Download the open-access full text of a paper, extract the text to lit/text/<id>.txt and return the
    beginning. Read further with read_file on that path."""
    paper = await resolve_paper(ctx, args.id)
    queue = []
    if paper.arxiv_id:
        queue.append(f"https://arxiv.org/pdf/{paper.arxiv_id}")
    if paper.pdf_url:
        queue.append(paper.pdf_url)
    if paper.pmcid:
        queue.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{paper.pmcid}/pdf/")
    if not queue:
        raise ToolError("没有找到这篇文献的开放获取全文。可以用 get_paper 阅读摘要，或让研究者提供 PDF。")
    from pypdf import PdfReader

    from ..web import html_to_markdown

    tried: set[str] = set()
    html_text: tuple[str, str] | None = None  # (url, text) of an HTML full text, used if no PDF works
    last_error = ""
    while queue:
        url = queue.pop(0)
        if url in tried:
            continue
        tried.add(url)
        try:
            resp = await _http(ctx).get(url, max_bytes=60_000_000)
        except HttpError as exc:
            last_error = str(exc)
            continue
        if resp.content.startswith(b"%PDF"):
            reader = PdfReader(io.BytesIO(resp.content))
            text = "\n\n".join(f"[第 {i + 1} 页]\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages))
            return _save_fulltext(ctx, paper, url, text, args.max_chars, f"PDF {len(reader.pages)} 页")
        head = resp.content[:2048].lstrip().lower()
        if "html" in resp.headers.get("content-type", "") or head.startswith((b"<!doctype html", b"<html")):
            html = resp.text
            pdf = _citation_pdf_url(html, resp.url)
            if pdf and pdf not in tried:
                queue.insert(0, pdf)  # publishers link the PDF from the landing page
            _, text = html_to_markdown(html, resp.url)
            if len(text) >= FULLTEXT_MIN_CHARS and html_text is None:
                html_text = (resp.url, text)
            elif not pdf:
                last_error = f"{url} 只有摘要页，没有可下载的全文"
            continue
        last_error = f"{url} 返回的不是 PDF 或网页全文"
    if html_text is not None:
        return _save_fulltext(ctx, paper, html_text[0], html_text[1], args.max_chars, "网页全文")
    raise ToolError(f"全文下载失败: {last_error}")


LITERATURE_TOOLS = [search_papers, get_paper, paper_graph, fetch_fulltext]
