from __future__ import annotations

import json

import httpx2
import pytest

from agent4sci.tools.base import ToolContext, ToolError
from agent4sci.tools.bib import add_reference, check_citations, check_citations_text, find_citations
from agent4sci.tools.findings import query_findings, record_finding
from agent4sci.tools.http import HttpClient
from agent4sci.tools.literature import LitConfig, fetch_fulltext, get_paper, merge_papers, paper_graph, parse_id, search_papers
from agent4sci.tools.literature.models import Paper
from agent4sci.tools.literature.sources import arxiv_query, reconstruct_abstract
from agent4sci.tools.neuro_data import allen_query, get_dandiset, get_openneuro_dataset, search_neuro_datasets
from agent4sci.tools.provenance import lint_bare_numbers, render_document
from helpers import make_pdf

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2504.08066v2</id>
    <published>2025-04-10T00:00:00Z</published>
    <title>The AI Scientist v2:
      Workshop-Level Discovery</title>
    <summary>We present an agentic tree search system.</summary>
    <author><name>Yutaro Yamada</name></author>
    <author><name>Robert Lange</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/2504.08066v2" rel="related"/>
  </entry>
</feed>"""

S2_SEARCH = {"total": 2, "data": [
    {"paperId": "abc", "title": "The AI Scientist v2: Workshop-Level Discovery", "year": 2025, "venue": "",
     "externalIds": {"ArXiv": "2504.08066"}, "abstract": None, "citationCount": 50,
     "authors": [{"name": "Yutaro Yamada"}], "tldr": {"text": "Tree search for science."}},
    {"paperId": "def", "title": "Deep learning for EEG decoding", "year": 2020, "venue": "J Neural Eng",
     "externalIds": {"DOI": "10.1088/1741-2552/ABC123", "PubMed": "32000001"}, "citationCount": 300,
     "authors": [{"name": "Ana Silva"}, {"name": "Bo Li"}], "openAccessPdf": {"url": "https://oa.example/eeg.pdf"}},
]}

OPENALEX = {"meta": {"count": 1, "cost_usd": 0.001}, "results": [
    {"id": "https://openalex.org/W123", "doi": "https://doi.org/10.1088/1741-2552/abc123",
     "display_name": "Deep learning for EEG decoding", "publication_year": 2020, "cited_by_count": 310,
     "authorships": [{"author": {"display_name": "Ana Silva"}}],
     "abstract_inverted_index": {"EEG": [0], "decoding": [1], "works": [2]},
     "primary_location": {"source": {"display_name": "Journal of Neural Engineering"}},
     "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/32000001"}, "open_access": {"oa_url": None}},
]}

CROSSREF = {"message": {"items": [
    {"DOI": "10.1088/1741-2552/abc123", "title": ["Deep learning for EEG decoding"],
     "author": [{"given": "Ana", "family": "Silva"}], "issued": {"date-parts": [[2020, 5]]},
     "container-title": ["Journal of Neural Engineering"], "is-referenced-by-count": 290},
]}}

PUBMED_SEARCH = {"esearchresult": {"idlist": ["39000002"]}}
PUBMED_XML = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>39000002</PMID><Article>
<Journal><Title>NeuroImage</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>Hippocampal theta in working memory</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Theta matters.</AbstractText><AbstractText Label="RESULTS">It does.</AbstractText></Abstract>
<AuthorList><Author><LastName>Zhang</LastName><ForeName>Wei</ForeName></Author></AuthorList>
</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="pubmed">39000002</ArticleId></ArticleIdList>
</PubmedData></PubmedArticle></PubmedArticleSet>"""

BIBTEX = "@article{Silva_2020, title={Deep learning for EEG decoding}, author={Silva, Ana}, year={2020}, doi={10.1088/1741-2552/abc123}}"


