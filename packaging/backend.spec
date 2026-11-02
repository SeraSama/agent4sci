# PyInstaller spec for the backend that the desktop app launches (onedir build).
#
#   uv run pyinstaller packaging/backend.spec --noconfirm --distpath build/pyinstaller/dist --workpath build/pyinstaller/work
#
# Output: build/pyinstaller/dist/backend/agent4sci-server[.exe] plus its libraries; electron-builder copies the folder
# to <app resources>/backend and fetch_uv.py adds the uv binary under backend/bin.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 - defined by PyInstaller

# Package data: skill catalog, built-in skills, loop templates, role prompts, domain packs.
datas = collect_data_files("agent4sci", excludes=["**/__pycache__/**", "**/*.pyc"])
datas += [(str(ROOT / "LICENSE"), ".")]
# Packages that read their own metadata at runtime (versions, entry points such as keyring backends).
for dist in ("agent4sci", "keyring", "mcp", "anthropic", "openai", "fastapi", "starlette", "uvicorn", "pydantic",
             "jupyter_client", "jupyter_core", "httpx2", "typer"):
    try:
        datas += copy_metadata(dist)
    except Exception:  # not installed under that distribution name
        pass

hiddenimports = (
    collect_submodules("agent4sci")
    + collect_submodules("uvicorn")
    + collect_submodules("keyring.backends")
    + ["websockets", "websockets.asyncio.server", "anyio._backends._asyncio"]
)
if sys.platform == "win32":
    hiddenimports += collect_submodules("win32ctypes")  # keyring's Windows Credential Manager backend

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "backend_entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "scipy", "IPython", "ipykernel", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    # UTF-8 mode: third-party code that opens files without an encoding must not fall back to GBK on Windows.
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="agent4sci-server",
    console=True,  # keeps stdin/stdout pipes working; Electron starts it with windowsHide
    icon=str(ROOT / "desktop" / "build" / ("icon.ico" if sys.platform == "win32" else "icon.icns")),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="backend", upx=False)  # noqa: F821
