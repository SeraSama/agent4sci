"""Export and import the app's configuration as one file.

Loop graphs were already portable, but the Harness profiles they reference were not, so a loop exported to
another machine could not run. A bundle carries the whole set: models (without their keys), harnesses, loops,
field packs, MCP servers and the role→model mapping.

API keys are never included — they live in the OS keychain. `models_needing_keys` says which ones to fill in
after importing.
"""

from __future__ import annotations

import time
from typing import Any

from ..agent.harness import HarnessProfile, HarnessStore, builtin_harnesses, domain_path, list_domains
from .paths import user_domains_dir

FORMAT = 1
KIND = "agent4sci-config"


def export_config(app: Any) -> dict[str, Any]:
    builtin_ids = {h.id for h in builtin_harnesses()}
    store = HarnessStore(app.db)
    # custom profiles, plus any built-in the user has overridden (those are stored rows too)
    harnesses = [h.model_dump(mode="json") for h in store.list()
                 if h.id not in builtin_ids or app.db.get("harness", h.id) is not None]
    loops = [g for g in (app.db.list("loop") or []) if not g.get("builtin")]
    domains = [{"id": d["id"], "source": (domain_path(d["id"], user=True).read_text("utf-8")
                                          if domain_path(d["id"], user=True).exists() else "")}
               for d in list_domains() if not d.get("builtin")]
    models = []
    needing_keys = []
    for profile in app.models.list():
        data = profile.model_dump(mode="json")
        data.pop("has_key", None)
        models.append(data)
        if profile.has_key:
            needing_keys.append(profile.name)
    return {
        "kind": KIND,
        "format": FORMAT,
        "created": time.time(),
        "models": models,
        "models_needing_keys": needing_keys,
        "harnesses": harnesses,
        "loops": loops,
        "domains": domains,
        "mcp": [c.model_dump(mode="json") for c in app.mcp_configs()],
        "roles": app.models.role_models(),
        "default_model": app.models.default_id(),
        "settings": {k: app.db.get_setting(k) for k in ("contact_email", "language")
                     if app.db.get_setting(k) is not None},
    }


def _apply(app: Any, kind: str, items: list[dict[str, Any]], id_key: str, overwrite: bool,
           report: dict[str, list[str]]) -> None:
    for item in items:
        ident = str(item.get(id_key) or "")
        if not ident:
            continue
        exists = app.db.get(kind, ident) is not None
        if exists and not overwrite:
            report["skipped"].append(f"{kind}:{ident}")
            continue
        app.db.put(kind, ident, item)
        report["replaced" if exists else "added"].append(f"{kind}:{ident}")


def import_config(app: Any, data: dict[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    if data.get("kind") != KIND:
        raise ValueError("这不是 Agent4Sci 的配置包")
    if int(data.get("format", 0)) > FORMAT:
        raise ValueError("配置包来自更新版本的 Agent4Sci，请先升级")
    report: dict[str, list[str]] = {"added": [], "replaced": [], "skipped": []}

    for profile in data.get("models") or []:
        profile = {**profile, "has_key": False}  # the key stays on the machine that had it
        _apply(app, "model", [profile], "id", overwrite, report)
    for harness in data.get("harnesses") or []:
        HarnessProfile(**harness)  # validate before storing: a broken harness breaks every chat using it
        _apply(app, "harness", [harness], "id", overwrite, report)
    _apply(app, "loop", list(data.get("loops") or []), "id", overwrite, report)
    _apply(app, "mcp", list(data.get("mcp") or []), "name", overwrite, report)

    for domain in data.get("domains") or []:
        ident, source = str(domain.get("id") or ""), str(domain.get("source") or "")
        if not ident or not source:
            continue
        path = domain_path(ident, user=True)
        if path.exists() and not overwrite:
            report["skipped"].append(f"domain:{ident}")
            continue
        user_domains_dir().mkdir(parents=True, exist_ok=True)
        report["replaced" if path.exists() else "added"].append(f"domain:{ident}")
        path.write_text(source, "utf-8")

    if data.get("roles"):
        app.models.set_role_models({k: v for k, v in data["roles"].items() if v})
    for key, value in (data.get("settings") or {}).items():
        if value is not None:
            app.db.set_setting(key, value)
    return {**report, "models_needing_keys": data.get("models_needing_keys") or []}