def router(request: httpx2.Request) -> httpx2.Response:
    host, path = request.url.host, request.url.path
    if host == "export.arxiv.org":
        return httpx2.Response(200, text=ARXIV_XML)
    if host == "api.semanticscholar.org":
        if path.endswith("/references"):
            return httpx2.Response(200, json={"data": [{"citedPaper": S2_SEARCH["data"][1]}]})
        if "/paper/search" in path:
            return httpx2.Response(200, json=S2_SEARCH)
        if path.startswith("/graph/v1/paper/"):
            requested = path[len("/graph/v1/paper/"):].upper()
            if requested.startswith("ARXIV:2504.08066"):
                return httpx2.Response(200, json=S2_SEARCH["data"][0])
            if requested.startswith("DOI:10.1088/1741-2552/ABC123"):
                return httpx2.Response(200, json=S2_SEARCH["data"][1])
            if requested == "DOI:10.9999/WRONG":  # an API answering with a different record
                return httpx2.Response(200, json=S2_SEARCH["data"][0])
            return httpx2.Response(404, json={"error": "not found"})
    if host == "api.openalex.org":
        return httpx2.Response(200, json=OPENALEX)
    if host == "api.crossref.org":
        if path.startswith("/works/"):  # single work lookup
            if "abc123" in path.lower():
                return httpx2.Response(200, json={"message": CROSSREF["message"]["items"][0]})
            return httpx2.Response(404, text="Resource not found.")
        return httpx2.Response(200, json=CROSSREF)
    if host == "eutils.ncbi.nlm.nih.gov":
        if "esearch" in path:
            return httpx2.Response(200, json=PUBMED_SEARCH)
        return httpx2.Response(200, text=PUBMED_XML)
    if host == "doi.org":
        if "abc123" in path.lower():
            return httpx2.Response(200, text=BIBTEX)
        return httpx2.Response(404, text="not found")
    if host == "arxiv.org":
        return httpx2.Response(200, content=make_pdf("Full text of AI Scientist"))
    if host == "api.dandiarchive.org":
        if path.endswith("/assets/"):
            return httpx2.Response(200, json={"results": [{"path": "sub-01/sub-01_ecephys.nwb", "size": 2048,
                                                           "asset_id": "a1"}]})
        if "/versions/" in path:
            return httpx2.Response(200, json={"name": "Hippocampus recordings", "license": ["spdx:CC-BY-4.0"],
                                              "description": "Neuropixels in mouse CA1",
                                              "assetsSummary": {"numberOfFiles": 12, "numberOfBytes": 10 ** 9}})
        return httpx2.Response(200, json={"results": [{"identifier": "000123", "draft_version": {
            "name": "Hippocampus recordings", "asset_count": 12, "size": 10 ** 9, "version": "draft"}}]})
    if host == "openneuro.org":
        body = json.loads(request.content)
        if "advancedSearch" in body["query"]:
            if "$keywords: [String!]" not in body["query"]:  # the real schema rejects [String] with HTTP 400
                return httpx2.Response(400, json={"errors": [{"message": 'Variable "$keywords" of type "[String]" '
                                                              'used in position expecting type "[String!]".'}]})
            return httpx2.Response(200, json={"data": {"advancedSearch": {"edges": [
                {"node": {"id": "ds000117", "latestSnapshot": {"tag": "1.0.5", "description": {"Name": "Faces MEG"}}}}]}}})
        if "Authors" in body["query"]:
            return httpx2.Response(400, json={"errors": [{"message": "Cannot query field"}]})
        return httpx2.Response(200, json={"data": {"dataset": {"id": "ds000117", "latestSnapshot": {
            "tag": "1.0.5", "description": {"Name": "Faces MEG"}}}}})
    if host == "neurostore.org":
        return httpx2.Response(200, json={"results": [{"id": "s1", "name": "Working memory fMRI", "year": 2019,
                                                       "doi": "10.1/wm"}]})
    if host == "api.brain-map.org":
        return httpx2.Response(200, json={"success": True, "msg": [{"acronym": "VISp", "name": "Primary visual area"}]})
    return httpx2.Response(404)


