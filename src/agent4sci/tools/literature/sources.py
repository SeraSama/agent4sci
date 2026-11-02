"""Thin async clients for arXiv, Semantic Scholar, OpenAlex, Crossref and PubMed."""

from __future__ import annotations

import asyncio
import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from ..http import HttpClient, HttpError
from .models import Paper

WEEK = 7 * 24 * 3600
DAY = 24 * 3600


@dataclass
class LitConfig:
    s2_api_key: str | None = None
    openalex_api_key: str | None = None
    ncbi_api_key: str | None = None
    contact_email: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _strip_tags(text: str | None) -> str | None:
    if not text:
        return text
    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def _year_range(year_from: int | None, year_to: int | None) -> tuple[int | None, int | None]:
    return year_from, year_to


# ---- arXiv -----------------------------------------------------------------------------

ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
         "os": "http://a9.com/-/spec/opensearch/1.1/"}


def _arxiv_terms(query: str) -> list[str]:
    return [t for t in re.split(r"[^\w\-]+", query) if len(t) > 1][:8]


def arxiv_query(query: str, year_from: int | None = None, year_to: int | None = None,
                match: str = "all") -> str:
    """``match``: "all" words; "first" word plus any of the others; "any" word (relevance-ranked)."""
    if re.search(r"\b(ti|au|abs|co|jr|cat|rn|id|all):", query):
        q = query
    else:
        terms = [f"all:{t}" for t in _arxiv_terms(query)]
        if match == "first" and len(terms) > 2:
            q = f"{terms[0]} AND ({' OR '.join(terms[1:])})"
        else:
            q = (" OR " if match == "any" else " AND ").join(terms) or f'all:"{query}"'
    if year_from or year_to:
        start = f"{year_from or 1990}01010000"
        end = f"{year_to or 2100}12312359"
        q = f"({q}) AND submittedDate:[{start} TO {end}]"
    return q


def parse_arxiv_feed(xml_text: str) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall("a:entry", _ATOM):
        raw_id = (entry.findtext("a:id", "", _ATOM) or "").strip()
        if "arxiv.org/abs/" not in raw_id:
            continue
        pdf = next((link.get("href") for link in entry.findall("a:link", _ATOM) if link.get("title") == "pdf"), None)
        published = entry.findtext("a:published", "", _ATOM) or ""
        papers.append(Paper(
            title=" ".join((entry.findtext("a:title", "", _ATOM) or "").split()),
            authors=[a.findtext("a:name", "", _ATOM) for a in entry.findall("a:author", _ATOM)],
            year=int(published[:4]) if published[:4].isdigit() else None,
            venue=entry.findtext("arxiv:journal_ref", None, _ATOM) or "arXiv",
            abstract=" ".join((entry.findtext("a:summary", "", _ATOM) or "").split()),
            doi=entry.findtext("arxiv:doi", None, _ATOM),
            arxiv_id=raw_id.split("/abs/")[-1],
            url=raw_id, pdf_url=pdf, sources=["arxiv"],
        ))
    return papers


def _urllib_get(url: str, user_agent: str) -> str:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": user_agent}), timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise HttpError(f"HTTP {exc.code} from export.arxiv.org", exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise HttpError(f"网络错误: {exc}") from exc


async def _arxiv_fetch(http: HttpClient, params: dict[str, Any]) -> str:
    try:
        return (await http.get(ARXIV_API, params=params, cache_ttl=WEEK)).text
    except HttpError as exc:
        if exc.status != 406:
            raise
    # export.arxiv.org's CDN has answered httpx requests with 406 on cache misses while serving the standard
    # library's client normally (seen 2026-09); retry through urllib, keeping the host's one-request-per-3-s limit.
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    async with http._limiter("export.arxiv.org"):
        return await asyncio.to_thread(_urllib_get, url, http.client.headers.get("user-agent", "agent4sci"))


