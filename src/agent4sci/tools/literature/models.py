"""Normalised paper record shared by all literature sources."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d)
    return d or None


def norm_arxiv(arxiv_id: str | None) -> str | None:
    if not arxiv_id:
        return None
    a = arxiv_id.strip()
    a = re.sub(r"^(https?://arxiv\.org/(abs|pdf)/|arxiv:)", "", a, flags=re.I)
    a = re.sub(r"\.pdf$", "", a)
    a = re.sub(r"v\d+$", "", a)
    return a or None


def norm_title(title: str | None) -> str:
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title).lower()
    return re.sub(r"[^a-z0-9一-鿿]+", "", t)


class Paper(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    tldr: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    s2_id: str | None = None
    openalex_id: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    citation_count: int | None = None
    sources: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        self.doi = norm_doi(self.doi)
        self.arxiv_id = norm_arxiv(self.arxiv_id)

    @property
    def ident(self) -> str:
        if self.doi:
            return f"doi:{self.doi}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        if self.s2_id:
            return f"s2:{self.s2_id}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id}"
        return f"title:{norm_title(self.title)[:60]}"

    def keys(self) -> list[str]:
        keys = []
        if self.doi:
            keys.append("doi:" + self.doi)
            if self.doi.startswith("10.48550/arxiv."):
                keys.append("arxiv:" + self.doi.split("arxiv.", 1)[1])
        if self.arxiv_id:
            keys.append("arxiv:" + self.arxiv_id.lower())
        if self.pmid:
            keys.append("pmid:" + self.pmid)
        title = norm_title(self.title)
        if len(title) > 20:
            keys.append(f"title:{title}")
        return keys

    def merge(self, other: "Paper") -> None:
        for field in ("year", "venue", "abstract", "tldr", "doi", "arxiv_id", "pmid", "pmcid", "s2_id",
                      "openalex_id", "url", "pdf_url"):
            if not getattr(self, field) and getattr(other, field):
                setattr(self, field, getattr(other, field))
        if not self.authors and other.authors:
            self.authors = other.authors
        if other.citation_count is not None:
            self.citation_count = max(self.citation_count or 0, other.citation_count)
        for s in other.sources:
            if s not in self.sources:
                self.sources.append(s)

    def authors_short(self) -> str:
        if not self.authors:
            return "（作者未知）"
        more = " 等" if len(self.authors) > 3 else ""
        return ", ".join(self.authors[:3]) + more

    def render(self, index: int | None = None, abstract_chars: int = 400) -> str:
        head = f"{index}. " if index is not None else ""
        cites = f" · 被引 {self.citation_count}" if self.citation_count is not None else ""
        venue = f" · {self.venue}" if self.venue else ""
        lines = [f"{head}{self.title} ({self.year or '?'})", f"   {self.authors_short()}{venue}{cites}",
                 f"   id: {self.ident}" + (f" | pdf: {self.pdf_url}" if self.pdf_url else "")]
        text = self.tldr or self.abstract
        if text and abstract_chars:
            lines.append("   " + (text[:abstract_chars] + ("…" if len(text) > abstract_chars else "")))
        return "\n".join(lines)


def merge_papers(result_lists: list[list[Paper]]) -> list[Paper]:
    """Interleave per-source rankings and merge duplicates (same DOI / arXiv / PMID / title)."""
    merged: list[Paper] = []
    index: dict[str, Paper] = {}
    longest = max((len(r) for r in result_lists), default=0)
    for rank in range(longest):
        for results in result_lists:
            if rank >= len(results):
                continue
            paper = results[rank]
            existing = next((index[k] for k in paper.keys() if k in index), None)
            if existing is not None:
                existing.merge(paper)
                target = existing
            else:
                target = paper.model_copy(deep=True)
                merged.append(target)
            for key in target.keys():
                index[key] = target
    return merged


def parse_id(raw: str) -> tuple[str, str]:
    """Classify a user/model supplied identifier → (kind, value)."""
    value = raw.strip()
    lower = value.lower()
    for prefix in ("doi:", "arxiv:", "pmid:", "s2:", "openalex:", "pmcid:"):
        if lower.startswith(prefix):
            return prefix[:-1], value[len(prefix):].strip()
    if re.match(r"^(https?://(dx\.)?doi\.org/)?10\.\d{4,9}/\S+$", value, re.I):
        return "doi", norm_doi(value) or value
    if re.match(r"^(https?://arxiv\.org/(abs|pdf)/)?\d{4}\.\d{4,5}(v\d+)?(\.pdf)?$", value, re.I) or re.match(
            r"^[a-z\-]+(\.[A-Z]{2})?/\d{7}(v\d+)?$", value):
        return "arxiv", norm_arxiv(value) or value
    if re.fullmatch(r"\d{5,9}", value):
        return "pmid", value
    if re.fullmatch(r"W\d+", value):
        return "openalex", value
    if re.fullmatch(r"[0-9a-f]{40}", value):
        return "s2", value
    return "unknown", value
