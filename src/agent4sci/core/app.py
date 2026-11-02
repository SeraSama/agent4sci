"""Process-wide services shared by the backend server and the CLI."""

from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path
from typing import Any

from ..agent.interactions import InteractionBroker
from ..tools.http import HttpClient
from ..tools.literature.sources import LitConfig
from .db import Database
from .events import EventBus
from .models import ModelRegistry
from .paths import cache_dir, db_path
from .projects import ProjectStore
from .secrets import get_secret_store

SERVICE_KEYS = {
    "s2": ("S2_API_KEY", "Semantic Scholar"),
    "openalex": ("OPENALEX_API_KEY", "OpenAlex"),
    "ncbi": ("NCBI_API_KEY", "NCBI / PubMed"),
    "tavily": ("TAVILY_API_KEY", "Tavily 网页搜索"),
    "brave": ("BRAVE_API_KEY", "Brave 网页搜索"),
    "github": ("GITHUB_TOKEN", "GitHub（提高技能下载限额）"),
}


class AppServices:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database(db_path())
        self.bus = EventBus(self.db)
        self.interactions = InteractionBroker(self.bus)
        self.models = ModelRegistry(self.db)
        self.projects = ProjectStore(self.db)
        self.secrets = get_secret_store()

    # ---- lazily created shared clients ------------------------------------------------
    @cached_property
    def http(self) -> HttpClient:
        return HttpClient(cache_dir=cache_dir() / "http", contact_email=self.contact_email())

    @cached_property
    def loops(self) -> Any:
        from ..loops.store import LoopStore

        return LoopStore(self.db)

    @cached_property
    def kernels(self) -> Any:
        from ..tools.python_exec import KernelPool

        return KernelPool()

    @cached_property
    def skill_hub(self) -> Any:
        from ..skills.hub import SkillHub

        return SkillHub(self.http)

    @cached_property
    def catalog(self) -> Any:
        from ..skills.catalog import load_catalog

        return load_catalog()

    @cached_property
    def skill_index(self) -> Any:
        from ..skills.catalog import CatalogIndex

        return CatalogIndex()

    @cached_property
    def mcp(self) -> Any:
        from ..mcp.manager import MCPManager

        return MCPManager()

    # ---- settings -------------------------------------------------------------------
    def contact_email(self) -> str | None:
        return self.db.get_setting("contact_email") or os.environ.get("A4S_CONTACT_EMAIL")

    def service_key(self, service: str) -> str | None:
        env, _ = SERVICE_KEYS[service]
        return self.secrets.get(f"service:{service}") or os.environ.get(env)

    def set_service_key(self, service: str, value: str | None) -> None:
        if service not in SERVICE_KEYS:
            raise KeyError(service)
        if value:
            self.secrets.set(f"service:{service}", value.strip())
            if service == "github":
                os.environ["GITHUB_TOKEN"] = value.strip()
        else:
            self.secrets.delete(f"service:{service}")

    def lit_config(self) -> LitConfig:
        return LitConfig(s2_api_key=self.service_key("s2"), openalex_api_key=self.service_key("openalex"),
                         ncbi_api_key=self.service_key("ncbi"), contact_email=self.contact_email())

    def mcp_configs(self) -> list[Any]:
        from ..mcp.manager import MCPServerConfig

        return [MCPServerConfig(**d) for d in self.db.list("mcp")]

    async def aclose(self) -> None:
        if "kernels" in self.__dict__:
            await self.kernels.shutdown_all()
        if "mcp" in self.__dict__:
            await self.mcp.aclose()
        if "http" in self.__dict__:
            await self.http.aclose()


def workspace_trace_path(workspace: Path, run_id: str) -> Path:
    return workspace / ".a4s" / "runs" / run_id / "trace.jsonl"
