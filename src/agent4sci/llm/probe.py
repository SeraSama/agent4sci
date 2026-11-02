"""Connection test for a model profile: one plain request plus a tool-calling probe."""

from __future__ import annotations

import time
from typing import Any

from ..core.models import ModelProfile
from . import create_provider
from .base import LLMError
from .types import ToolSpec, user_text

_PROBE_TOOL = ToolSpec(
    name="get_utc_time",
    description="Return the current UTC time. Always use this tool when asked for the time.",
    input_schema={"type": "object", "properties": {"format": {"type": "string", "description": "e.g. iso"}},
                  "required": ["format"]},
)


async def test_connection(profile: ModelProfile, api_key: str | None, *, check_tools: bool = True) -> dict[str, Any]:
    provider = create_provider(profile, api_key)
    report: dict[str, Any] = {"ok": False, "model": profile.model}
    try:
        started = time.monotonic()
        response = await provider.generate(
            system="You are a connection test. Follow instructions exactly.",
            messages=[user_text("Reply with exactly: OK")],
            tools=[],
            max_tokens=2048,
        )
        report.update(ok=True, latency_s=round(time.monotonic() - started, 2), served_model=response.model,
                      reply=response.message.text()[:200], stop=response.stop,
                      usage=response.usage.model_dump())
        model_caps = await provider.capabilities() if hasattr(provider, "capabilities") else None
        if model_caps:
            report["model_capabilities"] = sorted(model_caps)
            report["context"] = profile.context_window
        if model_caps and "tools" not in model_caps:
            report["tools_ok"] = False
            report["tools_note"] = "该模型不支持工具调用（Ollama /api/show），只能对话，不能使用工具"
        elif check_tools and profile.capabilities.tools:
            probe = await provider.generate(
                system="You are a connection test. Use the provided tool.",
                messages=[user_text("What is the current UTC time? Call the tool.")],
                tools=[_PROBE_TOOL],
                max_tokens=2048,
            )
            report["tools_ok"] = any(c.name == _PROBE_TOOL.name for c in probe.message.tool_calls())
    except LLMError as exc:
        report.update(ok=False, error=str(exc), kind=exc.kind, status=exc.status)
    except Exception as exc:  # misconfigured URL etc.
        report.update(ok=False, error=f"{type(exc).__name__}: {exc}", kind="other")
    finally:
        await provider.aclose()
    return report