@pytest.fixture
def ctx(workspace):
    return ToolContext(workspace=workspace, services={"http": HttpClient(transport=httpx2.MockTransport(router)),
                                                      "lit_config": LitConfig(openalex_api_key="k",
                                                                              contact_email="me@lab.org")})


async def call(tool, ctx, **kw):
    return await tool.run(tool.parse_args(kw), ctx)


def test_ids_queries_and_merge():
    assert parse_id("10.1038/nature12373") == ("doi", "10.1038/nature12373")
    assert parse_id("https://arxiv.org/abs/2504.08066v2") == ("arxiv", "2504.08066")
    assert parse_id("pmid:123456") == ("pmid", "123456")
    assert parse_id("W2741809807") == ("openalex", "W2741809807")
    assert arxiv_query("tree search agents", 2024) .startswith("(all:tree AND all:search AND all:agents) AND submittedDate")
    assert reconstruct_abstract({"b": [1], "a": [0]}) == "a b"
    a = Paper(title="Same Paper Title Here Long", doi="https://doi.org/10.1/X", sources=["s2"])
    b = Paper(title="Same paper title here long", doi="10.1/x", year=2020, sources=["crossref"])
    merged = merge_papers([[a], [b]])
    assert len(merged) == 1 and merged[0].year == 2020 and merged[0].sources == ["s2", "crossref"]


async def test_search_papers_merges_sources_and_tracks_cost(ctx, workspace):
    result = await call(search_papers, ctx, query="EEG decoding deep learning", limit=5)
    text = result.as_text()
    assert result.meta["cost_usd"] == pytest.approx(0.001)
    assert text.startswith("共 3 篇")  # AI Scientist (arxiv+s2), EEG paper (s2+openalex+crossref), PubMed paper
    assert "doi:10.1088/1741-2552/abc123" in text and "arxiv:2504.08066" in text and "pmid:39000002" in text
    lines = (workspace / "lit" / "papers.jsonl").read_text().splitlines()
    assert len(lines) == 3
    await call(search_papers, ctx, query="EEG decoding deep learning", limit=5)
    assert len((workspace / "lit" / "papers.jsonl").read_text().splitlines()) == 3  # no duplicates appended


async def test_get_paper_graph_and_fulltext(ctx, workspace):
    text = (await call(get_paper, ctx, id="arxiv:2504.08066")).as_text()
    assert "Tree search for science." in text and "agentic tree search" in text
    graph = (await call(paper_graph, ctx, id="arxiv:2504.08066", relation="references")).as_text()
    assert "Deep learning for EEG decoding" in graph
    full = (await call(fetch_fulltext, ctx, id="arxiv:2504.08066")).as_text()
    assert "Full text of AI Scientist" in full and list((workspace / "lit" / "text").glob("*.txt"))


async def test_add_reference_and_check_citations(ctx, workspace):
    out = (await call(add_reference, ctx, id="doi:10.1088/1741-2552/abc123")).as_text()
    assert "已添加 [@silva2020deep]" in out
    bib = (workspace / "references.bib").read_text()
    assert bib.startswith("@article{silva2020deep,") and "Silva_2020" not in bib
    again = (await call(add_reference, ctx, id="10.1088/1741-2552/ABC123")).as_text()
    assert again.startswith("已存在")
    # PubMed paper without DOI: BibTeX built from the retrieved record
    out = (await call(add_reference, ctx, id="pmid:39000002")).as_text()
    assert "zhang2024hippocampal" in out and "built from" in out
    log = (workspace / "lit" / "references.jsonl").read_text().splitlines()
    assert len(log) == 2
    # a record whose identifiers don't match the request is rejected, not silently cited
    with pytest.raises(ToolError, match="找不到这篇文献"):
        await call(add_reference, ctx, id="doi:10.9999/wrong")

    (workspace / "paper").mkdir()
    (workspace / "paper" / "p.md").write_text("As shown [@silva2020deep; @fake2021x] and \\cite{zhang2024hippocampal}.")
    report = (await call(check_citations, ctx, path="paper/p.md")).as_text()
    assert "fake2021x" in report and "❌" in report
    assert find_citations("see [-@a; @b, p. 3]") == ["a", "b"]
    assert check_citations_text("[@a]", "@article{a, title={x}}")["unknown"] == []