async def arxiv_search(http: HttpClient, query: str, limit: int = 10, year_from: int | None = None,
                       year_to: int | None = None, **_: Any) -> list[Paper]:
    params = {"search_query": arxiv_query(query, year_from, year_to), "start": 0, "max_results": limit,
              "sortBy": "relevance"}
    papers = parse_arxiv_feed(await _arxiv_fetch(http, params))
    if not papers and ":" not in query and len(_arxiv_terms(query)) > 2:
        # an AND of every word often finds nothing: keep the first (topic) word required, then any word
        for match in ("first", "any"):
            params["search_query"] = arxiv_query(query, year_from, year_to, match=match)
            papers = parse_arxiv_feed(await _arxiv_fetch(http, params))
            if papers:
                break
    return papers


async def arxiv_get(http: HttpClient, arxiv_id: str) -> Paper | None:
    resp = await http.get(ARXIV_API, params={"id_list": arxiv_id, "max_results": 1}, cache_ttl=WEEK)
    papers = parse_arxiv_feed(resp.text)
    return papers[0] if papers else None


# ---- Semantic Scholar -----------------------------------------------------------------------

S2_GRAPH = "https://api.semanticscholar.org/graph/v1"
S2_RECS = "https://api.semanticscholar.org/recommendations/v1"
S2_FIELDS = "title,year,venue,externalIds,abstract,citationCount,openAccessPdf,authors,url,tldr"


def s2_paper(d: dict[str, Any]) -> Paper:
    ext = d.get("externalIds") or {}
    return Paper(
        title=d.get("title") or "(untitled)", authors=[a.get("name", "") for a in d.get("authors") or []],
        year=d.get("year"), venue=d.get("venue") or None, abstract=d.get("abstract"),
        tldr=(d.get("tldr") or {}).get("text") if isinstance(d.get("tldr"), dict) else None,
        doi=ext.get("DOI"), arxiv_id=ext.get("ArXiv"), pmid=str(ext["PubMed"]) if ext.get("PubMed") else None,
        s2_id=d.get("paperId"), url=d.get("url"), citation_count=d.get("citationCount"),
        pdf_url=(d.get("openAccessPdf") or {}).get("url") or None, sources=["s2"],
    )


def _s2_headers(cfg: LitConfig) -> dict[str, str]:
    return {"x-api-key": cfg.s2_api_key} if cfg.s2_api_key else {}


def s2_id(kind: str, value: str) -> str:
    return {"doi": f"DOI:{value}", "arxiv": f"ARXIV:{value}", "pmid": f"PMID:{value}"}.get(kind, value)


async def s2_search(http: HttpClient, query: str, limit: int = 10, year_from: int | None = None,
                    year_to: int | None = None, cfg: LitConfig | None = None, **_: Any) -> list[Paper]:
    cfg = cfg or LitConfig()
    params: dict[str, Any] = {"query": query, "limit": limit, "fields": S2_FIELDS}
    if year_from or year_to:
        params["year"] = f"{year_from or ''}-{year_to or ''}"
    resp = await http.get(f"{S2_GRAPH}/paper/search", params=params, headers=_s2_headers(cfg), cache_ttl=WEEK)
    return [s2_paper(d) for d in resp.json().get("data", [])]


async def s2_get(http: HttpClient, paper_id: str, cfg: LitConfig | None = None) -> Paper | None:
    resp = await http.get(f"{S2_GRAPH}/paper/{paper_id}", params={"fields": S2_FIELDS},
                          headers=_s2_headers(cfg or LitConfig()), cache_ttl=WEEK, raise_for_status=False)
    return s2_paper(resp.json()) if resp.ok else None


async def s2_graph(http: HttpClient, paper_id: str, relation: str, limit: int = 20,
                   cfg: LitConfig | None = None) -> list[Paper]:
    headers = _s2_headers(cfg or LitConfig())
    fields = "title,year,venue,externalIds,citationCount,authors,url"
    if relation == "recommendations":
        resp = await http.get(f"{S2_RECS}/papers/forpaper/{paper_id}", params={"limit": limit, "fields": fields},
                              headers=headers, cache_ttl=WEEK)
        return [s2_paper(d) for d in resp.json().get("recommendedPapers", [])]
    key = "citingPaper" if relation == "citations" else "citedPaper"
    resp = await http.get(f"{S2_GRAPH}/paper/{paper_id}/{relation}", params={"limit": limit, "fields": fields},
                          headers=headers, cache_ttl=WEEK)
    return [s2_paper(item[key]) for item in resp.json().get("data", []) if item.get(key, {}).get("title")]


