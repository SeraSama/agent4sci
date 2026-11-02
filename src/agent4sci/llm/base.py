from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Literal

from ..core.models import ModelProfile
from .types import LLMResponse, Message, StreamEvent, ToolSpec

StreamCallback = Callable[[StreamEvent], None]

ErrorKind = Literal["auth", "rate_limit", "bad_request", "not_found", "server", "connection", "timeout", "other"]


class LLMError(Exception):
    def __init__(self, message: str, kind: ErrorKind = "other", status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate_limit", "server", "connection", "timeout")


class LLMProvider(ABC):
    """One model endpoint. ``generate`` streams deltas and returns the full turn."""

    kind: str = "base"

    def __init__(self, profile: ModelProfile, api_key: str | None = None) -> None:
        self.profile = profile
        self.api_key = api_key

    @abstractmethod
    async def generate(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream: StreamCallback | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    async def aclose(self) -> None:
        return None

    def raw_key(self) -> dict[str, str | None]:
        """Identifies responses that may be replayed verbatim to this provider."""
        return {"provider": self.kind, "base_url": self.profile.base_url, "model": self.profile.model}

    def can_replay(self, message: Message) -> bool:
        raw = message.raw or {}
        return raw.get("provider") == self.kind and raw.get("base_url") == self.profile.base_url
