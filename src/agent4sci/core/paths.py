"""Filesystem locations used by agent4sci.

Everything user-specific lives under the platform's app-data directory
(``~/Library/Application Support/agent4sci`` on macOS, ``%LOCALAPPDATA%\\agent4sci``
on Windows). Tests and portable setups can override it with ``A4S_DATA_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_cache_dir, user_data_dir

APP_NAME = "agent4sci"


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_data_dir() -> Path:
    return _ensure(_env_path("A4S_DATA_DIR") or Path(user_data_dir(APP_NAME, appauthor=False)))


def cache_dir() -> Path:
    return _ensure(_env_path("A4S_CACHE_DIR") or Path(user_cache_dir(APP_NAME, appauthor=False)))


def db_path() -> Path:
    return app_data_dir() / "app.db"


def logs_dir() -> Path:
    return _ensure(app_data_dir() / "logs")


def global_memory_dir() -> Path:
    return _ensure(app_data_dir() / "memory" / "global")


def conversation_memory_dir(conversation_id: str) -> Path:
    return _ensure(app_data_dir() / "memory" / "conversations" / conversation_id)


def conversation_files_dir(conversation_id: str) -> Path:
    """Scratch workspace for conversations that are not attached to a project."""
    return _ensure(app_data_dir() / "conversations" / conversation_id)


def user_skills_dir() -> Path:
    return _ensure(app_data_dir() / "skills")


def user_domains_dir() -> Path:
    return _ensure(app_data_dir() / "domains")


def temp_root() -> Path:
    return _ensure(app_data_dir() / "tmp")


def themes_dir() -> Path:
    return _ensure(app_data_dir() / "themes")


def default_projects_dir() -> Path:
    return _env_path("A4S_PROJECTS_DIR") or Path.home() / "Agent4Sci"


def package_dir() -> Path:
    """Directory of the installed ``agent4sci`` package (data files live here too)."""
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    return package_dir().joinpath(*parts)
