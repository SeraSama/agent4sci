"""Safe expressions and ``{{ … }}`` templates over the run state (via simpleeval)."""

from __future__ import annotations

import json
import re
import statistics
from typing import Any

from simpleeval import EvalWithCompoundTypes

_TEMPLATE = re.compile(r"\{\{\s*(.+?)\s*\}\}", re.S)


class AttrDict(dict):
    """dict with attribute access so expressions can say ``nodes.review.output.decision``."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return wrap(self[name])
        except KeyError:
            return None

    def __getitem__(self, key: Any) -> Any:
        return wrap(dict.__getitem__(self, key))

    def get(self, key: Any, default: Any = None) -> Any:  # type: ignore[override]
        return wrap(dict.get(self, key, default))


def wrap(value: Any) -> Any:
    if isinstance(value, AttrDict):
        return value
    if isinstance(value, dict):
        return AttrDict(value)
    if isinstance(value, list):
        return [wrap(v) for v in value]
    return value


def _last(values: Any, default: Any = None) -> Any:
    return values[-1] if values else default


def _best(values: Any, goal: str = "max") -> Any:
    if not values:
        return None
    return max(values) if goal == "max" else min(values)


def _mean(values: Any) -> float | None:
    vals = [v for v in values or [] if isinstance(v, (int, float))]
    return statistics.fmean(vals) if vals else None


FUNCTIONS = {
    "len": len, "min": min, "max": max, "sum": sum, "any": any, "all": all, "abs": abs, "round": round,
    "sorted": sorted, "float": float, "int": int, "str": str, "bool": bool, "list": list,
    "range": lambda *a: list(range(*a))[:1000],
    "last": _last, "best": _best, "mean": _mean,
    "contains": lambda seq, x: x in (seq or []),
    "lower": lambda s: str(s).lower(),
    "json": lambda v: json.dumps(v, ensure_ascii=False, default=str),
}


class ExpressionError(Exception):
    pass


def evaluate(expression: str, names: dict[str, Any]) -> Any:
    evaluator = EvalWithCompoundTypes(names={k: wrap(v) for k, v in names.items()}, functions=FUNCTIONS)
    evaluator.names.update({"true": True, "false": False, "null": None, "True": True, "False": False, "None": None})
    try:
        return evaluator.eval(expression)
    except Exception as exc:  # simpleeval raises many specific errors
        raise ExpressionError(f"表达式错误 `{expression}`: {exc}") from exc


def truthy(expression: str | None, names: dict[str, Any]) -> bool:
    if not expression or not expression.strip():
        return False
    return bool(evaluate(expression, names))


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value)


def render_template(text: str, names: dict[str, Any]) -> str:
    """Replace ``{{ expr }}``; anything that is not a valid expression is left untouched
    (e.g. result-provenance placeholders such as ``{{results/main.json#/acc}}``)."""

    def replace(match: re.Match[str]) -> str:
        try:
            return _to_text(evaluate(match.group(1), names))
        except ExpressionError:
            return match.group(0)

    return _TEMPLATE.sub(replace, text or "")
