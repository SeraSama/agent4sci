"""References: fetch real BibTeX records (DOI content negotiation) and check citations.

LLM-written BibTeX is wrong about half the time, so agents may only add
references through ``add_reference``, which retrieves the record from the
registration agency (Crossref/DataCite via doi.org) or builds it from PubMed.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field

from .base import ToolContext, ToolError, tool
from .http import HttpError
from .literature.models import Paper, norm_doi, parse_id
from .literature.tools import _http, resolve_paper

BIB_FILE = "references.bib"
REF_LOG = "lit/references.jsonl"
_ENTRY_HEAD = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.S)
_CITE_PANDOC = re.compile(r"@([A-Za-z0-9_](?:[A-Za-z0-9_:\-\.]*[A-Za-z0-9_])?)")
_CITE_LATEX = re.compile(r"\\cite[pt]?\*?(?:\[[^\]]*\])*\{([^}]+)\}")


def bib_keys(text: str) -> dict[str, str]:
    """key → entry text."""
    entries: dict[str, str] = {}
    matches = list(_ENTRY_HEAD.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entries[m.group(2)] = text[m.start():end].strip()
    return entries


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def make_key(paper: Paper, existing: set[str]) -> str:
    last = ""
    if paper.authors:
        first = paper.authors[0].replace(",", " ").split()
        last = _ascii(first[-1] if first else "")
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", _ascii(paper.title))
                 if w.lower() not in {"with", "from", "that", "this", "using", "towards", "toward"}), "")
    base = re.sub(r"[^a-z0-9]", "", f"{last}{paper.year or ''}{word}".lower()) or "ref"
    key, n = base, 0
    while key in existing:
        n += 1
        key = base + "abcdefghijklmnopqrstuvwxyz"[(n - 1) % 26] * (1 + (n - 1) // 26)
    return key


def _bibtex_from_paper(paper: Paper, key: str) -> str:
    fields = {"title": paper.title, "author": " and ".join(paper.authors), "year": str(paper.year or ""),
              "journal": paper.venue or "", "doi": paper.doi or "", "pmid": paper.pmid or "", "url": paper.url or ""}
    if paper.arxiv_id:
        fields.update(eprint=paper.arxiv_id, archivePrefix="arXiv")
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
    return f"@article{{{key},\n{body}\n}}"


class AddRefArgs(BaseModel):
    id: str = Field(description="doi:…, arxiv:…, pmid:… (as returned by search_papers)")


@tool("add_reference", category="literature.write")
async def add_reference(args: AddRefArgs, ctx: ToolContext):
    """Add a paper to references.bib using its verified bibliographic record and return the citation key
    to use as [@key]. Never write BibTeX by hand."""
    bib_path = ctx.workspace / BIB_FILE
    existing_text = bib_path.read_text("utf-8") if bib_path.exists() else ""
    entries = bib_keys(existing_text)
    paper = await resolve_paper(ctx, args.id)
    doi = paper.doi or (f"10.48550/arXiv.{paper.arxiv_id}" if paper.arxiv_id else None)
    for key, entry in entries.items():
        if doi and norm_doi(doi) and norm_doi(doi) in entry.lower():
            return f"已存在：[@{key}] — {paper.title}"
    bibtex, source, registered = None, "", None
    if doi:
        try:
            resp = await _http(ctx).get(f"https://doi.org/{doi}", headers={"Accept": "application/x-bibtex"},
                                        cache_ttl=30 * 24 * 3600)
            text = resp.text.strip()
            if text.startswith("@"):
                registered = text
        except HttpError:
            registered = None
    if registered:
        # the registered record wins over search metadata, which may describe the preprint (another year)
        year = re.search(r"\byear\s*=\s*[{\"]?\s*(\d{4})", registered, re.I)
        if year and paper.year != int(year.group(1)):
            paper = paper.model_copy(update={"year": int(year.group(1))})
    key = make_key(paper, set(entries))
    if registered:
        bibtex = _ENTRY_HEAD.sub(lambda m: f"@{m.group(1)}{{{key},", registered, count=1)
        source = "doi.org content negotiation"
    if bibtex is None:
        if not (paper.pmid or paper.arxiv_id or paper.doi):
            raise ToolError("没有可核验的标识符（DOI/arXiv/PMID），不能添加这条参考文献。")
        bibtex = _bibtex_from_paper(paper, key)
        source = f"built from {paper.sources[0] if paper.sources else 'record'} metadata"
    with bib_path.open("a", encoding="utf-8") as f:
        f.write(("\n" if existing_text and not existing_text.endswith("\n\n") else "") + bibtex.strip() + "\n\n")
    log = ctx.workspace / REF_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "ident": paper.ident, "doi": doi, "title": paper.title, "year": paper.year,
                            "authors": paper.authors[:10], "source": source, "retrieved_at": time.time()},
                           ensure_ascii=False) + "\n")
    ctx.emit("reference.added", key=key, title=paper.title, ident=paper.ident)
    return f"已添加 [@{key}] — {paper.authors_short()} ({paper.year}). {paper.title}（来源：{source}）"


def find_citations(text: str) -> list[str]:
    keys: list[str] = []
    for block in re.findall(r"\[([^\]]*@[^\]]*)\]", text):
        keys += _CITE_PANDOC.findall(block)
    for group in _CITE_LATEX.findall(text):
        keys += [k.strip() for k in group.split(",") if k.strip()]
    seen: list[str] = []
    for k in keys:
        if k not in seen:
            seen.append(k)
    return seen


def check_citations_text(text: str, bib_text: str) -> dict:
    known = bib_keys(bib_text)
    cited = find_citations(text)
    return {"cited": cited, "unknown": [k for k in cited if k not in known],
            "unused": [k for k in known if k not in cited], "n_references": len(known)}


class CheckArgs(BaseModel):
    path: str = Field(description="Markdown/LaTeX file to check, e.g. paper/paper.md")


@tool("check_citations", category="literature", parallel_safe=True)
async def check_citations(args: CheckArgs, ctx: ToolContext):
    """Verify that every citation key used in a document exists in references.bib (verified records)."""
    doc = ctx.resolve(args.path)
    if not doc.exists():
        raise ToolError(f"文件不存在: {args.path}")
    bib_path = ctx.workspace / BIB_FILE
    report = check_citations_text(doc.read_text("utf-8"), bib_path.read_text("utf-8") if bib_path.exists() else "")
    lines = [f"引用了 {len(report['cited'])} 个键，references.bib 中共有 {report['n_references']} 条。"]
    if report["unknown"]:
        lines.append("❌ 未在 references.bib 中找到（必须修正）: " + ", ".join(report["unknown"]))
    else:
        lines.append("✅ 所有引用键都有经过核验的记录。")
    if report["unused"]:
        lines.append("未被引用的条目: " + ", ".join(report["unused"][:30]))
    return "\n".join(lines)


BIB_TOOLS = [add_reference, check_citations]