async def test_findings_ledger(ctx, workspace):
    (workspace / "results").mkdir()
    (workspace / "results" / "main.json").write_text(json.dumps({"acc": {"mean": 0.873, "std": 0.02}}))
    (workspace / "references.bib").write_text("@article{silva2020deep, title={x}}\n")
    ok = (await call(record_finding, ctx, statement="Model reaches 87% accuracy",
                     evidence=["results/main.json#/acc/mean", "@silva2020deep"])).as_text()
    assert "F001" in ok and "✅" in ok
    bad = (await call(record_finding, ctx, statement="Unsupported", evidence=["results/main.json#/nope"])).as_text()
    assert "F002" in bad and "无法核验" in bad
    with pytest.raises(ToolError):
        await call(record_finding, ctx, statement="no evidence", evidence=[])
    listed = (await call(query_findings, ctx, only_verified=True)).as_text()
    assert "F001" in listed and "F002" not in listed


async def test_render_document_and_lint(ctx, workspace):
    (workspace / "results").mkdir()
    (workspace / "results" / "main.json").write_text(json.dumps({"acc": {"mean": 0.87321}, "n": 42}))
    (workspace / "paper").mkdir()
    (workspace / "paper" / "paper.src.md").write_text(
        "# Methods\nWe used 64 channels.\n\n## Results\nAccuracy was {{results/main.json#/acc/mean:.3f}} "
        "(n = {{results/main.json#/n}}), higher than 0.61 reported before [@x2020y]. "
        "Missing {{results/other.json#/a}}.\n")
    out = (await call(render_document, ctx)).as_text()
    rendered = (workspace / "paper" / "paper.md").read_text()
    assert "Accuracy was 0.873 (n = 42)" in rendered and "[[缺失:" in rendered
    assert "无法解析" in out and "0.61" in out and "64" not in out.split("⚠")[-1]
    assert lint_bare_numbers("## Results\nvalue {{r.json#/a}} and [@k2020a]\n") == []


async def test_neuro_data_tools(ctx):
    text = (await call(search_neuro_datasets, ctx, query="hippocampus", modality="meg")).as_text()
    assert "DANDI:000123" in text and "OpenNeuro:ds000117" in text and "NeuroStore:s1" in text
    details = (await call(get_dandiset, ctx, id="DANDI:000123")).as_text()
    assert "Hippocampus recordings" in details and "sub-01_ecephys.nwb" in details and "CC-BY-4.0" in details
    ds = (await call(get_openneuro_dataset, ctx, id="ds000117")).as_text()
    assert "Faces MEG" in ds and "openneuro download" in ds
    allen = (await call(allen_query, ctx, criteria="model::Structure,rma::criteria,[acronym$eq'VISp']")).as_text()
    assert "Primary visual area" in allen


