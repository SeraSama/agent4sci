"""Routes for skills, MCP servers and memory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body

from ..agent.harness import HarnessContext, load_domain, memory_roots
from ..core.paths import user_skills_dir
from ..mcp.manager import MCPServerConfig, check_server, missing_refs
from ..mcp.registry_api import config_from_registry, search_registry
from ..memory.store import SCOPE_LABELS, MemoryAccess
from ..skills.catalog import add_user_source, install_spec_for
from ..skills.distill import review_learned
from ..skills.lock import LockFile
from ..skills.registry import SkillRegistry, default_roots
from ..skills.scanner import scan_skill
from ..skills.spec import list_resources, load_skill

if TYPE_CHECKING:  # pragma: no cover
    from .app import ServerState


def domain_pack_items(domain: str) -> list[Any]:
    """A user-written field pack can list its own skills: `skills: [{source, skills, tags}]`."""
    from ..skills.catalog import PackItem

    items = []
    for entry in (load_domain(domain) or {}).get("skills") or []:
        if isinstance(entry, dict) and entry.get("source"):
            items.append(PackItem(source=str(entry["source"]), skills=[str(s) for s in entry.get("skills") or []],
                                  tags=[str(x) for x in entry.get("tags") or []]))
    return items


def register_extra(api: APIRouter, state: "ServerState") -> None:
    services = state.app

    def project(project_id: str | None):
        if not project_id:
            return None
        p = services.projects.get(project_id)
        if p is None:
            raise KeyError(project_id)
        return p

    def registry(project_id: str | None) -> SkillRegistry:
        p = project(project_id)
        return SkillRegistry(default_roots(p.workspace if p else None, trust_project_compat=bool(p and p.settings.trusted)))

    def target(scope: str, project_id: str | None) -> tuple[Path, LockFile]:
        if scope == "project":
            p = project(project_id)
            if p is None:
                raise ValueError("安装到项目需要 project_id")
            return p.workspace / "skills" / "installed", LockFile(p.workspace / "skills.lock")
        root = user_skills_dir()
        return root, LockFile(root / "skills.lock")

    # ---- skills -----------------------------------------------------------------------
    @api.get("/skills")
    async def list_skills(project_id: str | None = None) -> dict[str, Any]:
        reg = registry(project_id)
        items = []
        for r in reg.list(include_inactive=True):
            items.append({"name": r.name, "description": r.description, "root": r.root_kind, "trust": r.trust,
                          "status": r.status, "license": r.license, "tags": r.tags, "warnings": r.warnings,
                          "path": str(r.path), "shadowed": r.shadowed,
                          "lock": r.lock.model_dump(mode="json") if r.lock else None})
        return {"skills": items, "errors": reg.errors}

    @api.get("/skills/detail/{name}")
    async def skill_detail(name: str, project_id: str | None = None) -> dict[str, Any]:
        record = registry(project_id).get(name, include_inactive=True)
        if record is None:
            raise KeyError(name)
        parsed = load_skill(record.path)
        return {"name": record.name, "description": record.description, "body": parsed.body,
                "meta": parsed.meta.model_dump(), "resources": list_resources(record.path), "trust": record.trust,
                "status": record.status, "root": record.root_kind, "path": str(record.path),
                "lock": record.lock.model_dump(mode="json") if record.lock else None, "warnings": parsed.warnings}

    @api.post("/skills/scan/{name}")
    async def scan(name: str, project_id: str | None = None) -> dict[str, Any]:
        record = registry(project_id).get(name, include_inactive=True)
        if record is None:
            raise KeyError(name)
        return scan_skill(record.path).model_dump(mode="json")

    @api.post("/skills/trust/{name}")
    async def trust(name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        scope, project_id = body.get("scope", "global"), body.get("project_id")
        _, lock = target(scope, project_id)
        entries = lock.load()
        if name not in entries:
            raise KeyError(name)
        entries[name].trust = "reviewed" if body.get("reviewed", True) else "unreviewed"
        lock.save(entries)
        return {"trust": entries[name].trust}

    @api.post("/skills/install")
    async def install(body: dict[str, Any] = Body(...)) -> list[dict[str, Any]]:
        root, lock = target(body.get("scope", "project" if body.get("project_id") else "global"), body.get("project_id"))
        return await services.skill_hub.install(
            body["source"], target_dir=root, lock=lock, select=body.get("select") or None,
            allow_license=bool(body.get("allow_license")), force=bool(body.get("force")), tags=body.get("tags"),
            dry_run=bool(body.get("dry_run")))

    @api.post("/skills/remove/{name}")
    async def remove(name: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        root, lock = target(body.get("scope", "global"), body.get("project_id"))
        return {"removed": services.skill_hub.remove(name, target_dir=root, lock=lock)}

    @api.post("/skills/update-check/{name}")
    async def update_check(name: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        root, lock = target(body.get("scope", "global"), body.get("project_id"))
        return await services.skill_hub.check_update(name, target_dir=root, lock=lock)

    @api.post("/skills/learned/{name}/review")
    async def review(name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        p = project(body.get("project_id"))
        if p is None:
            raise ValueError("需要 project_id")
        review_learned(p.workspace / "skills" / "learned", name, bool(body.get("approve")))
        return {"ok": True}

    @api.get("/skills/catalog")
    async def catalog() -> dict[str, Any]:
        cat = services.catalog
        index = services.skill_index
        return {"sources": [s.model_dump() for s in cat.sources],
                "packs": {k: [i.model_dump() for i in v] for k, v in cat.packs.items()},
                "index": {"built_at": index.built_at, "count": len(index.data.get("entries", [])),
                          "sources": index.data.get("sources", {})}}

    @api.post("/skills/catalog/refresh")
    async def refresh(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        async def job() -> None:
            services.bus.publish("skills", "catalog.started", {})
            info = await services.skill_index.build(
                services.catalog, services.skill_hub, only=body.get("only"),
                progress=lambda sid, msg: services.bus.publish("skills", "catalog.progress", {"source": sid, "status": msg}))
            services.bus.publish("skills", "catalog.done", {"sources": info})

        state.spawn(job(), "catalog-refresh")
        return {"started": True}

    @api.get("/skills/search")
    async def search(q: str = "", domain: str | None = None, limit: int = 40, project_id: str | None = None) -> list[dict[str, Any]]:
        installed = {r.name for r in registry(project_id).list(include_inactive=True)}
        out = []
        for entry in services.skill_index.search(q, limit, domain):
            out.append({**entry.model_dump(), "spec": install_spec_for(services.catalog, entry),
                        "installed": entry.name in installed})
        return out

    @api.post("/skills/sources")
    async def add_source(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        src = add_user_source(body["repo"], body.get("path", ""), body.get("description", ""), body.get("domains"))
        services.__dict__.pop("catalog", None)  # reload on next access
        return src.model_dump()

    @api.post("/skills/packs/{domain}/install")
    async def install_pack(domain: str, body: dict[str, Any] = Body(default={})) -> list[dict[str, Any]]:
        items = services.catalog.packs.get(domain) or domain_pack_items(domain)
        if not items:
            raise KeyError(domain)
        root, lock = target(body.get("scope", "project" if body.get("project_id") else "global"), body.get("project_id"))
        results: list[dict[str, Any]] = []
        for item in items:
            try:
                spec = services.catalog.source(item.source).spec
            except KeyError:
                spec = item.source  # a user-written field pack may name a repo directly: owner/repo[/path][@ref]
            try:
                results += await services.skill_hub.install(spec, target_dir=root, lock=lock, select=item.skills,
                                                            tags=item.tags)
            except Exception as exc:  # one failing source should not block the pack
                results.append({"name": item.source, "status": "error", "reason": str(exc)[:300]})
        return results

    # ---- MCP ----------------------------------------------------------------------------------
    @api.get("/mcp")
    async def list_mcp() -> dict[str, Any]:
        status = {s["name"]: s for s in services.mcp.status()}
        configs = []
        for cfg in services.mcp_configs():
            configs.append({**cfg.model_dump(), "status": status.get(cfg.name), "missing_env": missing_refs(cfg)})
        return {"servers": configs, "catalog": [m.model_dump() for m in services.catalog.mcp_servers]}

    @api.post("/mcp")
    async def add_mcp(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        cfg = MCPServerConfig(**body)
        if services.db.get("mcp", cfg.name) is not None and not body.get("replace"):
            raise ValueError(f"已存在同名 MCP 服务器：{cfg.name}")
        services.db.put("mcp", cfg.name, cfg.model_dump())
        if cfg.enabled:
            state.spawn(services.mcp.restart(cfg), f"mcp:{cfg.name}")
        return cfg.model_dump()

    @api.put("/mcp/{name}")
    async def update_mcp(name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        current = services.db.get("mcp", name)
        if current is None:
            raise KeyError(name)
        cfg = MCPServerConfig(**{**current, **body, "name": name})
        services.db.put("mcp", name, cfg.model_dump())
        # A first start can spend minutes downloading packages. Restarting it because the user toggled
        # something unrelated (or pressed the button twice) throws that away, so only restart when the
        # launch actually changed — or when the server is not up.
        launch = ("command", "args", "url", "env", "headers", "tools")
        previous = MCPServerConfig(**current)
        changed = any(getattr(cfg, k) != getattr(previous, k) for k in launch)
        handle = services.mcp.handles.get(name)
        busy = handle is not None and handle.error is None and not handle.ok  # still connecting
        if cfg.enabled:
            if changed or not busy:
                state.spawn(services.mcp.restart(cfg), f"mcp:{name}")
        else:
            handle = services.mcp.handles.pop(name, None)
            if handle is not None:
                state.spawn(handle.stop(), f"mcp-stop:{name}")
        return cfg.model_dump()

    @api.delete("/mcp/{name}")
    async def delete_mcp(name: str) -> dict[str, Any]:
        handle = services.mcp.handles.pop(name, None)
        if handle is not None:
            await handle.stop()
        return {"deleted": services.db.delete("mcp", name)}

    @api.post("/mcp/{name}/test")
    async def test_mcp(name: str) -> dict[str, Any]:
        data = services.db.get("mcp", name)
        if data is None:
            raise KeyError(name)
        return await check_server(MCPServerConfig(**data))

    @api.put("/mcp-env/{var}")
    async def set_env_secret(var: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Store a value for ${VAR} references in MCP configs (kept in the OS keychain)."""
        if body.get("value"):
            services.secrets.set(f"env:{var}", str(body["value"]))
        else:
            services.secrets.delete(f"env:{var}")
        return {"ok": True}

    @api.get("/mcp/registry")
    async def registry_search(q: str) -> list[dict[str, Any]]:
        out = []
        for server in await search_registry(services.http, q):
            try:
                cfg = config_from_registry(server).model_dump()
            except ValueError:
                cfg = None
            out.append({**server.model_dump(), "config": cfg, "env_vars": [v.model_dump() for v in server.env_vars()]})
        return out

    # ---- memory ---------------------------------------------------------------------------------
    def access(project_id: str | None, conversation_id: str | None) -> MemoryAccess:
        p = project(project_id)
        ws = p.workspace if p else Path(".")
        ctx = HarnessContext(workspace=ws, project=p, conversation_id=conversation_id)
        return MemoryAccess("global", memory_roots(ctx))

    @api.get("/memory")
    async def list_memory(project_id: str | None = None, conversation_id: str | None = None) -> list[dict[str, Any]]:
        acc = access(project_id, conversation_id)
        return [{"scope": scope, "label": SCOPE_LABELS[scope], "path": str(store.root),
                 "items": [i.model_dump(exclude={"body"}) for i in store.list()]} for scope, store in acc.stores.items()]

    @api.get("/memory/item")
    async def get_memory(scope: str, file: str, project_id: str | None = None,
                         conversation_id: str | None = None) -> dict[str, Any]:
        store = access(project_id, conversation_id).store(scope)
        path = (store.root / file).resolve()
        if store.root.resolve() not in path.parents or not path.exists():
            raise KeyError(file)
        return store.load(path).model_dump()

    @api.put("/memory/item")
    async def put_memory(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        store = access(body.get("project_id"), body.get("conversation_id")).store(body["scope"])
        item = store.write(body["name"], body.get("body", ""), description=body.get("description", ""),
                           type=body.get("type", "fact"), source=body.get("source", "manual"), file=body.get("file"))
        return item.model_dump()

    @api.delete("/memory/item")
    async def delete_memory(scope: str, file: str, project_id: str | None = None,
                            conversation_id: str | None = None) -> dict[str, Any]:
        return {"deleted": access(project_id, conversation_id).store(scope).delete(file)}
