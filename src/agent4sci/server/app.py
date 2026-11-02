"""FastAPI backend for the desktop app (listens on 127.0.0.1 only; every request needs the launch token)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from .. import __version__
from ..agent.harness import (ALL_GROUPS, GROUP_LABELS, HarnessBuilder, HarnessContext, HarnessProfile, domain_path,
                             list_domains, memory_roots, tool_groups)
from ..core.app import SERVICE_KEYS, AppServices
from ..core.assets import delete_asset, import_asset, list_assets, asset_path, content_type
from ..core.audit import audit_project, default_audit_config, latest_report
from ..core.backup import backup_dir, create_backup, data_dir_size, inspect_backup, restore_backup
from ..core.config_bundle import export_config, import_config
from ..agent.permissions import MODE_HINTS, MODE_LABELS
from ..core.diagnostics import run_diagnostics
from ..core.models import PRESETS, ModelProfile, demo_mode, profile_from_preset
from ..core.paths import app_data_dir, default_projects_dir
from ..core.projects import RECOMMENDED_DIRS, REQUIRED_DIRS, WORKSPACE_DIRS, ProjectSettings, create_venv
from ..core.textio import decode_text, looks_binary
from ..llm.pricing import resolve_pricing
from ..loops import gitops
from ..loops.engine import LoopEngine
from ..loops.schema import NODE_TYPE_INFO, LoopGraph, validate_graph
from ..memory.store import MODE_SCOPES, SCOPE_LABELS, MemoryAccess
from ..tools.bib import BIB_FILE, bib_keys
from ..tools.findings import load_findings
from .conversations import ConversationManager

TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".csv", ".tsv", ".yaml", ".yml", ".bib", ".tex", ".log", ".r", ".R",
                 ".m", ".jl", ".sh", ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".ipynb", ".jsonl", ""}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".svg": "image/svg+xml", ".webp": "image/webp"}


class ServerState:
    def __init__(self, app: AppServices, token: str | None, provider_factory: Callable | None = None) -> None:
        self.app = app
        self.token = token
        self.builder = HarnessBuilder(app, provider_factory)
        self.engine = LoopEngine(app, self.builder)
        self.conversations = ConversationManager(app, self.builder)
        self.background: set[asyncio.Task] = set()

    def spawn(self, coro: Any, name: str = "") -> asyncio.Task:
        task = asyncio.create_task(coro, name=name or None)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def startup(self) -> None:
        for state in self.engine.runs.list(limit=200):
            if state.status in ("running", "waiting", "pending"):
                state.status = "paused"
                state.error = "应用重启时运行被中断，可继续运行"
                self.engine.runs.save(state)
        self.conversations.sweep_interrupted()
        configs = [c for c in self.app.mcp_configs() if c.enabled]
        if configs:
            self.spawn(self.app.mcp.start(configs), "mcp-start")

    async def shutdown(self) -> None:
        await self.conversations.aclose()
        for run_id, task in list(self.engine.tasks.items()):
            if not task.done():
                self.engine.cancel(run_id)
        for task in list(self.background):
            task.cancel()
        await self.app.aclose()


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_dump(o) for o in obj]
    return obj


def _resolve_in(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    base = root.resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "路径越界")
    return target


def create_app(state: ServerState) -> FastAPI:
    services = state.app

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        await state.startup()
        yield
        await state.shutdown()

    app = FastAPI(title="Agent4Sci", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def auth(request: Request) -> None:
        if state.token is None:
            return
        header = request.headers.get("authorization", "")
        if header == f"Bearer {state.token}" or request.query_params.get("token") == state.token:
            return
        raise HTTPException(401, "unauthorized")

    @app.exception_handler(KeyError)
    async def _not_found(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": f"不存在：{exc.args[0] if exc.args else ''}"}, status_code=404)

    @app.exception_handler(FileNotFoundError)
    async def _missing_file(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def _bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def _runtime(_: Request, exc: RuntimeError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "demo": demo_mode()}

    api = APIRouter(prefix="/api", dependencies=[Depends(auth)])

    @api.get("/permission-modes")
    async def permission_modes() -> list[dict[str, Any]]:
        return [{"id": mode, "label": label, "hint": MODE_HINTS[mode]} for mode, label in MODE_LABELS.items()]

    @api.get("/diagnostics")
    async def diagnostics(network: bool = False) -> dict[str, Any]:
        """What `a4s doctor` checks, for the packaged app that ships no CLI."""
        return await run_diagnostics(services, network=network)

    # ---- configuration bundle --------------------------------------------------------------
    @api.get("/config/export")
    async def config_export() -> dict[str, Any]:
        """Everything another machine needs to reproduce this setup, except the API keys."""
        return export_config(services)

    @api.post("/config/import")
    async def config_import(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        bundle = body.get("bundle") or body
        result = import_config(services, bundle, overwrite=bool(body.get("overwrite")))
        services.__dict__.pop("catalog", None)
        return result

    # ---- backup --------------------------------------------------------------------------
    @api.get("/backups")
    async def list_backups() -> dict[str, Any]:
        items = []
        for path in sorted(backup_dir().glob("*.zip"), reverse=True):
            items.append({"path": str(path), "name": path.name, "size": path.stat().st_size,
                          "created": path.stat().st_mtime})
        return {"backups": items, "dir": str(backup_dir()), "data": data_dir_size()}

    @api.post("/backups")
    async def make_backup(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        target = Path(body["path"]).expanduser() if body.get("path") else None
        return await asyncio.to_thread(create_backup, target)

    @api.post("/backups/inspect")
    async def read_backup(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return inspect_backup(Path(str(body["path"])).expanduser())

    @api.post("/backups/restore")
    async def apply_backup(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Replace the current data with a backup. The app must restart afterwards."""
        return await asyncio.to_thread(restore_backup, Path(str(body["path"])).expanduser())

    # ---- info & settings ---------------------------------------------------------------
    @api.get("/info")
    async def info() -> dict[str, Any]:
        return {"version": __version__, "platform": platform.system(), "machine": platform.machine(),
                "data_dir": str(app_data_dir()), "projects_dir": str(default_projects_dir()), "demo": demo_mode(),
                "default_model": services.models.default_id(), "secret_store": services.secrets.name,
                "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False)),
                "utf8_mode": bool(sys.flags.utf8_mode),
                # host:port only — a proxy URL may carry credentials
                "proxy": re.sub(r"^\w+://(?:[^@/]*@)?", "", os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                                or "") or None}

    @api.get("/settings")
    async def get_settings() -> dict[str, Any]:
        db = services.db
        return {"contact_email": services.contact_email() or "", "language": db.get_setting("language", "zh-CN"),
                "theme": db.get_setting("theme", {"mode": "system", "light": "light", "dark": "dark"}),
                "memory_auto_apply": db.get_setting("memory_auto_apply", False), "demo_mode": demo_mode(),
                "ui": db.get_setting("ui", {}), "terms_accepted_at": db.get_setting("terms_accepted_at", None),
                "services": {k: {"label": label, "env": env, "configured": bool(services.service_key(k))}
                             for k, (env, label) in SERVICE_KEYS.items()}}

    @api.put("/settings")
    async def put_settings(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        for key in ("contact_email", "language", "theme", "memory_auto_apply", "ui", "terms_accepted_at"):
            if key in body:
                services.db.set_setting(key, body[key])
        if "demo_mode" in body:
            services.db.set_setting("demo_mode", bool(body["demo_mode"]))
            if body["demo_mode"]:
                os.environ["A4S_FAKE_LLM"] = "1"
            else:
                os.environ.pop("A4S_FAKE_LLM", None)
        return await get_settings()

    @api.put("/settings/services/{service}")
    async def put_service_key(service: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        services.set_service_key(service, body.get("value"))
        return {"configured": bool(services.service_key(service))}

    @api.get("/themes")
    async def list_themes() -> list[dict[str, Any]]:
        return services.db.list("theme")

    @api.put("/themes/{theme_id}")
    async def put_theme(theme_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        body["id"] = theme_id
        services.db.put("theme", theme_id, body)
        return body

    @api.delete("/themes/{theme_id}")
    async def delete_theme(theme_id: str) -> dict[str, Any]:
        return {"deleted": services.db.delete("theme", theme_id)}

    # ---- assets (background images / videos the user imports) -----------------------------
    @api.get("/assets")
    async def get_assets() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_assets)

    @api.post("/assets")
    async def add_asset(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return await asyncio.to_thread(import_asset, Path(str(body["path"])))

    @api.get("/assets/{asset_id}/raw")
    async def read_asset(asset_id: str) -> FileResponse:
        path = asset_path(asset_id)
        if path is None:
            raise HTTPException(404, f"找不到素材 {asset_id}")
        # Range requests matter here: a <video> seeks, and FileResponse handles them.
        return FileResponse(path, media_type=content_type(asset_id),
                            headers={"Cache-Control": "private, max-age=86400"})

    @api.delete("/assets/{asset_id}")
    async def remove_asset(asset_id: str) -> dict[str, Any]:
        return {"deleted": await asyncio.to_thread(delete_asset, asset_id)}

    # ---- models ---------------------------------------------------------------------------
    def model_view(p: ModelProfile) -> dict[str, Any]:
        return {**p.model_dump(mode="json"), "key": services.models.key_status(p),
                "pricing_known": resolve_pricing(p) is not None}

    @api.get("/models")
    async def list_models() -> dict[str, Any]:
        return {"models": [model_view(p) for p in services.models.list()], "default": services.models.default_id(),
                "roles": services.models.role_models()}

    @api.get("/models/presets")
    async def presets() -> list[dict[str, Any]]:
        return [p.model_dump(mode="json") for p in PRESETS.values()]

    @api.post("/models")
    async def create_model(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        preset = body.pop("preset", None) or "custom"
        api_key = body.pop("api_key", None)
        profile = profile_from_preset(preset, **{k: v for k, v in body.items() if v is not None})
        if not profile.model:
            raise ValueError("请填写模型名")
        services.models.save(profile)
        if api_key:
            services.models.set_api_key(profile.id, api_key)
        if services.models.default_id() in (None, "demo") or len(services.models.list(include_demo=False)) == 1:
            services.models.set_default(profile.id)
        return model_view(services.models.get(profile.id))

    @api.put("/models/{model_id}")
    async def update_model(model_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        current = services.models.get(model_id)
        if current is None:
            raise KeyError(model_id)
        api_key = body.pop("api_key", None)
        body.pop("key", None)
        merged = {**current.model_dump(), **body, "id": model_id}
        profile = services.models.save(ModelProfile(**merged))
        if api_key is not None:
            services.models.set_api_key(model_id, api_key)
        return model_view(services.models.get(profile.id))

    @api.delete("/models/{model_id}")
    async def delete_model(model_id: str) -> dict[str, Any]:
        return {"deleted": services.models.delete(model_id)}

    @api.post("/models/{model_id}/test")
    async def test_model(model_id: str) -> dict[str, Any]:
        from ..llm.probe import test_connection

        profile = services.models.get(model_id)
        if profile is None:
            raise KeyError(model_id)
        return await test_connection(profile, services.models.resolve_api_key(profile))

    @api.put("/models-defaults")
    async def put_defaults(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if body.get("default"):
            services.models.set_default(body["default"])
        if "roles" in body:
            services.models.set_role_models({k: v for k, v in (body["roles"] or {}).items() if v})
        return {"default": services.models.default_id(), "roles": services.models.role_models()}

    # ---- projects & files ----------------------------------------------------------------
    @api.get("/domains")
    async def domains() -> list[dict[str, Any]]:
        out = []
        for d in list_domains():
            path = domain_path(d["id"], user=not d.get("builtin", True))
            out.append({**d, "source": path.read_text("utf-8") if path.exists() else ""})
        return out

    @api.put("/domains/{domain_id}")
    async def save_domain(domain_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Write a domain pack into the data directory; a user pack overrides a built-in one with the same id."""
        source = str(body.get("source") or "")
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML 格式错误：{exc}") from exc
        if not isinstance(data, dict) or not str(data.get("label") or "").strip():
            raise ValueError("领域包至少需要 label（显示名称）；可以参考内置领域包的写法。")
        if str(data.get("id") or domain_id) != domain_id:
            raise ValueError(f"文件里的 id（{data.get('id')}）必须和领域包名称（{domain_id}）一致。")
        path = domain_path(domain_id, user=True)
        path.write_text(source, "utf-8")
        return {**data, "id": domain_id, "builtin": False, "source": source}

    @api.delete("/domains/{domain_id}")
    async def delete_domain(domain_id: str) -> dict[str, Any]:
        path = domain_path(domain_id, user=True)
        if not path.exists():
            raise KeyError(domain_id)  # built-in packs live inside the app and cannot be deleted
        path.unlink()
        return {"deleted": True, "builtin_restored": domain_path(domain_id).exists()}

    @api.get("/projects")
    async def list_projects() -> list[dict[str, Any]]:
        return _dump(services.projects.list())

    @api.get("/workspace-dirs")
    async def workspace_dirs() -> dict[str, Any]:
        return {"dirs": WORKSPACE_DIRS, "required": REQUIRED_DIRS, "recommended": RECOMMENDED_DIRS}

    @api.post("/projects")
    async def create_project(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        project = services.projects.create(body["name"], body.get("path") or None, body.get("description", ""),
                                           body.get("domains") or [], body.get("dirs"))
        if body.get("create_venv"):
            state.spawn(_venv(project.id), f"venv:{project.id}")
        return _dump(project)

    async def _venv(project_id: str) -> None:
        project = services.projects.get(project_id)
        topic = f"project:{project_id}"
        services.bus.publish(topic, "venv.started", {})
        result = await create_venv(project.workspace,
                                   progress=lambda step: services.bus.publish(topic, "venv.progress", {"step": step}))
        services.bus.publish(topic, "venv.done", result)

    @api.post("/projects/open")
    async def open_project(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return _dump(services.projects.open_existing(body["path"]))

    @api.get("/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        project = services.projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        from ..core.envfix import venv_python
        from ..core.projects import project_environment

        return {**_dump(project), "environment": project_environment(project.workspace),
                "venv": bool(venv_python(project.workspace)), "git": gitops.available(project.workspace)}

    @api.put("/projects/{project_id}")
    async def update_project(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        project = services.projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        for key in ("name", "description", "domains"):
            if key in body:
                setattr(project, key, body[key])
        if "settings" in body:
            project.settings = ProjectSettings(**{**project.settings.model_dump(), **body["settings"]})
        return _dump(services.projects.save(project))

    @api.delete("/projects/{project_id}")
    async def delete_project(project_id: str) -> dict[str, Any]:
        return {"deleted": services.projects.delete(project_id)}

    @api.post("/projects/{project_id}/venv")
    async def make_venv(project_id: str) -> dict[str, Any]:
        state.spawn(_venv(project_id), f"venv:{project_id}")
        return {"started": True}

    def _root(project_id: str | None, conversation_id: str | None) -> Path:
        if project_id:
            project = services.projects.get(project_id)
            if project is None:
                raise KeyError(project_id)
            return project.workspace
        if conversation_id:
            return state.conversations.workspace(state.conversations.get(conversation_id))
        raise ValueError("需要 project_id 或 conversation_id")

    @api.get("/files")
    async def list_files(project_id: str | None = None, conversation_id: str | None = None, path: str = ".") -> dict[str, Any]:
        root = _root(project_id, conversation_id)
        target = _resolve_in(root, path)
        if not target.is_dir():
            raise ValueError("不是目录")
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name in (".git", ".venv", "__pycache__", ".DS_Store"):
                continue
            stat = child.stat()
            entries.append({"name": child.name, "path": child.relative_to(root.resolve()).as_posix(),
                            "dir": child.is_dir(), "size": stat.st_size, "mtime": stat.st_mtime})
        return {"root": str(root), "path": path, "entries": entries}

    @api.get("/file")
    async def read_file(path: str, project_id: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        root = _root(project_id, conversation_id)
        target = _resolve_in(root, path)
        if not target.is_file():
            raise KeyError(path)
        size = target.stat().st_size
        suffix = target.suffix.lower()
        if suffix in IMAGE_TYPES:
            return {"path": path, "kind": "image", "mime": IMAGE_TYPES[suffix], "size": size,
                    "content": base64.b64encode(target.read_bytes()).decode()}
        if suffix == ".pdf":
            from ..tools.fs import _pdf_text

            return {"path": path, "kind": "text", "size": size, "content": _pdf_text(target, 20)}
        raw = target.read_bytes()[:2_000_000]
        if looks_binary(raw[:4096]) and suffix not in TEXT_SUFFIXES:
            return {"path": path, "kind": "binary", "size": size}
        return {"path": path, "kind": "text", "size": size, "content": decode_text(raw),
                "truncated": size > 2_000_000}

    @api.put("/file")
    async def write_file(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        root = _root(body.get("project_id"), body.get("conversation_id"))
        target = _resolve_in(root, body["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.get("content", ""), "utf-8")
        return {"saved": True}

    @api.get("/projects/{project_id}/git")
    async def git_log(project_id: str) -> list[dict[str, Any]]:
        return gitops.log(services.projects.get(project_id).workspace)

    @api.post("/projects/{project_id}/git/rollback")
    async def git_rollback(project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return {"ok": gitops.rollback(services.projects.get(project_id).workspace, body["sha"])}

    @api.get("/projects/{project_id}/findings")
    async def findings(project_id: str) -> list[dict[str, Any]]:
        return load_findings(services.projects.get(project_id).workspace)

    @api.post("/projects/{project_id}/audit")
    async def audit(project_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        """Run the project self-check now. Reports only — it never blocks a run."""
        workspace = services.projects.get(project_id).workspace
        return await asyncio.to_thread(audit_project, workspace, body.get("config") or None)

    @api.get("/projects/{project_id}/audit")
    async def last_audit(project_id: str) -> dict[str, Any]:
        workspace = services.projects.get(project_id).workspace
        return latest_report(workspace) or {"ts": None, "checked": default_audit_config(workspace)}

    @api.get("/projects/{project_id}/references")
    async def references(project_id: str) -> list[dict[str, Any]]:
        bib = services.projects.get(project_id).workspace / BIB_FILE
        text = bib.read_text("utf-8") if bib.exists() else ""
        return [{"key": k, "entry": v} for k, v in bib_keys(text).items()]

    # ---- conversations -------------------------------------------------------------------
    convs = state.conversations

    @api.get("/conversations")
    async def list_conversations(project_id: str | None = None, archived: bool = False) -> list[dict[str, Any]]:
        return [c.public() for c in convs.list(project_id, include_archived=archived)]

    @api.post("/conversations")
    async def create_conversation(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        conv = convs.create(project_id=body.get("project_id"), title=body.get("title"),
                            memory_mode=body.get("memory_mode"), harness_id=body.get("harness_id") or "assistant",
                            model_id=body.get("model_id"), temporary=bool(body.get("temporary")))
        return conv.public()

    @api.get("/conversations/{conv_id}")
    async def get_conversation(conv_id: str) -> dict[str, Any]:
        return {**convs.get(conv_id).public(), "messages": convs.messages(conv_id),
                "pending": _dump(services.interactions.pending(f"conv:{conv_id}"))}

    @api.patch("/conversations/{conv_id}")
    async def patch_conversation(conv_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        allowed = {k: body[k] for k in ("title", "memory_mode", "harness_id", "model_id", "archived",
                                        "permission_mode") if k in body}
        return convs.update(conv_id, **allowed).public()

    @api.delete("/conversations/{conv_id}")
    async def delete_conversation(conv_id: str) -> dict[str, Any]:
        convs.delete(conv_id)
        return {"deleted": True}

    @api.post("/conversations/{conv_id}/messages")
    async def post_message(conv_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        text = (body.get("text") or "").strip()
        files = [str(f) for f in (body.get("files") or [])]
        if not text and not files:
            raise ValueError("消息不能为空")
        return await convs.send(conv_id, text or "（见附带的文件）", files)

    @api.get("/path-info")
    async def path_info(path: str) -> dict[str, Any]:
        """Does this path exist on this machine? Used by the composer when a file path is pasted or dropped."""
        target = Path(path).expanduser()
        if not target.exists():
            return {"path": path, "exists": False}
        stat = target.stat()
        return {"path": str(target.resolve()), "name": target.name, "exists": True,
                "is_dir": target.is_dir(), "size": stat.st_size}

    @api.post("/conversations/{conv_id}/stop")
    async def stop_conversation(conv_id: str) -> dict[str, Any]:
        return {"stopped": convs.stop(conv_id)}

    @api.post("/conversations/{conv_id}/effort")
    async def set_effort(conv_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        convs.set_effort(conv_id, body.get("effort"))
        return {"ok": True}

    @api.post("/conversations/{conv_id}/persist")
    async def persist_conversation(conv_id: str) -> dict[str, Any]:
        return convs.persist(conv_id).public()

    @api.post("/conversations/{conv_id}/remember")
    async def remember(conv_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        conv = convs.get(conv_id)
        ctx = convs.harness_context(conv)
        mode = conv.memory_mode if conv.memory_mode != "none" else "conversation"
        access = MemoryAccess(mode, memory_roots(ctx))
        scope = body.get("scope") or access.scopes[-1]
        text = body["text"]
        item = access.store(scope).write(body.get("name") or text.strip().splitlines()[0][:40], text,
                                         description=body.get("description", ""), type=body.get("type", "fact"),
                                         source=f"conv:{conv_id}")
        return item.model_dump()

    @api.post("/conversations/{conv_id}/memory/propose")
    async def propose_memory(conv_id: str) -> list[dict[str, Any]]:
        from ..memory.consolidate import propose_updates, transcript_text

        conv = convs.get(conv_id)
        if conv.memory_mode == "none":
            return []
        ctx = convs.harness_context(conv)
        access = MemoryAccess(conv.memory_mode, memory_roots(ctx))
        model = state.builder.model_for(state.builder.harnesses.get(conv.harness_id), ctx)
        ops = await propose_updates(state.builder.provider_for(model), access,
                                    transcript_text(convs.transcript(conv_id)), workspace=ctx.workspace)
        return [o.model_dump() for o in ops]

    @api.post("/conversations/{conv_id}/memory/apply")
    async def apply_memory(conv_id: str, body: dict[str, Any] = Body(...)) -> list[dict[str, Any]]:
        from ..memory.consolidate import MemoryOp, apply_ops

        conv = convs.get(conv_id)
        access = MemoryAccess(conv.memory_mode, memory_roots(convs.harness_context(conv)))
        ops = [MemoryOp(**o) for o in body.get("ops", [])]
        return [i.model_dump() for i in apply_ops(access, ops, source=f"conv:{conv_id}")]

    # ---- interactions & events ----------------------------------------------------------
    @api.get("/interactions")
    async def interactions(topic: str | None = None) -> list[dict[str, Any]]:
        return _dump(services.interactions.pending(topic))

    @api.post("/interactions/{interaction_id}")
    async def resolve_interaction(interaction_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if not services.interactions.resolve(interaction_id, body):
            raise KeyError(interaction_id)
        return {"ok": True}

    @api.get("/events")
    async def events(topic: str, after: int = 0, limit: int = 5000) -> list[dict[str, Any]]:
        return services.db.list_events(topic, after, limit)

    # ---- harness ------------------------------------------------------------------------------
    @api.get("/harnesses")
    async def list_harnesses() -> list[dict[str, Any]]:
        return _dump(state.builder.harnesses.list())

    @api.get("/harness-groups")
    async def harness_groups() -> list[dict[str, Any]]:
        groups = tool_groups()
        out = [{"id": g, "label": GROUP_LABELS[g],
                "tools": [{"name": t.name, "description": t.description.split("\n")[0][:160], "category": t.category}
                          for t in groups.get(g, [])]} for g in ALL_GROUPS]
        return out

    @api.post("/harnesses")
    async def create_harness(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        body.pop("builtin", None)
        return _dump(state.builder.harnesses.save(HarnessProfile(**body)))

    @api.put("/harnesses/{harness_id}")
    async def update_harness(harness_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        current = state.builder.harnesses.get(harness_id)
        merged = {**current.model_dump(), **body, "id": harness_id}
        return _dump(state.builder.harnesses.save(HarnessProfile(**merged)))

    @api.delete("/harnesses/{harness_id}")
    async def delete_harness(harness_id: str) -> dict[str, Any]:
        state.builder.harnesses.reset(harness_id)
        return {"ok": True}

    @api.post("/harnesses/{harness_id}/preview")
    async def preview_harness(harness_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        profile = state.builder.harnesses.get(harness_id)
        if body.get("profile"):
            profile = HarnessProfile(**{**profile.model_dump(), **body["profile"]})
        if body.get("conversation_id"):
            ctx = convs.harness_context(convs.get(body["conversation_id"]))
        else:
            project = services.projects.get(body["project_id"]) if body.get("project_id") else None
            workspace = project.workspace if project else app_data_dir() / "preview"
            workspace.mkdir(parents=True, exist_ok=True)
            ctx = HarnessContext(workspace=workspace, project=project,
                                 memory_mode=body.get("memory_mode") or profile.memory_mode,
                                 model_override=body.get("model_id"))
        return state.builder.context_preview(profile, ctx)

    # ---- loops & runs --------------------------------------------------------------------------
    loops = state.engine.loops
    runs = state.engine.runs

    @api.get("/loop-node-types")
    async def node_types() -> dict[str, Any]:
        return NODE_TYPE_INFO

    @api.get("/loops")
    async def list_loops() -> list[dict[str, Any]]:
        return [{"id": g.id, "name": g.name, "description": g.description, "category": g.category,
                 "builtin": g.builtin, "version": g.version, "nodes": len(g.nodes)} for g in loops.list()]

    @api.get("/loops/{loop_id}")
    async def get_loop(loop_id: str) -> dict[str, Any]:
        graph = loops.get(loop_id)
        if graph is None:
            raise KeyError(loop_id)
        return {**_dump(graph), "issues": _dump(validate_graph(graph))}

    @api.post("/loops/validate")
    async def validate_loop(body: dict[str, Any] = Body(...)) -> list[dict[str, Any]]:
        return _dump(validate_graph(LoopGraph(**body)))

    @api.post("/loops")
    async def create_loop(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        body.pop("builtin", None)
        graph = LoopGraph(**body)
        return _dump(loops.save(graph, note=body.get("note", "")))

    @api.put("/loops/{loop_id}")
    async def save_loop(loop_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        note = body.pop("note", "")
        body["id"] = loop_id
        return _dump(loops.save(LoopGraph(**body), note=note))

    @api.post("/loops/{loop_id}/duplicate")
    async def duplicate_loop(loop_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        return _dump(loops.duplicate(loop_id, body.get("name")))

    @api.delete("/loops/{loop_id}")
    async def delete_loop(loop_id: str) -> dict[str, Any]:
        return {"deleted": loops.delete(loop_id)}

    @api.get("/loops/{loop_id}/versions")
    async def loop_versions(loop_id: str) -> list[dict[str, Any]]:
        return loops.versions(loop_id)

    @api.get("/loops/{loop_id}/yaml", response_class=PlainTextResponse)
    async def export_yaml(loop_id: str) -> str:
        graph = loops.get(loop_id)
        if graph is None:
            raise KeyError(loop_id)
        return yaml.safe_dump(graph.model_dump(mode="json", exclude={"builtin"}), allow_unicode=True, sort_keys=False)

    @api.post("/loops/import")
    async def import_yaml(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        data = yaml.safe_load(body["yaml"])
        data.pop("builtin", None)
        graph = LoopGraph(**data)
        if loops.get(graph.id) is not None and loops.get(graph.id).builtin:
            graph.id = f"{graph.id}-copy"
        return _dump(loops.save(graph, note="导入"))

    @api.get("/runs")
    async def list_runs(project_id: str | None = None) -> list[dict[str, Any]]:
        return [r.summary() for r in runs.list(project_id)]

    @api.post("/runs")
    async def create_run(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        graph = LoopGraph(**body["graph"]) if body.get("graph") else loops.get(body["loop_id"])
        if graph is None:
            raise KeyError(body.get("loop_id"))
        project = services.projects.get(body["project_id"])
        if project is None:
            raise KeyError(body["project_id"])
        run = state.engine.create_run(graph, project, body.get("inputs") or {}, auto_mode=bool(body.get("auto_mode")),
                                      max_cost_usd=body.get("max_cost_usd"), model_id=body.get("model_id"),
                                      conversation_id=body.get("conversation_id"))
        state.engine.start(run.id)
        return run.summary()

    @api.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run = runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return {**run.summary(), "graph": _dump(run.graph), "nodes": run.node_status(),
                "records": {k: _dump(v) for k, v in run.records.items()}, "metrics": run.metrics,
                "waiting": run.waiting, "pending": _dump(services.interactions.pending(f"run:{run_id}"))}

    @api.post("/runs/{run_id}/pause")
    async def pause_run(run_id: str) -> dict[str, Any]:
        state.engine.pause(run_id)
        return {"ok": True}

    @api.post("/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict[str, Any]:
        state.engine.start(run_id)
        return {"ok": True}

    @api.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        state.engine.cancel(run_id)
        return {"ok": True}

    @api.delete("/runs/{run_id}")
    async def delete_run(run_id: str) -> dict[str, Any]:
        state.engine.cancel(run_id)
        return {"deleted": runs.delete(run_id)}

    # ---- skills, MCP, memory -----------------------------------------------------------------
    from .routes_extra import register_extra

    register_extra(api, state)
    app.include_router(api)

    # ---- websocket --------------------------------------------------------------------------------
    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        if state.token is not None and websocket.query_params.get("token") != state.token:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        topics = [t for t in (websocket.query_params.get("topics") or "").split(",") if t]
        sub = services.bus.subscribe(topics or None)

        async def reader() -> None:
            nonlocal sub
            while True:
                data = json.loads(await websocket.receive_text())
                if "topics" in data:
                    sub.close()
                    sub = services.bus.subscribe([t for t in data["topics"] if t] or None)

        read_task = asyncio.create_task(reader())
        try:
            while True:
                get_task = asyncio.create_task(sub.queue.get())
                done, _ = await asyncio.wait({get_task, read_task}, return_when=asyncio.FIRST_COMPLETED)
                if read_task in done:
                    get_task.cancel()
                    read_task.result()
                    break
                await websocket.send_text(json.dumps(get_task.result().to_json(), ensure_ascii=False, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            read_task.cancel()
            sub.close()

    return app


def build_app(token: str | None = None, *, data_db: Any = None, provider_factory: Callable | None = None) -> FastAPI:
    services = AppServices(db=data_db)
    services.models.ensure_env_profiles()
    if services.db.get_setting("demo_mode"):
        os.environ["A4S_FAKE_LLM"] = "1"
    return create_app(ServerState(services, token, provider_factory))


__all__ = ["ServerState", "build_app", "create_app", "MODE_SCOPES", "SCOPE_LABELS", "os"]
