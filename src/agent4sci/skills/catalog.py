"""Curated catalog of external skill sources, domain packs and a searchable index."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field

from ..core.paths import app_data_dir, cache_dir, resource
from .hub import SkillHub, classify_license, parse_source, skill_license
from .spec import SkillParseError, load_skill


class CatalogSource(BaseModel):
    id: str
    repo: str
    path: str = ""
    license: str = ""
    trust: str = "community"
    domains: list[str] = Field(default_factory=list)
    description: str = ""
    note: str = ""
    user_added: bool = False

    @property
    def spec(self) -> str:
        return f"{self.repo}/{self.path}".rstrip("/") if self.path else self.repo


class PackItem(BaseModel):
    source: str
    skills: list[str]
    tags: list[str] = Field(default_factory=list)


class CatalogMCP(BaseModel):
    name: str
    label: str = ""
    repo: str = ""
    license: str = ""
    domains: list[str] = Field(default_factory=list)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    note: str = ""  # anything worth knowing before adding it (a heavy first install, a runtime requirement)


class Catalog(BaseModel):
    sources: list[CatalogSource]
    packs: dict[str, list[PackItem]] = Field(default_factory=dict)
    mcp_servers: list[CatalogMCP] = Field(default_factory=list)

    def source(self, source_id: str) -> CatalogSource:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(source_id)


def _user_sources_path() -> Path:
    return app_data_dir() / "catalog-sources.json"


def load_catalog() -> Catalog:
    data = yaml.safe_load(resource("skills", "catalog.yaml").read_text("utf-8"))
    catalog = Catalog(**data)
    extra = _user_sources_path()
    if extra.exists():
        try:
            for item in json.loads(extra.read_text("utf-8")):
                catalog.sources.append(CatalogSource(**item, user_added=True))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return catalog


def add_user_source(repo: str, path: str = "", description: str = "", domains: list[str] | None = None) -> CatalogSource:
    parse_source(repo)  # validates
    src = CatalogSource(id=re.sub(r"[^a-z0-9]+", "-", repo.lower()).strip("-"), repo=repo, path=path,
                        description=description, domains=domains or [], trust="user", user_added=True)
    path_ = _user_sources_path()
    items = json.loads(path_.read_text("utf-8")) if path_.exists() else []
    items = [i for i in items if i.get("id") != src.id] + [src.model_dump(exclude={"user_added"})]
    path_.write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")
    return src


class IndexEntry(BaseModel):
    source: str
    name: str
    description: str
    path: str
    license: str | None = None
    license_status: str = "unknown"
    domains: list[str] = Field(default_factory=list)
    trust: str = "community"

    @property
    def install_spec(self) -> str:
        return ""


class CatalogIndex:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_dir() / "catalog-index.json"
        self.data: dict[str, Any] = {"built_at": None, "sources": {}, "entries": []}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

    @property
    def entries(self) -> list[IndexEntry]:
        return [IndexEntry(**e) for e in self.data.get("entries", [])]

    @property
    def built_at(self) -> float | None:
        return self.data.get("built_at")

    async def build(self, catalog: Catalog, hub: SkillHub, only: list[str] | None = None,
                    progress: Callable[[str, str], None] | None = None) -> dict[str, Any]:
        entries = [e for e in self.data.get("entries", []) if only and e["source"] not in only]
        sources_info = {k: v for k, v in self.data.get("sources", {}).items() if only and k not in only}
        for src in catalog.sources:
            if only and src.id not in only:
                continue
            if progress:
                progress(src.id, "fetching")
            try:
                source = parse_source(src.spec)
                fetched = await hub.fetch(source, metadata_only=True)
                dirs = hub.discover(fetched.root, source.subpath)
            except Exception as exc:  # network errors etc.: keep going with other sources
                sources_info[src.id] = {"error": str(exc)[:300], "at": time.time()}
                if progress:
                    progress(src.id, f"error: {exc}")
                continue
            count = 0
            for d in dirs:
                try:
                    parsed = load_skill(d, strict=False)
                except SkillParseError:
                    continue
                ident, status = skill_license(d, parsed.meta.license, fetched.root)
                entries.append(IndexEntry(
                    source=src.id, name=parsed.meta.name, description=parsed.meta.description,
                    path=d.relative_to(fetched.root).as_posix(), license=ident, license_status=status,
                    domains=src.domains, trust=src.trust,
                ).model_dump())
                count += 1
            sources_info[src.id] = {"commit": fetched.commit, "count": count, "at": time.time()}
            if progress:
                progress(src.id, f"{count} skills")
        self.data = {"built_at": time.time(), "sources": sources_info, "entries": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), "utf-8")
        return sources_info

    def search(self, query: str, limit: int = 20, domain: str | None = None) -> list[IndexEntry]:
        terms = [t for t in re.split(r"[^\w一-鿿]+", query.lower()) if t]
        scored: list[tuple[float, IndexEntry]] = []
        for entry in self.entries:
            if domain and domain not in entry.domains:
                continue
            name = entry.name.lower()
            desc = entry.description.lower()
            score = 0.0
            for term in terms:
                if term == name:
                    score += 6
                elif term in name:
                    score += 3
                score += desc.count(term)
            if not terms:
                score = 1
            if score:
                if entry.license_status != "open":
                    score *= 0.5
                if entry.trust == "low":
                    score *= 0.7
                scored.append((score, entry))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]


def install_spec_for(catalog: Catalog, entry: IndexEntry) -> str:
    src = catalog.source(entry.source)
    return f"{src.repo}/{entry.path}" if entry.path and entry.path != "." else src.repo


__all__ = ["Catalog", "CatalogIndex", "CatalogMCP", "CatalogSource", "IndexEntry", "PackItem", "add_user_source",
           "classify_license", "install_spec_for", "load_catalog"]
