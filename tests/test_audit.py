"""The user-triggered project self-check."""

from __future__ import annotations

import json

from agent4sci.core.audit import audit_project, default_audit_config, latest_report
from agent4sci.tools.provenance import lint_bare_numbers


def write(workspace, rel: str, text: str) -> None:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def finding(fid: str, evidence: list[str], verified: bool = True, statement: str = "一条结论") -> str:
    return json.dumps({"id": fid, "statement": statement, "evidence": evidence, "kind": "result",
                       "confidence": "high", "verified": verified, "checks": []}, ensure_ascii=False)


def test_default_scope_skips_scratch_files_and_the_reviewers_own_document(workspace):
    write(workspace, "paper/paper.md", "# Paper\n")
    write(workspace, "paper/paper.src.md", "# Paper\n")
    write(workspace, "paper/_draft.md", "# scratch\n")
    write(workspace, "lit/review.md", "# Review\n")
    write(workspace, "review/review.md", "# 评审意见\n")
    write(workspace, "results/main.json", "{}")
    write(workspace, "results/_tmp.json", "{}")

    cfg = default_audit_config(workspace)
    assert cfg["citations"] == ["paper/paper.md", "lit/review.md"]  # review/ critiques the paper, it is not one
    assert "paper/_draft.md" not in cfg["citations"] and "results/_tmp.json" not in cfg["json_valid"]
    assert cfg["placeholders"] == ["paper/paper.src.md"] and cfg["json_valid"] == ["results/main.json"]


async def test_evidence_that_rotted_since_it_was_recorded_is_an_error(workspace):
    """The case this feature exists for: a result file changed and nobody re-checked the claims."""
    write(workspace, "results/coding.json", json.dumps({"studies": [{"tier": 1}, {"tier": 2}]}))
    write(workspace, "references.bib", "@article{smith2020x,\n  title = {X},\n}\n")
    write(workspace, "findings.jsonl", "\n".join([
        finding("F001", ["results/coding.json#/studies/0/tier"]),           # still resolves
        finding("F002", ["results/coding.json#/studies/23/tier"]),          # the table shrank: gone
        finding("F003", ["@smith2020x"]),                                   # bib key, still there
        finding("F004", ["lit/notes/never-written.md"], verified=False),    # cited a file that was never created
        finding("F005", ["results/coding.json"]),                           # weak: only proves the file exists
    ]) + "\n")

    report = audit_project(workspace, save=False)
    assert report["passed"] is False
    assert any("F002" in e["message"] for e in report["errors"])
    assert not any("F001" in e["message"] or "F003" in e["message"] for e in report["errors"])
    assert any("F004" in w["message"] for w in report["warnings"])
    assert report["findings"]["total"] == 5
    assert [i["id"] for i in report["findings"]["drifted"]] == ["F002"]
    assert [i["id"] for i in report["findings"]["unverifiable"]] == ["F004"]
    assert [i["id"] for i in report["findings"]["weak"]] == ["F005"]
    assert report["errors"][0]["file"] == "results/coding.json"  # the UI can open it


async def test_unrendered_numbers_and_unknown_citation_keys(workspace):
    write(workspace, "references.bib", "@article{known2020,\n  title = {K},\n}\n")
    write(workspace, "paper/paper.src.md", "# Results\n准确率 {{results/main.json#/acc:.2f}}，见 [@known2020]。\n")
    write(workspace, "paper/paper.md", "# Results\n准确率 [[缺失: results/main.json#/acc]]，见 [@missing2020]。\n")
    write(workspace, "results/main.json", "{}")

    report = audit_project(workspace, save=False)
    messages = " ".join(e["message"] for e in report["errors"])
    assert "不存在的文献键" in messages and "missing2020" in messages
    assert "没有渲染出来" in messages
    assert any("占位符无法解析" in e["message"] for e in report["errors"])


async def test_the_report_is_saved_and_can_be_read_back(workspace):
    write(workspace, "paper/paper.md", "# Paper\n没有引用。\n")
    assert latest_report(workspace) is None
    report = audit_project(workspace)
    assert report["path"].endswith(".json")
    again = latest_report(workspace)
    assert again is not None and again["ts"] == report["ts"]


def test_lint_only_stops_at_a_sibling_heading_and_skips_code():
    text = (
        "## Results\n"
        "准确率是 0.873。\n"
        "### 表 1\n"
        "召回率是 0.902。\n"          # a sub-heading used to silently disable the lint from here on
        "```python\n"
        "x = 3.14159\n"              # code is not a claim
        "```\n"
        "## Discussion\n"
        "我们在 2019 年提出过 0.5 的阈值。\n"  # outside Results: not checked by default
    )
    issues = lint_bare_numbers(text)
    assert len(issues) == 2 and "0.873" in issues[0] and "0.902" in issues[1]
    assert not any("3.14159" in i for i in issues)
    assert any("0.5" in i for i in lint_bare_numbers(text, everywhere=True))
