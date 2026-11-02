"""Research projects = a workspace folder + a DB record."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .db import Database
from .envfix import IS_WINDOWS, find_executable, venv_python
from .paths import default_projects_dir

WORKSPACE_DIRS = ["plan", "lit", "data", "code", "code/eval", "results", "figures", "paper", "notes", "review",
                  "skills/installed", "skills/learned", "loops"]
# Where the app itself puts things; always created, whatever the user picked.
REQUIRED_DIRS = ["skills/installed", "skills/learned", "loops"]
# A sensible starting set for a writing-up project; the tools mkdir anything else on demand.
RECOMMENDED_DIRS = ["plan", "lit", "notes", "results", "figures", "paper"]

GITIGNORE = """\
# agent4sci defaults
.venv/
.a4s/cache/
.a4s/outputs/
.a4s/jobs/
.a4s/tmp/
data/
lit/pdfs/
*.pdf
*.npy
*.npz
*.h5
*.hdf5
*.nwb
*.nii
*.nii.gz
*.fif
*.edf
*.pt
*.ckpt
*.safetensors
__pycache__/
.ipynb_checkpoints/
.DS_Store
"""

BASE_PACKAGES = ["numpy", "pandas", "scipy", "matplotlib", "ipykernel"]


class ProjectSettings(BaseModel):
    trusted: bool = False  # load .agents/skills and .claude/skills from the workspace
    env_passthrough: list[str] = Field(default_factory=lambda: ["NCBI_API_KEY"])
    auto_commit: bool = True
    default_memory_mode: Literal["global", "project", "conversation", "none"] = "project"
    mcp_servers: list[str] | None = None  # None = all globally enabled servers


class Project(BaseModel):
    id: str = Field(default_factory=lambda: "p_" + uuid.uuid4().hex[:10])
    name: str
    path: str
    description: str = ""
    domains: list[str] = Field(default_factory=list)
    created: float = Field(default_factory=time.time)
    settings: ProjectSettings = Field(default_factory=ProjectSettings)

    @property
    def workspace(self) -> Path:
        return Path(self.path)


def _windows_memory_gb() -> float | None:
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    return round(status.ullTotalPhys / 2**30, 1)


def detect_compute() -> dict[str, Any]:
    info: dict[str, Any] = {"os": platform.system(), "machine": platform.machine(), "cpus": os.cpu_count()}
    try:
        if platform.system() == "Darwin":
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", timeout=5).stdout)
            info["memory_gb"] = round(mem / 2**30, 1)
            chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=5).stdout.strip()
            info["chip"] = chip
            info["accelerator"] = "Apple MPS" if platform.machine() == "arm64" else "none"
        elif platform.system() == "Windows":
            info["memory_gb"] = _windows_memory_gb()
            info["chip"] = platform.processor()
        elif hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            info["memory_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        pass
    nvidia = find_executable("nvidia-smi")
    if nvidia:
        try:
            out = subprocess.run([nvidia, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=10).stdout.strip()
            if out:
                info["accelerator"] = "CUDA: " + out.replace("\n", "; ")
        except (OSError, subprocess.SubprocessError):
            pass
    return info


def compute_summary(info: dict[str, Any]) -> str:
    parts = [f"{info.get('os')} {info.get('machine')}", f"{info.get('cpus')} CPU cores"]
    if info.get("memory_gb"):
        parts.append(f"{info['memory_gb']} GB RAM")
    if info.get("chip"):
        parts.append(str(info["chip"]))
    parts.append(f"accelerator: {info.get('accelerator', 'none')}")
    return ", ".join(parts)


def init_workspace(path: Path, name: str, description: str = "", dirs: list[str] | None = None) -> None:
    """Create the workspace. ``dirs`` picks which subfolders to make up front (default: all of them)."""
    path.mkdir(parents=True, exist_ok=True)
    wanted = WORKSPACE_DIRS if dirs is None else [*dirs, *REQUIRED_DIRS]
    for d in wanted:
        if d in WORKSPACE_DIRS:  # never let a caller-supplied name escape the workspace
            (path / d).mkdir(parents=True, exist_ok=True)
    (path / ".a4s").mkdir(exist_ok=True)
    if not (path / ".gitignore").exists():
        (path / ".gitignore").write_text(GITIGNORE, "utf-8")
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {name}\n\n{description}\n\n由 Agent4Sci 创建的研究工作区。\n", "utf-8")
    (path / ".a4s" / "environment.json").write_text(json.dumps(detect_compute(), ensure_ascii=False, indent=2), "utf-8")
    git = find_executable("git")
    if git and not (path / ".git").exists():
        subprocess.run([git, "init", "-q"], cwd=path, capture_output=True)


def project_environment(path: Path) -> dict[str, Any]:
    env_file = path / ".a4s" / "environment.json"
    try:
        return json.loads(env_file.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return detect_compute()


class ProjectStore:
    KIND = "project"

    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[Project]:
        return [Project(**d) for d in self.db.list(self.KIND)]

    def get(self, project_id: str) -> Project | None:
        data = self.db.get(self.KIND, project_id)
        return Project(**data) if data else None

    def save(self, project: Project) -> Project:
        self.db.put(self.KIND, project.id, project.model_dump(mode="json"))
        return project

    def create(self, name: str, path: str | None = None, description: str = "",
               domains: list[str] | None = None, dirs: list[str] | None = None) -> Project:
        folder = Path(path).expanduser() if path else default_projects_dir() / _safe_folder(name)
        init_workspace(folder, name, description, dirs)
        project = Project(name=name, path=str(folder.resolve()), description=description, domains=domains or [])
        (folder / ".a4s" / "project.json").write_text(project.model_dump_json(indent=2), "utf-8")
        return self.save(project)

    def open_existing(self, path: str) -> Project:
        folder = Path(path).expanduser().resolve()
        for p in self.list():
            if Path(p.path) == folder:
                return p
        marker = folder / ".a4s" / "project.json"
        if marker.exists():
            project = Project(**json.loads(marker.read_text("utf-8")))
            project.path = str(folder)
        else:
            init_workspace(folder, folder.name)
            project = Project(name=folder.name, path=str(folder))
        return self.save(project)

    def delete(self, project_id: str) -> bool:
        return self.db.delete(self.KIND, project_id)


def _safe_folder(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "-_ " else "-" for ch in name).strip().replace(" ", "-")
    return keep or f"project-{uuid.uuid4().hex[:6]}"


async def create_venv(workspace: Path, packages: list[str] | None = None,
                      progress: Any = None) -> dict[str, Any]:
    """Create ``.venv`` with uv (downloads a Python if needed) and install base packages."""
    uv = find_executable("uv")
    if uv is None:
        return {"ok": False, "error": "没有找到 uv（安装包会自带；开发环境请安装 uv）"}
    # --seed puts pip in the venv, so a plain `pip install` from the agent lands there too
    steps = [[uv, "venv", "--seed", "--python", "3.12", str(workspace / ".venv")]]
    pkgs = packages or BASE_PACKAGES
    python = str(workspace / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python"))
    steps.append([uv, "pip", "install", "--python", python, *pkgs])
    log = []
    for argv in steps:
        if progress:
            progress(" ".join(argv[1:4]))
        proc = await asyncio.create_subprocess_exec(*argv, cwd=str(workspace), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        log.append(out.decode(errors="replace")[-2000:])
        if proc.returncode != 0:
            return {"ok": False, "error": log[-1]}
    return {"ok": venv_python(workspace) is not None, "python": python, "packages": pkgs}
