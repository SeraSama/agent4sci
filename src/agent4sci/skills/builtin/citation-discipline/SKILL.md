---
name: citation-discipline
description: How to find, verify and cite literature without hallucinated references — search, screen, add verified BibTeX, cite with [@key], check citations. Use whenever you search literature, write a review, or add references to a report (文献检索、引用、参考文献、防止幻觉引用).
license: MIT
metadata:
  origin: builtin
---

# Citation discipline

1. **Search** with `search_papers` (several sources; vary the query wording). Prefer primary sources and recent reviews. For a key paper, use `paper_graph` to walk its references/citations.
2. **Screen**: read titles/abstracts; record inclusion/exclusion decisions in `lit/screening.md` (with reasons) for systematic reviews.
3. **Read**: use `fetch_fulltext` for open-access papers and read the relevant sections; for many papers, `delegate` reading to sub-agents with a clear extraction template.
4. **Add** every paper you will cite with `add_reference(id=…)` using its DOI, arXiv ID or PMID. This fetches the real BibTeX record. Never write BibTeX by hand, never invent DOIs, authors, years or titles.
5. **Cite** in Markdown as `[@key]` using the exact key returned by `add_reference`.
6. **Check**: run `check_citations` on the document; fix every unknown key before finishing.

If a claim cannot be supported by a retrieved source, say "no source found" instead of citing something vaguely related.
