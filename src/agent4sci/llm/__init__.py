"""LLM adapters. Use :func:`create_provider` to get the adapter for a profile."""

from __future__ import annotations

from ..core.models import ModelProfile
from .base import LLMError, LLMProvider
from .types import (
    ImageBlock,
    LLMResponse,
    Message,
    ReasoningBlock,
    StreamEvent,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
    Usage,
    user_text,
)


def create_provider(profile: ModelProfile, api_key: str | None) -> LLMProvider:
    if profile.provider == "fake":
        from .fake_provider import FakeProvider, demo_brain

        return FakeProvider(profile, brain=demo_brain, delay=0.015)
    if profile.provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(profile, api_key)
    if profile.provider == "ollama":
        from .ollama_provider import OllamaProvider

        return OllamaProvider(profile, api_key)
    if profile.provider in ("openai", "openai-compatible"):
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(profile, api_key)
    raise ValueError(f"unknown provider: {profile.provider}")


__all__ = [
    "ImageBlock",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "ReasoningBlock",
    "StreamEvent",
    "TextBlock",
    "ToolCallBlock",
    "ToolResultBlock",
    "ToolSpec",
    "Usage",
    "create_provider",
    "user_text",
]
