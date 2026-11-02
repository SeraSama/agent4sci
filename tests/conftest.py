import os
from pathlib import Path

import pytest

from agent4sci.core import secrets


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test gets its own app-data dir and an in-memory secret store."""
    monkeypatch.setenv("A4S_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("A4S_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("A4S_SECRETS", "memory")
    monkeypatch.setenv("A4S_SKIP_SHELL_ENV", "1")
    monkeypatch.delenv("A4S_FAKE_LLM", raising=False)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    secrets.reset_secret_store()
    yield
    secrets.reset_secret_store()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws
