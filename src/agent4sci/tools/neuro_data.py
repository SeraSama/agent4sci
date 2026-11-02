"""Public neuroscience data: DANDI, OpenNeuro, NeuroStore, Allen Brain Atlas (RMA)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from .base import ToolContext, ToolError, tool
from .http import HttpClient, HttpError

DANDI = "https://api.dandiarchive.org/api"
OPENNEURO = "https://openneuro.org/crn/graphql"
NEUROSTORE = "https://neurostore.org/api"
ALLEN = "https://api.brain-map.org/api/v2/data/query.json"
HOUR = 3600


def _http(ctx: ToolContext) -> HttpClient:
    client = ctx.services.get("http")
    if client is None:
        client = ctx.services["http"] = HttpClient()
    return client


def _fmt_size(size: Any) -> str:
    try:
        n = float(size)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


async def dandi_search(http: HttpClient, query: str, limit: int) -> list[str]:
    resp = await http.get(f"{DANDI}/dandisets/", params={"search": query, "page_size": limit}, cache_ttl=HOUR)
    out = []
    for d in resp.json().get("results", []):
        version = d.get("most_recent_published_version") or d.get("draft_version") or {}
        out.append(f"DANDI:{d.get('identifier')} — {version.get('name', '(无标题)')} "
                   f"[{version.get('asset_count', '?')} 个文件, {_fmt_size(version.get('size'))}, "
                   f"版本 {version.get('version', 'draft')}]")
    return out


_OPENNEURO_SEARCH = """query ($keywords: [String!], $modality: String, $first: Int) {
  advancedSearch(query: {keywords: $keywords, modality: $modality, publicOnly: true}, first: $first) {
    edges { node { id latestSnapshot { tag description { Name } } } }
  }
}"""


async def _openneuro_graphql(http: HttpClient, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """POST to OpenNeuro's GraphQL API. Query errors come back as HTTP 400 with a JSON ``errors`` list."""
    resp = await http.post(OPENNEURO, json_body={"query": query, "variables": variables}, cache_ttl=HOUR,
                           raise_for_status=False)
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict) or (not resp.ok and not data.get("errors")):
        raise HttpError(f"HTTP {resp.status_code} from openneuro.org: {resp.text[:300]}", resp.status_code)
    return data


async def openneuro_search(http: HttpClient, query: str, limit: int, modality: str | None) -> list[str]:
    variables = {"keywords": [query], "modality": modality, "first": limit}
    data = await _openneuro_graphql(http, _OPENNEURO_SEARCH, variables)
    if data.get("errors"):
        raise RuntimeError(data["errors"][0].get("message", "GraphQL error"))
    edges = (((data.get("data") or {}).get("advancedSearch") or {}).get("edges")) or []
    out = []
    for edge in edges:
        node = edge.get("node") or {}
        snap = node.get("latestSnapshot") or {}
        out.append(f"OpenNeuro:{node.get('id')} — {(snap.get('description') or {}).get('Name', '(无标题)')} "
                   f"[快照 {snap.get('tag', '?')}]")
    return out


async def neurostore_search(http: HttpClient, query: str, limit: int) -> list[str]:
    resp = await http.get(f"{NEUROSTORE}/studies/", params={"search": query, "page_size": limit}, cache_ttl=HOUR)
    out = []
    for s in resp.json().get("results", []):
        ids = " ".join(x for x in (f"doi:{s['doi']}" if s.get("doi") else "",
                                   f"pmid:{s['pmid']}" if s.get("pmid") else "") if x)
        out.append(f"NeuroStore:{s.get('id')} — {s.get('name', '(无标题)')} ({s.get('year') or '?'}) {ids}".strip())
    return out


class NeuroSearchArgs(BaseModel):
    query: str
    sources: list[Literal["dandi", "openneuro", "neurostore"]] = Field(
        default_factory=lambda: ["dandi", "openneuro", "neurostore"])
    modality: str | None = Field(None, description="OpenNeuro modality filter, e.g. eeg, meg, mri, ieeg, pet")
    limit: int = Field(8, ge=1, le=50)


@tool("search_neuro_datasets", category="data", parallel_safe=True)
async def search_neuro_datasets(args: NeuroSearchArgs, ctx: ToolContext):
    """Search public neuroscience datasets: DANDI (NWB electrophysiology/imaging), OpenNeuro (BIDS MRI/EEG/MEG)
    and NeuroStore (coordinate-based fMRI studies)."""
    http = _http(ctx)
    calls = {"dandi": lambda: dandi_search(http, args.query, args.limit),
             "openneuro": lambda: openneuro_search(http, args.query, args.limit, args.modality),
             "neurostore": lambda: neurostore_search(http, args.query, args.limit)}
    results = await asyncio.gather(*(calls[s]() for s in args.sources), return_exceptions=True)
    lines = []
    for source, outcome in zip(args.sources, results):
        lines.append(f"## {source}")
        if isinstance(outcome, Exception):
            lines.append(f"（失败: {str(outcome)[:200]}）")
        else:
            lines.extend(outcome or ["（无结果）"])
    return "\n".join(lines)


