"""Process environment for tools: login-shell PATH on macOS, secret scrubbing, workspace venv.

GUI apps on macOS start with a minimal PATH (no Homebrew, no uv), so we ask the
user's login shell for its PATH once, like VS Code does.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|SESSION)", re.I)
_SECRET_PREFIXES = ("ANTHROPIC_", "OPENAI_", "AWS_", "AZURE_", "GOOGLE_APPLICATION", "GH_", "GITHUB_", "HF_", "A4S_")


_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
_LOCAL_HOSTS = "localhost,127.0.0.1,::1,.local"


def apply_system_proxy() -> dict[str, str]:
    """Adopt the OS proxy (macOS System Settings, Windows Internet Options) when the environment has none.

    Apps started from Finder or the Start menu get no HTTP(S)_PROXY variables, so requests from the backend —
    and from uv, git and MCP servers it starts — would bypass a system-wide proxy such as Clash. Local hosts stay
    direct so the desktop app and local models (Ollama) keep working.
    """
    if any(os.environ.get(name) for name in _PROXY_VARS) or os.environ.get("A4S_NO_SYSTEM_PROXY"):
        return {}
    import urllib.request

    try:
        proxies = urllib.request.getproxies()  # reads SystemConfiguration on macOS, the registry on Windows
    except Exception:  # pragma: no cover - broken system settings
        return {}
    applied: dict[str, str] = {}
    for scheme in ("http", "https"):
        url = proxies.get(scheme)
        if url and url.startswith(("http://", "https://")):
            os.environ[f"{scheme.upper()}_PROXY"] = url
            applied[scheme] = url
    if applied:
        extra = proxies.get("no", "")
        os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY", ""), _LOCAL_HOSTS, extra]))
    return applied


@lru_cache(maxsize=1)
def login_shell_path() -> str:
    current = os.environ.get("PATH", "")
    if IS_WINDOWS or os.environ.get("A4S_SKIP_SHELL_ENV"):
        return current
    shell = os.environ.get("SHELL") or ("/bin/zsh" if platform.system() == "Darwin" else "/bin/bash")
    try:
        out = subprocess.run([shell, "-ilc", "printf '__A4S__%s__A4S__' \"$PATH\""], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=8, stdin=subprocess.DEVNULL)
        match = re.search(r"__A4S__(.*?)__A4S__", out.stdout, re.S)
        login = match.group(1) if match else ""
    except (OSError, subprocess.SubprocessError):
        login = ""
    parts: list[str] = []
    for entry in (login.split(os.pathsep) + current.split(os.pathsep) +
                  ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")]):
        if entry and entry not in parts:
            parts.append(entry)
    return os.pathsep.join(parts)


def bundled_bin_dir() -> Path | None:
    """Directory with the uv binary shipped inside the packaged app (if any)."""
    candidates = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates += [base / "bin", Path(sys.executable).parent / "bin"]
    env = os.environ.get("A4S_BIN_DIR")
    if env:
        candidates.insert(0, Path(env))
    for c in candidates:
        if c.is_dir():
            return c
    return None


def venv_dir(workspace: Path) -> Path:
    return workspace / ".venv"


def venv_python(workspace: Path) -> Path | None:
    venv = venv_dir(workspace)
    exe = venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return exe if exe.exists() else None


def find_executable(name: str) -> str | None:
    bundled = bundled_bin_dir()
    if bundled is not None:
        exe = bundled / (name + (".exe" if IS_WINDOWS else ""))
        if exe.exists():
            return str(exe)
    return shutil.which(name, path=login_shell_path())


def scrub_env(env: dict[str, str], passthrough: list[str] | tuple[str, ...] = ()) -> dict[str, str]:
    keep = set(passthrough)
    out = {}
    for name, value in env.items():
        if name in keep:
            out[name] = value
            continue
        upper = name.upper()
        if upper.startswith(_SECRET_PREFIXES) or _SECRET_NAME.search(upper):
            continue
        out[name] = value
    return out


def tool_env(workspace: Path, passthrough: list[str] | tuple[str, ...] = (), extra: dict[str, str] | None = None,
             headless_plots: bool = True) -> dict[str, str]:
    env = scrub_env(dict(os.environ), passthrough)
    path_parts = []
    venv = venv_dir(workspace)
    if venv_python(workspace) is not None:
        path_parts.append(str(venv / ("Scripts" if IS_WINDOWS else "bin")))
        env["VIRTUAL_ENV"] = str(venv)
    bundled = bundled_bin_dir()
    if bundled is not None:
        path_parts.append(str(bundled))
    path_parts.append(login_shell_path())
    env["PATH"] = os.pathsep.join(path_parts)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")  # scripts' open() defaults to UTF-8 even on Windows (GBK locale otherwise)
    # Without a workspace venv, `pip` on PATH belongs to the system / conda Python: refuse instead of changing it
    env.setdefault("PIP_REQUIRE_VIRTUALENV", "1")
    if headless_plots:
        env["MPLBACKEND"] = "Agg"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if extra:
        env.update(extra)
    return env


# Windows PowerShell writes to pipes in the console code page (GBK on Chinese Windows); make its output — and how
# it decodes native programs' output — UTF-8, which is what the tools decode.
POWERSHELL_UTF8 = ("try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; "
                   "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                   # `>`, Out-File, Set-Content, Get-Content: UTF-8 instead of UTF-16 / the ANSI code page
                   "$PSDefaultParameterValues['*:Encoding'] = 'utf8'; ")


def shell_command(command: str) -> tuple[list[str], str]:
    """Return argv that runs ``command`` in the platform shell, plus the shell's display name."""
    if IS_WINDOWS:
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                POWERSHELL_UTF8 + command], "PowerShell"
    bash = shutil.which("bash") or "/bin/bash"
    return [bash, "-c", command], "bash"
