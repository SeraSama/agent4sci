"""Agent Skills (SKILL.md) parsing and validation — https://agentskills.io/specification.

``strict`` mode enforces the spec (used for skills the agent writes itself);
``lenient`` mode follows the client-implementation guidance for third-party
skills: warn on naming problems, skip only when the description is missing or
the YAML is unreadable, and ignore vendor-specific fields.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STANDARD_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
ACTIVATION_SHELL = re.compile(r"!`[^`\n]+`")
SKILL_FILE_NAMES = ("SKILL.md", "skill.md", "Skill.md")


class SkillParseError(Exception):
    pass


class SkillMeta(BaseModel):
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class ParsedSkill(BaseModel):
    meta: SkillMeta
    body: str
    path: Path
    warnings: list[str] = Field(default_factory=list)

    @property
    def skill_md(self) -> Path:
        return find_skill_file(self.path) or self.path / "SKILL.md"


def find_skill_file(directory: Path) -> Path | None:
    for name in SKILL_FILE_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _lenient_yaml(head: str) -> dict[str, Any]:
    """Fallback for frontmatter that YAML rejects (usually unquoted colons in descriptions)."""
    data: dict[str, Any] = {}
    current_map: dict[str, str] | None = None
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and current_map is not None:
            key, _, value = line.strip().partition(":")
            current_map[key.strip()] = value.strip().strip("'\"")
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if value == "":
            current_map = {}
            data[key] = current_map
        else:
            current_map = None
            data[key] = value.strip("'\"")
    return data


def split_skill_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    warnings: list[str] = []
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        raise SkillParseError("SKILL.md 缺少 YAML frontmatter（应以 --- 开头）")
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise SkillParseError("frontmatter 没有结束标记 ---")
    head = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    try:
        data = yaml.safe_load(head) or {}
        if not isinstance(data, dict):
            raise SkillParseError("frontmatter 不是键值映射")
    except yaml.YAMLError:
        data = _lenient_yaml(head)
        warnings.append("frontmatter 不是合法 YAML，已按宽松规则解析（常见原因：描述中有未加引号的冒号）")
        if not data:
            raise SkillParseError("frontmatter 无法解析")
    return data, body, warnings


def load_skill(directory: Path, *, strict: bool = False) -> ParsedSkill:
    skill_file = find_skill_file(directory)
    if skill_file is None:
        raise SkillParseError(f"{directory} 中没有 SKILL.md")
    if skill_file.name != "SKILL.md" and strict:
        raise SkillParseError("文件名必须是 SKILL.md")
    data, body, warnings = split_skill_frontmatter(skill_file.read_text(encoding="utf-8", errors="replace"))
    errors: list[str] = []

    name = str(data.get("name") or "").strip()
    if not name:
        errors.append("缺少 name")
        name = directory.name
    if len(name) > 64:
        errors.append("name 超过 64 个字符")
    if not NAME_RE.match(name):
        errors.append("name 只能包含小写字母、数字和连字符，且不能以连字符开头/结尾或连续出现")
    if name != directory.name:
        errors.append(f"name（{name}）与目录名（{directory.name}）不一致")

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillParseError("缺少 description")
    description = " ".join(description.split())
    if len(description) > 1024:
        errors.append("description 超过 1024 个字符")
        if not strict:
            description = description[:1021] + "…"

    compatibility = data.get("compatibility")
    if compatibility is not None:
        compatibility = str(compatibility)
        if len(compatibility) > 500:
            errors.append("compatibility 超过 500 个字符")

    metadata_raw = data.get("metadata") or {}
    metadata: dict[str, str] = {}
    if isinstance(metadata_raw, dict):
        for k, v in metadata_raw.items():
            if not isinstance(v, str):
                errors.append(f"metadata.{k} 不是字符串")
            metadata[str(k)] = str(v)
    else:
        errors.append("metadata 必须是键值映射")

    extra = {k: v for k, v in data.items() if k not in STANDARD_FIELDS}
    if extra:
        msg = "非标准字段（厂商扩展，已忽略）: " + ", ".join(sorted(extra))
        (errors if strict else warnings).append(msg)
    if ACTIVATION_SHELL.search(body):
        warnings.append("正文包含 Claude Code 的 !`命令` 激活期 shell 语法；本框架不会执行它")

    if strict and errors:
        raise SkillParseError("; ".join(errors))
    warnings.extend(errors)

    allowed = data.get("allowed-tools")
    meta = SkillMeta(
        name=name, description=description, license=str(data["license"]) if data.get("license") else None,
        compatibility=compatibility, metadata=metadata,
        allowed_tools=" ".join(allowed) if isinstance(allowed, list) else (str(allowed) if allowed else None),
        extra_fields=extra,
    )
    return ParsedSkill(meta=meta, body=body, path=directory, warnings=warnings)


def list_resources(directory: Path, limit: int = 200) -> list[str]:
    out = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name not in SKILL_FILE_NAMES and ".git" not in path.parts:
            out.append(path.relative_to(directory).as_posix())
            if len(out) >= limit:
                break
    return out


def render_skill_md(meta: dict[str, Any], body: str) -> str:
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{head}\n---\n\n{body.strip()}\n"