class DandisetArgs(BaseModel):
    id: str = Field(description="Dandiset id, e.g. 000123 or DANDI:000123")
    version: str = Field("draft", description="'draft' or a published version")
    assets: int = Field(15, ge=0, le=200, description="How many asset paths to list")


@tool("get_dandiset", category="data", parallel_safe=True)
async def get_dandiset(args: DandisetArgs, ctx: ToolContext):
    """Metadata and file listing of one DANDI dandiset."""
    ident = args.id.replace("DANDI:", "").strip()
    http = _http(ctx)
    try:
        meta = (await http.get(f"{DANDI}/dandisets/{ident}/versions/{args.version}/", cache_ttl=HOUR)).json()
        assets = (await http.get(f"{DANDI}/dandisets/{ident}/versions/{args.version}/assets/",
                                 params={"page_size": args.assets}, cache_ttl=HOUR)).json() if args.assets else {}
    except HttpError as exc:
        raise ToolError(f"DANDI 请求失败: {exc}") from exc
    lines = [f"DANDI:{ident} — {meta.get('name')}", f"许可证: {', '.join(meta.get('license') or [])}",
             f"DOI: {meta.get('doi') or '（草稿无 DOI）'}", "", (meta.get("description") or "")[:2000]]
    summary = meta.get("assetsSummary") or {}
    if summary:
        lines.append(f"\n文件数 {summary.get('numberOfFiles')}，大小 {_fmt_size(summary.get('numberOfBytes'))}，"
                     f"物种 {[s.get('name') for s in summary.get('species', [])]}，"
                     f"方法 {[m.get('name') for m in summary.get('approach', [])][:5]}")
    for a in assets.get("results", []):
        lines.append(f"- {a.get('path')} ({_fmt_size(a.get('size'))}) asset_id={a.get('asset_id')}")
    lines.append(f"\n流式读取示例：dandi 的 asset 下载地址为 {DANDI}/assets/<asset_id>/download/")
    return "\n".join(lines)


_OPENNEURO_DATASET = """query ($id: ID!) {
  dataset(id: $id) { id latestSnapshot { tag description { Name Authors DatasetDOI License }
    summary { modalities subjects tasks totalFiles size } } }
}"""
_OPENNEURO_DATASET_MIN = """query ($id: ID!) { dataset(id: $id) { id latestSnapshot { tag description { Name } } } }"""


class OpenNeuroArgs(BaseModel):
    id: str = Field(description="Accession, e.g. ds000117")


@tool("get_openneuro_dataset", category="data", parallel_safe=True)
async def get_openneuro_dataset(args: OpenNeuroArgs, ctx: ToolContext):
    """Details of one OpenNeuro (BIDS) dataset: authors, DOI, license, modalities, subjects, tasks."""
    http = _http(ctx)
    ident = args.id.replace("OpenNeuro:", "").strip()
    data: dict[str, Any] = {}
    for query in (_OPENNEURO_DATASET, _OPENNEURO_DATASET_MIN):
        data = await _openneuro_graphql(http, query, {"id": ident})
        if not data.get("errors"):
            break
    if data.get("errors"):
        raise ToolError(f"OpenNeuro 查询失败: {data['errors'][0].get('message')}")
    ds = (data.get("data") or {}).get("dataset") or {}
    snap = ds.get("latestSnapshot") or {}
    return (f"OpenNeuro:{ident}（快照 {snap.get('tag')}）\n" + json.dumps(snap, ensure_ascii=False, indent=2)
            + f"\n\n下载：openneuro download --dataset {ident} <目标目录>（或 DataLad）")


class AllenArgs(BaseModel):
    criteria: str = Field(description="RMA criteria, e.g. model::Structure,rma::criteria,[acronym$eq'VISp']")
    num_rows: int = Field(20, ge=1, le=2000)


@tool("allen_query", category="data", parallel_safe=True)
async def allen_query(args: AllenArgs, ctx: ToolContext):
    """Query the Allen Brain Atlas RMA API (structures, experiments, gene expression …)."""
    resp = await _http(ctx).get(ALLEN, params={"criteria": args.criteria, "num_rows": args.num_rows}, cache_ttl=HOUR)
    data = resp.json()
    if not data.get("success", False):
        raise ToolError(f"Allen API 错误: {data.get('msg')}")
    return json.dumps(data.get("msg"), ensure_ascii=False, indent=1)[:20000]


NEURO_TOOLS = [search_neuro_datasets, get_dandiset, get_openneuro_dataset, allen_query]
