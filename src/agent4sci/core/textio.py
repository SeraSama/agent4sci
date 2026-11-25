"""Decode text no matter which tool wrote it.

Agents and user commands produce text through many tools, and on Windows not all of it is UTF-8:

* PowerShell 5.1's ``>`` writes UTF-16 with a byte-order mark, Out-File/Set-Content may add a UTF-8 BOM;
* on a Chinese Windows the ANSI code page is GBK (cp936), so Notepad's "ANSI", Excel's CSV export, and the
  output of native programs that ignore ``chcp`` are GBK bytes, which decoded as UTF-8 are a row of ``�``.

So every place that reads a file or a command's output goes through here: BOM first, then UTF-8 (which is
self-validating, so real UTF-8 is never mistaken for anything else), then the legacy code page.
"""

from __future__ import annotations

import codecs
import locale
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _legacy_candidates() -> tuple[str, ...]:
    """Non-UTF-8 encodings worth trying, most likely first.

    The machine's own ANSI code page comes first (cp936 on a Chinese Windows, cp1252 on a Western one).
    gb18030 follows everywhere, including macOS: a file copied off a Windows machine is still GBK, and
    gb18030 is a superset of GBK/GB2312 that also decodes the rest of Unicode.
    """
    names: list[str] = []
    for name in (locale.getpreferredencoding(False), "mbcs" if sys.platform == "win32" else "", "gb18030"):
        if not name:
            continue
        try:
            codecs.lookup(name)
        except LookupError:
            continue
        canonical = codecs.lookup(name).name
        if canonical in ("utf-8", "ascii") or canonical in names:
            continue
        names.append(canonical)
    return tuple(names)


def text_encoding(raw: bytes) -> str | None:
    """The encoding this data is in when it is *not* plain UTF-8, else None.

    Callers write the value back out again when they edit a file, so a GBK file stays GBK instead of being
    silently converted the first time an agent touches it.
    """
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as err:
        # Callers hand us slices (the first 2 MB of a file, the first 4 KB for a binary check), so a character
        # cut in half at the end is not evidence of another encoding — only a failure earlier on is.
        if err.start >= len(raw) - 4:
            return None
    else:
        return None
    for name in _legacy_candidates():
        try:
            raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
        return name
    return None


def decode_text(raw: bytes, errors: str = "replace") -> str:
    return raw.decode(text_encoding(raw) or "utf-8", errors)


def read_text(path: Path | str, errors: str = "replace") -> str:
    return decode_text(Path(path).read_bytes(), errors)


def looks_binary(raw: bytes) -> bool:
    """NUL bytes mean binary, except in UTF-16 text (which is full of them)."""
    return text_encoding(raw) != "utf-16" and b"\x00" in raw[:8192]
