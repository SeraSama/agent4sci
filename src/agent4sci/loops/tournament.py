"""Idea tournament (simplified AI co-scientist): generate → novelty check → score → pairwise Elo → rank."""

from __future__ import annotations

import itertools
import random
import re
from typing import Any, Awaitable, Callable

CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "rationale": {"type": "string", "description": "Which gap in the literature this addresses"},
                    "test": {"type": "string", "description": "Concrete test: data, method, metric, refuting outcome"},
                },
                "required": ["title", "hypothesis", "rationale", "test"],
            },
        }
    },
    "required": ["candidates"],
}

NOVELTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "closest_works": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "title": {"type": "string"}, "overlap": {"type": "string"}}}},
        "novelty": {"type": "number", "minimum": 0, "maximum": 10},
        "feasibility": {"type": "number", "minimum": 0, "maximum": 10},
        "impact": {"type": "number", "minimum": 0, "maximum": 10},
        "comment": {"type": "string"},
    },
    "required": ["novelty", "feasibility", "impact", "comment"],
}

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"winner": {"enum": ["A", "B"]}, "reason": {"type": "string"}},
    "required": ["winner", "reason"],
}

WEIGHTS = {"novelty": 0.4, "feasibility": 0.35, "impact": 0.25}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^\w一-鿿]+", text.lower()) if len(t) > 2}


def dedupe(candidates: list[dict[str, Any]], threshold: float = 0.6) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for c in candidates:
        tokens = _tokens(c.get("title", "") + " " + c.get("hypothesis", ""))
        duplicate = False
        for k in kept:
            other = _tokens(k.get("title", "") + " " + k.get("hypothesis", ""))
            union = tokens | other
            if union and len(tokens & other) / len(union) >= threshold:
                duplicate = True
                k.setdefault("merged", []).append(c.get("title"))
                break
        if not duplicate:
            kept.append(dict(c))
    return kept


def weighted_score(scores: dict[str, Any]) -> float:
    total = 0.0
    for key, weight in WEIGHTS.items():
        try:
            total += weight * float(scores.get(key, 0))
        except (TypeError, ValueError):
            pass
    return round(total, 3)


def elo_update(ra: float, rb: float, a_wins: bool, k: float = 32.0) -> tuple[float, float]:
    expected_a = 1 / (1 + 10 ** ((rb - ra) / 400))
    score_a = 1.0 if a_wins else 0.0
    return ra + k * (score_a - expected_a), rb + k * ((1 - score_a) - (1 - expected_a))


Judge = Callable[[dict[str, Any], dict[str, Any]], Awaitable[str]]


async def run_elo(candidates: list[dict[str, Any]], judge: Judge, seed: int = 0) -> list[dict[str, Any]]:
    """Round-robin pairwise comparisons; A/B order randomised to reduce position bias."""
    rng = random.Random(seed)
    ratings = {i: 1200.0 for i in range(len(candidates))}
    wins = {i: 0 for i in range(len(candidates))}
    pairs = list(itertools.combinations(range(len(candidates)), 2))
    rng.shuffle(pairs)
    for i, j in pairs:
        first, second = (i, j) if rng.random() < 0.5 else (j, i)
        verdict = await judge(candidates[first], candidates[second])
        winner = first if verdict == "A" else second
        loser = second if winner == first else first
        ratings[winner], ratings[loser] = elo_update(ratings[winner], ratings[loser], True)
        wins[winner] += 1
    ranked = []
    for idx, cand in enumerate(candidates):
        ranked.append({**cand, "elo": round(ratings[idx], 1), "wins": wins[idx]})
    ranked.sort(key=lambda c: (-c["elo"], -c.get("score", 0)))
    return ranked


def to_markdown(ranked: list[dict[str, Any]], top_k: int) -> str:
    lines = ["# 候选假设（锦标赛排序）", ""]
    for rank, c in enumerate(ranked, 1):
        star = " ⭐" if rank <= top_k else ""
        lines.append(f"## {rank}. {c.get('title')}{star}")
        lines.append(f"- Elo：{c.get('elo')}（胜 {c.get('wins', 0)} 场）；综合分：{c.get('score', '—')}")
        scores = c.get("scores") or {}
        if scores:
            lines.append(f"- 新颖性 {scores.get('novelty')} / 可行性 {scores.get('feasibility')} / 影响 {scores.get('impact')}")
        lines.append(f"- 假设：{c.get('hypothesis')}")
        lines.append(f"- 理由：{c.get('rationale')}")
        lines.append(f"- 检验方法：{c.get('test')}")
        works = (scores.get("closest_works") if scores else None) or []
        if works:
            lines.append("- 最相近的已有工作：" + "；".join(f"{w.get('title')}（{w.get('id', '')}）" for w in works[:3]))
        if scores.get("comment"):
            lines.append(f"- 评述：{scores['comment']}")
        if c.get("merged"):
            lines.append(f"- 合并的相似想法：{', '.join(c['merged'])}")
        lines.append("")
    return "\n".join(lines)