async def test_reference_key_uses_the_registered_year_not_the_preprint(workspace, monkeypatch):
    from agent4sci.tools import bib
    from agent4sci.tools.literature.models import Paper

    async def preprint_record(ctx, ident):
        return Paper(title="Optogenetic frequency scrambling of hippocampal theta oscillations",
                     authors=["Guillaume Etter"], year=2021, doi="10.1038/s41467-023-35825-5", sources=["s2"])

    monkeypatch.setattr(bib, "resolve_paper", preprint_record)
    registered = ("@article{Etter_2023, title={Optogenetic frequency scrambling of hippocampal theta oscillations}, "
                  "author={Etter, Guillaume}, year={2023}, DOI={10.1038/s41467-023-35825-5}}")
    http = HttpClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(200, text=registered)))
    ctx = ToolContext(workspace=workspace, services={"http": http})
    out = (await call(add_reference, ctx, id="doi:10.1038/s41467-023-35825-5")).as_text()
    assert "[@etter2023optogenetic]" in out and "(2023)" in out
    assert (workspace / "references.bib").read_text().startswith("@article{etter2023optogenetic,")


async def test_fulltext_follows_landing_pages(workspace, monkeypatch):
    from agent4sci.tools.literature import tools as lit
    from agent4sci.tools.literature.models import Paper

    pages = {
        "/a/1": '<html><head><meta name="citation_pdf_url" content="/a/1.pdf"></head><body>Abstract only.</body></html>',
        "/a/2": "<html><body><article><h1>Results</h1>" + "<p>Theta power rose with load. </p>" * 400 + "</article></body></html>",
        "/a/3": "<html><body><p>Abstract only, subscribe to read.</p></body></html>",
    }

    def handler(request):
        if request.url.path == "/a/1.pdf":
            return httpx2.Response(200, content=make_pdf("Full paper text"), headers={"content-type": "application/pdf"})
        return httpx2.Response(200, text=pages[request.url.path], headers={"content-type": "text/html"})

    async def paper_for(ctx, ident):
        return Paper(title="T", doi=f"10.1/{ident[-1]}", pdf_url=f"https://journal.example/a/{ident[-1]}", sources=["x"])

    monkeypatch.setattr(lit, "resolve_paper", paper_for)
    ctx = ToolContext(workspace=workspace, services={"http": HttpClient(transport=httpx2.MockTransport(handler))})
    out = (await call(fetch_fulltext, ctx, id="doi:10.1/1")).as_text()
    assert "PDF 1 页" in out and "Full paper text" in out
    out = (await call(fetch_fulltext, ctx, id="doi:10.1/2")).as_text()
    assert "网页全文" in out and "Theta power rose" in out
    with pytest.raises(ToolError, match="只有摘要页"):
        await call(fetch_fulltext, ctx, id="doi:10.1/3")


async def test_arxiv_falls_back_to_stdlib_on_406_and_relaxes_empty_and_queries(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from agent4sci.tools.literature import sources as S

    empty = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    seen: list[str] = []

    def stdlib(url, user_agent):
        query = parse_qs(urlsplit(url).query)["search_query"][0]
        seen.append(query)
        return ARXIV_XML if " AND (" in query else empty  # only the relaxed query finds papers

    monkeypatch.setattr(S, "_urllib_get", stdlib)
    from agent4sci.tools.http import HostPolicy

    http = HttpClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(406)),
                      policies={"export.arxiv.org": HostPolicy(0, 1)})  # no 3-s spacing in tests
    papers = await S.arxiv_search(http, "hippocampal theta working memory", limit=3)
    assert papers and papers[0].arxiv_id
    assert seen[0].count(" AND ") == 3 and seen[1] == "all:hippocampal AND (all:theta OR all:working OR all:memory)"


def test_system_proxy_is_adopted_only_without_proxy_env(monkeypatch):
    import urllib.request

    from agent4sci.core import envfix

    for name in envfix._PROXY_VARS + ("NO_PROXY", "A4S_NO_SYSTEM_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:7897",
                                                               "https": "http://127.0.0.1:7897",
                                                               "socks": "socks5://127.0.0.1:7897"})
    assert envfix.apply_system_proxy() == {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
    import os

    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7897" and "localhost" in os.environ["NO_PROXY"]
    monkeypatch.setenv("HTTPS_PROXY", "http://corp:8080")
    assert envfix.apply_system_proxy() == {}  # an explicit proxy wins
