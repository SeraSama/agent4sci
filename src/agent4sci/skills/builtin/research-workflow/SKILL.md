---
name: research-workflow
description: How to run a research task in an Agent4Sci workspace — workspace layout, the findings ledger, citations, result provenance and hand-offs between stages. Use at the start of any research, analysis or writing task (研究流程、工作区约定、发现账本、结果溯源).
license: MIT
metadata:
  origin: builtin
---

# Research workflow in Agent4Sci

## Workspace layout
| Folder | Contents |
|---|---|
| `plan/` | brief, hypotheses, experiment plan (the plan is frozen once approved) |
| `lit/` | `review.md`, `papers.jsonl`, full texts in `lit/text/` |
| `references.bib` | only references added with `add_reference` (verified records) |
| `data/` | raw and derived data (never edit raw data in place) |
| `code/` | analysis/experiment code; evaluation code in `code/eval/` |
| `results/` | machine-readable outputs (`*.json`, `*.csv`) written by code |
| `figures/` | figures; plots from `run_python` land in `figures/auto/` |
| `paper/` | `paper.src.md` (with result placeholders) → rendered `paper.md` |
| `notes/` | working notes |

## Working loop
1. Read the brief and prior stage summaries before acting. Check memory indexes for relevant notes.
2. Plan small, verifiable steps. After each step, check the output (shape, units, counts, sanity values).
3. Record every finding worth keeping with `record_finding`, attaching evidence: a bib key, a results file with a JSON pointer (`results/main.json#/acc/mean`), or a code path.
4. Save numbers to `results/*.json` from code — never type numbers into reports by hand.
5. When writing, cite as `[@key]` using keys from `references.bib`, and reference numbers with placeholders: `{{results/main.json#/acc/mean:.3f}}`. Run `check_citations` before finishing.
6. Finish a stage by summarising what was done, what was found, what failed, and which files were produced.

## Honesty rules
- Report negative results and failed attempts; they are findings too.
- Separate what the data show from interpretation and speculation.
- If something could not be done (missing data, no access, compute limits), say so explicitly.