# ---- OpenAlex ----------------------------------------------------------------------------

OPENALEX = "https://api.openalex.org"


def reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        positions.extend((i, word) for i in idxs)
    return " ".join(word for _, word in sorted(positions))


def openalex_paper(d: dict[str, Any]) -> Paper:
    ids = d.get("ids") or {}
    pmid = ids.get("pmid", "")
    loc = d.get("primary_location") or {}
    return Paper(
        title=d.get("display_name") or d.get("title") or "(untitled)",
        authors=[a.get("author", {}).get("display_name", "") for a in d.get("authorships") or []],
        year=d.get("publication_year"), venue=(loc.get("source") or {}).get("display_name"),
        abstract=reconstruct_abstract(d.get("abstract_inverted_index")), doi=d.get("doi"),
        pmid=pmid.rstrip("/").split("/")[-1] if pmid else None,
        openalex_id=(d.get("id") or "").split("/")[-1] or None, url=d.get("doi") or d.get("id"),
        pdf_url=(d.get("open_access") or {}).get("oa_url"), citation_count=d.get("cited_by_count"),
        sources=["openalex"],
    )


def _openalex_params(cfg: LitConfig) -> dict[str, str]:
    params = {}
    if cfg.openalex_api_key:
        params["api_key"] = cfg.openalex_api_key
    if cfg.contact_email:
        params["mailto"] = cfg.contact_email
    return params


async def openalex_search(http: HttpClient, query: str, limit: int = 10, year_from: int | None = None,
                          year_to: int | None = None, cfg: LitConfig | None = None, **_: Any) -> tuple[list[Paper], float]:
    cfg = cfg or LitConfig()
    params: dict[str, Any] = {"search": query, "per_page": limit, **_openalex_params(cfg)}
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    resp = await http.get(f"{OPENALEX}/works", params=params, cache_ttl=WEEK)
    data = resp.json()
    cost = float((data.get("meta") or {}).get("cost_usd") or 0.0) if not resp.from_cache else 0.0
    return [openalex_paper(d) for d in data.get("results", [])], cost


async def openalex_get(http: HttpClient, work_id: str, cfg: LitConfig | None = None) -> Paper | None:
    resp = await http.get(f"{OPENALEX}/works/{work_id}", params=_openalex_params(cfg or LitConfig()), cache_ttl=WEEK,
                          raise_for_status=False)
    return openalex_paper(resp.json()) if resp.ok else None


# ---- Crossref -------------------------------------------------------------------------------

CROSSREF = "https://api.crossref.org"


def crossref_paper(d: dict[str, Any]) -> Paper:
    parts = ((d.get("issued") or {}).get("date-parts") or [[None]])[0]
    return Paper(
        title=" ".join((d.get("title") or ["(untitled)"])[0].split()),
        authors=[" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in d.get("author") or []],
        year=parts[0] if parts and isinstance(parts[0], int) else None,
        venue=(d.get("container-title") or [None])[0], abstract=_strip_tags(d.get("abstract")), doi=d.get("DOI"),
        url=d.get("URL"), citation_count=d.get("is-referenced-by-count"), sources=["crossref"],
    )


async def crossref_search(http: HttpClient, query: str, limit: int = 10, year_from: int | None = None,
                          year_to: int | None = None, cfg: LitConfig | None = None, **_: Any) -> list[Paper]:
    cfg = cfg or LitConfig()
    params: dict[str, Any] = {"query.bibliographic": query, "rows": limit,
                              "select": "DOI,title,author,issued,container-title,is-referenced-by-count,abstract,URL"}
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}")
    if year_to:
        filters.append(f"until-pub-date:{year_to}")
    if filters:
        params["filter"] = ",".join(filters)
    if cfg.contact_email:
        params["mailto"] = cfg.contact_email
    resp = await http.get(f"{CROSSREF}/works", params=params, cache_ttl=WEEK)
    return [crossref_paper(d) for d in resp.json().get("message", {}).get("items", [])]


