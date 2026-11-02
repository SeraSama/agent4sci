"""API-key storage.

Keys go to the OS credential store through ``keyring`` (macOS Keychain, Windows
Credential Manager). When no usable keyring exists we fall back to a JSON file
with owner-only permissions. ``A4S_SECRETS=memory|file|keyring`` forces a backend
(tests use ``memory``).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .paths import app_data_dir

SERVICE = "agent4sci"


class SecretStore:
    name = "base"

    def get(self, key: str) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError

    def set(self, key: str, value: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def delete(self, key: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class MemorySecretStore(SecretStore):
    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class FileSecretStore(SecretStore):
    name = "file"

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            data = self._load()
            data[key] = value
            self._save(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._load()
            if data.pop(key, None) is not None:
                self._save(data)


class KeyringSecretStore(SecretStore):
    name = "keyring"

    def __init__(self) -> None:
        import keyring

        self._keyring = keyring

    def get(self, key: str) -> str | None:
        return self._keyring.get_password(SERVICE, key)

    def set(self, key: str, value: str) -> None:
        self._keyring.set_password(SERVICE, key, value)

    def delete(self, key: str) -> None:
        try:
            self._keyring.delete_password(SERVICE, key)
        except Exception:  # keyring raises PasswordDeleteError when missing
            pass


_store: SecretStore | None = None
_store_lock = threading.Lock()


def _keyring_usable() -> bool:
    try:
        import keyring
        from keyring.backends import fail

        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


def get_secret_store() -> SecretStore:
    global _store
    with _store_lock:
        if _store is not None:
            return _store
        backend = os.environ.get("A4S_SECRETS", "keyring").lower()
        if backend == "memory":
            _store = MemorySecretStore()
        elif backend == "file" or not _keyring_usable():
            _store = FileSecretStore(app_data_dir() / "secrets.json")
        else:
            _store = KeyringSecretStore()
        return _store


def reset_secret_store() -> None:
    """Forget the cached backend (used by tests after changing env vars)."""
    global _store
    with _store_lock:
        _store = None


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"
