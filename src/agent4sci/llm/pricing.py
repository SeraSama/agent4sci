"""Token pricing (USD per million tokens) and cost calculation."""

from __future__ import annotations

from ..core.models import ModelProfile, Pricing
from .types import Usage


def _claude(inp: float, out: float, cache_read: float | None = None) -> Pricing:
    return Pricing(input=inp, output=out, cache_read=cache_read if cache_read is not None else inp * 0.1,
                   cache_write=inp * 1.25)


# Claude API first-party list prices.
BUILTIN_PRICING: dict[str, Pricing] = {
    "claude-fable-5-1": _claude(10, 50, 0.25),
    "claude-fable-5": _claude(10, 50),
    "claude-opus-5": _claude(5, 25),
    "claude-opus-4-8": _claude(5, 25),
    "claude-opus-4-7": _claude(5, 25),
    "claude-opus-4-6": _claude(5, 25),
    "claude-sonnet-5": _claude(2, 10),
    "claude-sonnet-4-6": _claude(3, 15),
    "claude-haiku-4-5": _claude(1, 5),
}


def lookup_pricing(model: str) -> Pricing | None:
    model = model.lower()
    if model in BUILTIN_PRICING:
        return BUILTIN_PRICING[model]
    # Match dated or prefixed variants such as "anthropic/claude-opus-5".
    for name in sorted(BUILTIN_PRICING, key=len, reverse=True):
        if name in model:
            return BUILTIN_PRICING[name]
    return None


def resolve_pricing(profile: ModelProfile, served_model: str | None = None) -> Pricing | None:
    """The profile's prices, else the built-in table; local endpoints (Ollama, vLLM on this machine) are free.
    None means unknown: costs undercount and budgets cannot be enforced."""
    if profile.pricing is not None:
        return profile.pricing
    builtin = lookup_pricing(served_model or profile.model)
    if builtin is not None:
        return builtin
    if profile.is_local:
        return Pricing(input=0, output=0)
    return None


def compute_cost(pricing: Pricing | None, usage: Usage) -> float | None:
    if pricing is None:
        return None
    cache_read = pricing.cache_read if pricing.cache_read is not None else pricing.input
    cache_write = pricing.cache_write if pricing.cache_write is not None else pricing.input
    total = (
        usage.input_tokens * pricing.input
        + usage.output_tokens * pricing.output
        + usage.cache_read_tokens * cache_read
        + usage.cache_write_tokens * cache_write
    )
    return total / 1_000_000