async def crossref_get(http: HttpClient, doi: str, cfg: LitConfig | None = None) -> Paper | None:
    params = {"mailto": cfg.contact_email} if cfg and cfg.contact_email else None
    resp = await http.get(f"{CROSSREF}/works/{doi}", params=params, cache_ttl=WEEK, raise_for_status=False)
    return crossref_paper(resp.json().get("message", {})) if resp.ok else None


# ---- PubMed ---------------------------------------------------------------------------------

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _ncbi_params(cfg: LitConfig) -> dict[str, str]:
    params = {"tool": "agent4sci"}
    if cfg.contact_email:
        params["email"] = cfg.contact_email
    if cfg.ncbi_api_key:
        params["api_key"] = cfg.ncbi_api_key
    return params


def parse_pubmed_xml(xml_text: str) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers = []
    for art in root.iter("PubmedArticle"):
        citation = art.find("MedlineCitation")
        if citation is None:
            continue
        article = citation.find("Article")
        if article is None:
            continue
        title = "".join(article.find("ArticleTitle").itertext()) if article.find("ArticleTitle") is not None else ""
        abstract_parts = []
        for node in article.findall("Abstract/AbstractText"):
            label = node.get("Label")
            text = "".join(node.itertext()).strip()
            abstract_parts.append(f"{label}: {text}" if label else text)
        authors = []
        for a in article.findall("AuthorList/Author"):
            last, first = a.findtext("LastName"), a.findtext("ForeName")
            name = " ".join(x for x in (first, last) if x) or a.findtext("CollectiveName") or ""
            if name:
                authors.append(name)
        year_text = article.findtext("Journal/JournalIssue/PubDate/Year") or article.findtext(
            "Journal/JournalIssue/PubDate/MedlineDate") or ""
        ids = {i.get("IdType"): (i.text or "").strip() for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
        pmid = citation.findtext("PMID")
        papers.append(Paper(
            title=" ".join(title.split()), authors=authors,
            year=int(year_text[:4]) if year_text[:4].isdigit() else None,
            venue=article.findtext("Journal/Title"), abstract=" ".join(abstract_parts) or None,
            doi=ids.get("doi"), pmid=pmid, pmcid=ids.get("pmc"),
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None, sources=["pubmed"],
        ))
    return papers


async def pubmed_fetch(http: HttpClient, pmids: list[str], cfg: LitConfig | None = None) -> list[Paper]:
    if not pmids:
        return []
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", **_ncbi_params(cfg or LitConfig())}
    resp = await http.get(f"{EUTILS}/efetch.fcgi", params=params, cache_ttl=WEEK)
    return parse_pubmed_xml(resp.text)


async def pubmed_search(http: HttpClient, query: str, limit: int = 10, year_from: int | None = None,
                        year_to: int | None = None, cfg: LitConfig | None = None, **_: Any) -> list[Paper]:
    cfg = cfg or LitConfig()
    params: dict[str, Any] = {"db": "pubmed", "term": query, "retmax": limit, "retmode": "json",
                              **_ncbi_params(cfg)}
    if year_from or year_to:
        params.update(datetype="pdat", mindate=str(year_from or 1900), maxdate=str(year_to or 3000))
    resp = await http.get(f"{EUTILS}/esearch.fcgi", params=params, cache_ttl=DAY)
    ids = resp.json().get("esearchresult", {}).get("idlist", [])
    papers = await pubmed_fetch(http, ids, cfg)
    order = {pmid: i for i, pmid in enumerate(ids)}
    return sorted(papers, key=lambda p: order.get(p.pmid or "", 1_000))
