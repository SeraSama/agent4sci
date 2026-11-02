"""Model registry: user-defined model profiles, each with its own endpoint and key.

A profile says *which* provider adapter to use (``anthropic``, ``openai`` or any
``openai-compatible`` endpoint), the model name, base URL, parameters, pricing
and capability flags. API keys are never stored in the database: they live in
the secret store under ``model:<profile id>`` or are read from an env var.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from .db import Database
from .secrets import SecretStore, get_secret_store, mask

ProviderKind = Literal["anthropic", "openai", "openai-compatible", "ollama", "fake"]


class Capabilities(BaseModel):
    tools: bool = True
    parallel_tools: bool = True
    vision: bool = True
    thinking: bool = False  # Anthropic adaptive thinking
    tool_result_images: bool = False  # images allowed inside tool results
    native_web_search: bool = False  # Anthropic server-side web_search / web_fetch
    native_memory_tool: bool = False  # Anthropic memory_20250818 tool type
    stream_usage: bool = True  # OpenAI stream_options.include_usage
    echo_reasoning_content: bool = False  # DeepSeek-style reasoning_content round trip


class Pricing(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None


class ModelProfile(BaseModel):
    id: str = Field(default_factory=lambda: "m_" + uuid.uuid4().hex[:10])
    name: str
    provider: ProviderKind
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    has_key: bool = False
    effort: str | None = None  # Anthropic output_config.effort
    reasoning_effort: str | None = None  # OpenAI reasoning_effort
    max_tokens: int | None = None
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_completion_tokens"
    temperature: float | None = None
    context_window: int = 200_000
    pricing: Pricing | None = None
    capabilities: Capabilities = Field(default_factory=Capabilities)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 600.0
    enabled: bool = True
    preset: str | None = None
    fallbacks: bool = True  # Anthropic server-side refusal fallbacks where supported

    @property
    def is_local(self) -> bool:
        url = (self.base_url or "").lower()
        return "localhost" in url or "127.0.0.1" in url


class Preset(BaseModel):
    id: str
    label: str
    provider: ProviderKind
    base_url: str | None = None
    model: str = ""
    model_hint: str = ""
    api_key_env: str | None = None
    needs_key: bool = True
    context_window: int = 128_000
    capabilities: Capabilities = Field(default_factory=Capabilities)
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    effort: str | None = None
    docs: str = ""
    pricing_url: str = ""  # where this provider publishes its prices, so cost tracking can be filled in


def _compat(**kw: Any) -> Capabilities:
    base = dict(vision=False, tool_result_images=False)
    base.update(kw)
    return Capabilities(**base)


PRESETS: dict[str, Preset] = {
    p.id: p
    for p in [
        Preset(
            id="anthropic", label="Anthropic Claude", provider="anthropic", model="claude-opus-5",
            model_hint="claude-opus-5 / claude-sonnet-5 / claude-haiku-4-5", api_key_env="ANTHROPIC_API_KEY",
            context_window=1_000_000, effort="high",
            capabilities=Capabilities(thinking=True, tool_result_images=True, native_web_search=True,
                                      native_memory_tool=True),
            docs="https://platform.claude.com/docs",
            pricing_url="https://www.anthropic.com/pricing",
        ),
        Preset(
            id="openai", label="OpenAI", provider="openai", model_hint="填写你要用的 OpenAI 模型名",
            api_key_env="OPENAI_API_KEY", max_tokens_param="max_completion_tokens",
            capabilities=Capabilities(vision=True), docs="https://platform.openai.com/docs",
            pricing_url="https://openai.com/api/pricing/",
        ),
        Preset(
            id="deepseek", label="DeepSeek", provider="openai-compatible", base_url="https://api.deepseek.com",
            model="deepseek-chat", model_hint="deepseek-chat / deepseek-reasoner", api_key_env="DEEPSEEK_API_KEY",
            capabilities=_compat(echo_reasoning_content=True), docs="https://api-docs.deepseek.com",
            pricing_url="https://api-docs.deepseek.com/quick_start/pricing",
        ),
        Preset(
            id="dashscope", label="通义千问 (DashScope)", provider="openai-compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model_hint="如 qwen-plus",
            api_key_env="DASHSCOPE_API_KEY", capabilities=_compat(),
            pricing_url="https://help.aliyun.com/zh/model-studio/models",
        ),
        Preset(
            id="moonshot", label="Kimi (Moonshot)", provider="openai-compatible",
            base_url="https://api.moonshot.cn/v1", model_hint="填写 Kimi 模型名", api_key_env="MOONSHOT_API_KEY",
            capabilities=_compat(), pricing_url="https://platform.moonshot.cn/docs/pricing/chat",
        ),
        Preset(
            id="zhipu", label="智谱 GLM", provider="openai-compatible",
            base_url="https://open.bigmodel.cn/api/paas/v4", model_hint="填写 GLM 模型名",
            api_key_env="ZHIPU_API_KEY", capabilities=_compat(),
            pricing_url="https://open.bigmodel.cn/pricing",
        ),
        Preset(
            id="siliconflow", label="SiliconFlow", provider="openai-compatible",
            base_url="https://api.siliconflow.cn/v1", model_hint="填写模型名", api_key_env="SILICONFLOW_API_KEY",
            capabilities=_compat(), pricing_url="https://siliconflow.cn/pricing",
        ),
        Preset(
            id="openrouter", label="OpenRouter", provider="openai-compatible",
            base_url="https://openrouter.ai/api/v1", model_hint="如 anthropic/claude-opus-5",
            api_key_env="OPENROUTER_API_KEY", capabilities=_compat(vision=True),
            pricing_url="https://openrouter.ai/models",
        ),
        Preset(
            id="gemini", label="Google Gemini (OpenAI 兼容)", provider="openai-compatible",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/", model_hint="填写 Gemini 模型名",
            api_key_env="GEMINI_API_KEY", capabilities=_compat(vision=True),
            pricing_url="https://ai.google.dev/pricing",
        ),
        Preset(
            id="ollama", label="Ollama (本地)", provider="ollama", base_url="http://localhost:11434",
            model_hint="需支持工具调用的模型，如 qwen3", needs_key=False, context_window=16_384,
            # tools / thinking / images are switched on per model from Ollama's /api/show
            capabilities=_compat(parallel_tools=False, vision=True, thinking=True, stream_usage=False),
            docs="原生接口：按“上下文长度”设置 num_ctx（OpenAI 兼容接口固定 4096，会截断系统提示）。"
                 "上下文越大越占内存，8 GB 内存的电脑建议 8192。",
        ),
        Preset(
            id="vllm", label="vLLM (本地)", provider="openai-compatible", base_url="http://localhost:8000/v1",
            model_hint="部署时的模型名", needs_key=False, capabilities=_compat(),
        ),
        Preset(
            id="custom", label="自定义 OpenAI 兼容端点", provider="openai-compatible", model_hint="模型名",
            capabilities=_compat(),
        ),
    ]
}

DEMO_PROFILE_ID = "demo"


def demo_mode() -> bool:
    return os.environ.get("A4S_FAKE_LLM", "") not in ("", "0", "false")


def demo_profile() -> ModelProfile:
    return ModelProfile(
        id=DEMO_PROFILE_ID, name="演示模型（离线）", provider="fake", model="demo",
        context_window=200_000, pricing=Pricing(input=0, output=0),
    )


def profile_from_preset(preset_id: str, **overrides: Any) -> ModelProfile:
    preset = PRESETS[preset_id]
    data: dict[str, Any] = dict(
        name=preset.label,
        provider=preset.provider,
        model=preset.model,
        base_url=preset.base_url,
        api_key_env=preset.api_key_env,
        context_window=preset.context_window,
        capabilities=preset.capabilities.model_copy(),
        max_tokens_param=preset.max_tokens_param,
        effort=preset.effort,
        preset=preset.id,
    )
    data.update(overrides)
    return ModelProfile(**data)


class ModelRegistry:
    """CRUD for model profiles plus key resolution and role → model mapping."""

    KIND = "model"

    def __init__(self, db: Database, secrets: SecretStore | None = None) -> None:
        self.db = db
        self.secrets = secrets or get_secret_store()

    # ---- profiles --------------------------------------------------------
    def list(self, include_demo: bool | None = None) -> list[ModelProfile]:
        profiles = [ModelProfile(**d) for d in self.db.list(self.KIND)]
        profiles.sort(key=lambda p: p.name.lower())
        if include_demo if include_demo is not None else demo_mode():
            profiles.insert(0, demo_profile())
        return profiles

    def get(self, profile_id: str) -> ModelProfile | None:
        if profile_id == DEMO_PROFILE_ID:
            return demo_profile()
        data = self.db.get(self.KIND, profile_id)
        return ModelProfile(**data) if data else None

    def save(self, profile: ModelProfile) -> ModelProfile:
        if profile.id == DEMO_PROFILE_ID:
            raise ValueError("the demo profile is built in and cannot be modified")
        profile.has_key = self.secrets.get(self._key_name(profile.id)) is not None
        self.db.put(self.KIND, profile.id, profile.model_dump(mode="json"))
        return profile

    def delete(self, profile_id: str) -> bool:
        self.secrets.delete(self._key_name(profile_id))
        return self.db.delete(self.KIND, profile_id)

    # ---- keys ------------------------------------------------------------
    @staticmethod
    def _key_name(profile_id: str) -> str:
        return f"model:{profile_id}"

    def set_api_key(self, profile_id: str, api_key: str | None) -> None:
        profile = self.get(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        if api_key:
            self.secrets.set(self._key_name(profile_id), api_key.strip())
        else:
            self.secrets.delete(self._key_name(profile_id))
        self.save(profile)

    def resolve_api_key(self, profile: ModelProfile) -> str | None:
        key = self.secrets.get(self._key_name(profile.id))
        if key:
            return key
        if profile.api_key_env and os.environ.get(profile.api_key_env):
            return os.environ[profile.api_key_env]
        preset = PRESETS.get(profile.preset or "")
        if profile.is_local or (preset is not None and not preset.needs_key):
            return "not-needed"
        return None

    def key_status(self, profile: ModelProfile) -> dict[str, Any]:
        stored = self.secrets.get(self._key_name(profile.id))
        env = profile.api_key_env and os.environ.get(profile.api_key_env)
        return {
            "stored": bool(stored),
            "masked": mask(stored) if stored else "",
            "env": profile.api_key_env if env else None,
            "resolved": self.resolve_api_key(profile) is not None,
        }

    # ---- defaults & roles --------------------------------------------------
    def default_id(self) -> str | None:
        wanted = self.db.get_setting("default_model")
        if wanted and self.get(wanted):
            return wanted
        profiles = [p for p in self.list() if p.enabled]
        return profiles[0].id if profiles else None

    def set_default(self, profile_id: str) -> None:
        self.db.set_setting("default_model", profile_id)

    def role_models(self) -> dict[str, str]:
        return dict(self.db.get_setting("role_models", {}) or {})

    def set_role_models(self, mapping: dict[str, str]) -> None:
        self.db.set_setting("role_models", mapping)

    def for_role(self, role: str | None) -> str | None:
        if role:
            mapped = self.role_models().get(role)
            if mapped and self.get(mapped):
                return mapped
        return self.default_id()

    def ensure_env_profiles(self) -> list[ModelProfile]:
        """Create profiles for providers whose API key is already in the environment."""
        created = []
        existing = {p.preset for p in self.list(include_demo=False)}
        for preset_id in ("anthropic", "openai", "deepseek"):
            preset = PRESETS[preset_id]
            if preset_id in existing or not preset.api_key_env or not os.environ.get(preset.api_key_env):
                continue
            if not preset.model:
                continue
            created.append(self.save(profile_from_preset(preset_id)))
        return created
