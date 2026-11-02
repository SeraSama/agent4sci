"""Environment checks shared by `a4s doctor` and the desktop app's diagnostics panel.

The packaged app ships no CLI, so this is the only way a user can find out why skills will not install or why
a model cannot be reached.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from typing import Any

from .. import __version__
from .envfix import find_executable
from .paths import app_data_dir, default_projects_dir

Check = dict[str, Any]
NETWORK_TARGETS = (
    ("arXiv", "https://export.arxiv.org/api/query?search_query=all:test&max_results=1"),
    ("Crossref", "https://api.crossref.org/works?rows=1"),
    ("GitHub", "https://api.github.com/rate_limit"),
)


def _check(name: str, ok: bool | None, detail: str, hint: str = "") -> Check:
    return {"name": name, "ok": ok, "detail": detail, "hint": hint}


def proxy_display() -> str:
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    return re.sub(r"^\w+://(?:[^@/]*@)?", "", raw)  # never show credentials


def environment_checks(app: Any) -> list[Check]:
    from .app import SERVICE_KEYS

    proxy = proxy_display()
    checks = [
        _check("Agent4Sci", True, __version__),
        _check("Python", sys.version_info >= (3, 11), platform.python_version()),
        _check("系统", True, f"{platform.system()} {platform.release()} ({platform.machine()})"),
        _check("数据目录", os.access(app_data_dir(), os.W_OK), str(app_data_dir()),
               "" if os.access(app_data_dir(), os.W_OK) else "没有写权限，对话和设置都保存不了"),
        _check("项目目录", True, str(default_projects_dir())),
        _check("密钥存储", True, type(app.secrets).__name__),
        _check("网络代理", True if proxy else None, proxy or "未使用（直连；系统代理会自动采用）"),
    ]
    for exe, why in (("uv", "创建项目 Python 环境、运行技能脚本、uvx 启动 MCP"),
                     ("git", "工作区版本记录与回滚"),
                     ("npx", "启动 Node 版 MCP 服务器（可选）")):
        path = find_executable(exe)
        ok: bool | None = bool(path)
        if exe == "npx" and not path:
            ok = None
        checks.append(_check(exe, ok, path or "未找到", "" if path else why))

    models = app.models.list()
    checks.append(_check("模型档案", bool(models), f"{len(models)} 个，默认 {app.models.default_id() or '未设置'}",
                         "" if models else "还没有模型：在「模型」页添加一个并填 API Key"))
    for profile in models:
        key = app.models.key_status(profile)
        no_key_needed = profile.is_local or profile.provider == "fake"
        state = "已配置" if key.get("resolved") else ("无需" if no_key_needed else "缺失")
        checks.append(_check(f"  {profile.name}", bool(key.get("resolved") or no_key_needed),
                             f"{profile.provider} · {profile.model} · Key {state}",
                             "" if key.get("resolved") or no_key_needed else "在「模型」页编辑并填写 API Key"))
    for sid, (env, label) in SERVICE_KEYS.items():
        configured = bool(app.service_key(sid))
        checks.append(_check(f"  {label}", True if configured else None,
                             "已配置" if configured else f"未配置（可选，{env}）"))
    checks.append(_check("联系邮箱", True if app.contact_email() else None,
                         app.contact_email() or "未设置（Crossref / PubMed 礼貌池，可选）"))
    return checks


async def network_checks(app: Any) -> list[Check]:
    out = []
    for label, url in NETWORK_TARGETS:
        try:
            resp = await app.http.get(url, cache_ttl=0, raise_for_status=False)
            out.append(_check(f"网络 {label}", resp.status_code < 400, f"HTTP {resp.status_code}",
                              "" if resp.status_code < 400 else "可能需要代理，或该服务在限流"))
        except Exception as exc:  # noqa: BLE001 - any failure is the answer here
            out.append(_check(f"网络 {label}", False, str(exc)[:160], "检查网络或代理设置"))
    return out


async def run_diagnostics(app: Any, *, network: bool = False) -> dict[str, Any]:
    checks = environment_checks(app)
    if network:
        checks += await network_checks(app)
    return {"checks": checks, "failed": sum(1 for c in checks if c["ok"] is False)}
