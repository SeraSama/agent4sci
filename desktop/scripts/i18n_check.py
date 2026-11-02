"""List Chinese UI strings that have no English translation in src/renderer/src/i18n/en.ts.

Chinese is the source language: UI text is written in Chinese and used as the i18n key. This script collects
  * string literals containing Chinese from the renderer sources (ignoring comments), and
  * backend strings the UI passes through t() (domain packs, catalog labels, loop templates, harness labels),
and prints every one missing from en.ts.  Run:  uv run python desktop/scripts/i18n_check.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REN = ROOT / "desktop/src/renderer/src"
SRC = ROOT / "src/agent4sci"
CJK = re.compile(r"[　-〿一-鿿＀-￯]")
T_CALL = re.compile(r"\bt\(\s*'((?:[^'\\\n]|\\.)*)'|\bt\(\s*\"((?:[^\"\\\n]|\\.)*)\"")
# backend strings that are never shown through t()
IGNORE = re.compile(r"[|()?]{2,}|^记忆路径|^包含隐藏")


def ts_literals(text: str) -> list[str]:
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        c = text[i]
        if c in "'\"`":
            j, buf, has_expr = i + 1, [], False
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    buf.append({"n": "\n", "t": "\t"}.get(text[j + 1], text[j + 1]))
                    j += 2
                    continue
                if c == "`" and text.startswith("${", j):
                    has_expr = True
                buf.append(text[j])
                j += 1
            s = "".join(buf)
            if CJK.search(s) and not has_expr:
                out.append(s)
            i = j + 1
            continue
        i += 1
    return out


def collect() -> dict[str, str]:
    keys: dict[str, str] = {}

    def add(s: object, where: str) -> None:
        if isinstance(s, str) and s and CJK.search(s) and not IGNORE.search(s):
            keys.setdefault(s, where)

    for f in sorted(REN.rglob("*.ts*")):
        if f.name == "en.ts" or ".test." in f.name or "__tests__" in f.parts:
            continue
        text = f.read_text("utf-8")
        for s in ts_literals(text):
            add(s, f.name)
        for m in T_CALL.finditer(text):
            add(m.group(1) if m.group(1) is not None else m.group(2), f.name)
    for y in (SRC / "domains").glob("*.yaml"):
        d = yaml.safe_load(y.read_text("utf-8"))
        add(d.get("label"), y.name)
        add(d.get("description"), y.name)
    cat = yaml.safe_load((SRC / "skills/catalog.yaml").read_text("utf-8"))
    for m in cat.get("mcp_servers", []):
        add(m.get("label"), "catalog.yaml")
        add(m.get("license"), "catalog.yaml")
    for s in cat.get("sources", []):
        add(s.get("description"), "catalog.yaml")
        add(s.get("note"), "catalog.yaml")
    for y in (SRC / "loops/templates").glob("*.yaml"):
        d = yaml.safe_load(y.read_text("utf-8"))
        for k in ("name", "description", "category"):
            add(d.get(k), y.name)
        for node in d.get("nodes", []):
            add(node.get("label"), y.name)
        for e in d.get("edges", []):
            add(e.get("label"), y.name)
        for v in (d.get("inputs") or {}).values():
            add(v.get("label"), y.name)
    for py in ("agent/harness.py", "memory/store.py", "loops/schema.py"):
        for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', (SRC / py).read_text("utf-8")):
            if len(m.group(1)) < 120 and "{" not in m.group(1):
                add(m.group(1), py)
    return keys


def translated() -> set[str]:
    out = set()
    for line in (REN / "i18n/en.ts").read_text("utf-8").splitlines():
        m = re.match(r'\s*("(?:[^"\\]|\\.)*"):', line)
        if m:
            out.add(json.loads(m.group(1)))
    return out


if __name__ == "__main__":
    keys, have = collect(), translated()
    missing = {k: v for k, v in keys.items() if k not in have}
    for k, where in sorted(missing.items(), key=lambda kv: kv[1]):
        print(f"{where}\t{json.dumps(k, ensure_ascii=False)}")
    print(f"{len(keys)} strings, {len(missing)} missing", file=sys.stderr)
    sys.exit(1 if missing else 0)
