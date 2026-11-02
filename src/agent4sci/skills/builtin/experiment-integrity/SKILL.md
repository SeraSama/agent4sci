---
name: experiment-integrity
description: Checklist for trustworthy computational experiments and data analysis — pre-registered metrics, fixed splits and seeds, baselines and controls, logging results to files, statistics, compute limits. Use when designing or running experiments or statistical analyses (实验设计、可复现、统计检验、基线、防止 p-hacking).
license: MIT
metadata:
  origin: builtin
---

# Experiment integrity checklist

## Before running (design)
- Write down the hypothesis, primary metric, data splits, baselines, controls and the statistical test in `plan/experiment_plan.md` **before** looking at results. Once approved, do not change them; if a change is truly needed, document why in the plan and flag it.
- Estimate compute: this machine may be a laptop (check the Environment section). Start with a small pilot (subset of data, few epochs) to validate the pipeline end to end.
- Fix random seeds (`random`, `numpy`, `torch`) and record library versions (`pip freeze > results/env.txt` or `uv pip freeze`).

## While running
- Keep evaluation code in `code/eval/` separate from training/analysis code; never tune on the test set.
- Every run writes a JSON file to `results/` with: config, seed, metrics, timestamps, dataset version/hash.
- Use `job_start` for anything longer than a few minutes; poll with `job_status`.
- Stop and investigate when numbers look too good, too bad or identical across conditions — check for leakage, bugs and caching.

## Statistics
- Report effect sizes with confidence intervals, not only p-values. Use multiple seeds / bootstrap for variance.
- Correct for multiple comparisons when testing many hypotheses.
- Check assumptions (normality, independence) or use non-parametric tests.

## Reporting
- Include all pre-registered analyses, including null results; label exploratory analyses as exploratory.
- Every number in a report must trace to a results file (use `{{results/…#/…}}` placeholders).
