"""Decode text files no matter which tool wrote them.

Agents and user commands write workspace files through many tools. On Windows, PowerShell 5.1's ``>`` writes
UTF-16 with a byte-order mark and Out-File/Set-Content may add a UTF-8 BOM, so every place that reads files an
agent or command produced (results, metrics, documents, file views) decodes through here.
"""

from __future__ import annotations

import codecs
from pathlib import Path


def text_encoding(raw: bytes) -> str | None:
    """``utf-8-sig`` or ``utf-16`` when the data starts with a byte-order mark, else None."""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return None


def decode_text(raw: bytes, errors: str = "replace") -> str:
    return raw.decode(text_encoding(raw) or "utf-8", errors)


def read_text(path: Path | str, errors: str = "replace") -> str:
    return decode_text(Path(path).read_bytes(), errors)


def looks_binary(raw: bytes) -> bool:
    """NUL bytes mean binary, except in UTF-16 text (which is full of them)."""
    return text_encoding(raw) != "utf-16" and b"\x00" in raw[:8192]
