"""Static security scan for third-party skills (run before install and on update).

Motivation: in early 2026 hundreds of malicious skills appeared on public skill
registries, mostly hiding a payload behind fake "prerequisite" install steps or
prompt-injection text. This scanner flags the common patterns; it is a filter,
not a guarantee, so unreviewed skills also get runtime approval gates.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high"]
_RANK = {"low": 0, "medium": 1, "high": 2}

TEXT_SUFFIXES = {".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ""}
SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".bat", ".cmd", ".rb", ".pl",
                   ".r", ".R", ".jl", ".lua", ".php"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".whl", ".egg"}
MAX_FILE_BYTES = 5_000_000
MAX_TOTAL_BYTES = 50_000_000


class Finding(BaseModel):
    severity: Severity
    rule: str
    file: str
    line: int | None = None
    message: str
    excerpt: str = ""


class ScanReport(BaseModel):
    risk: Severity = "low"
    findings: list[Finding] = Field(default_factory=list)
    files: int = 0
    bytes: int = 0
    external: str | None = None

    def summary(self) -> dict:
        counts = {s: sum(1 for f in self.findings if f.severity == s) for s in ("high", "medium", "low")}
        return {"risk": self.risk, "counts": counts, "files": self.files}


Rule = tuple[str, Severity, re.Pattern[str], str]

TEXT_RULES: list[Rule] = [
    ("prompt-injection", "high", re.compile(
        r"ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts|rules)|disregard (the |your )?"
        r"(system|previous) (prompt|instructions)|you are now (in )?(dan|developer mode|jailbroken)|"
        r"do not (tell|inform|show) the user|without (telling|informing) the user|忽略(之前|以上|所有)的?(指令|提示)", re.I),
     "提示注入话术"),
    ("hidden-unicode", "high", re.compile("[​-‏‪-‮⁠-⁤﻿]"), "隐藏的 Unicode 控制字符"),
    ("activation-shell", "medium", re.compile(r"!`[^`\n]+`"), "Claude Code 激活期 shell 语法（本框架不会执行）"),
    ("pipe-to-shell", "high", re.compile(r"(curl|wget)\s[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b|iwr\s[^\n|]*\|\s*iex", re.I),
     "“安装步骤”里下载后直接执行脚本"),
    ("encrypted-archive", "high", re.compile(r"unzip\s+-P\s|7z\s+x\s[^\n]*-p\S|password[- ]protected (zip|archive)", re.I),
     "要求解压加密压缩包（常见的恶意载荷投放方式）"),
    ("credential-request", "medium", re.compile(
        r"(paste|enter|provide|send|upload)\s+(your\s+)?(api[_ -]?key|password|private key|seed phrase|token)", re.I),
     "索要密钥/口令"),
    ("remote-binary", "high", re.compile(r"(curl|wget)\s[^\n]*\s-o\s*\S+\s*(&&|;)\s*(chmod\s+\+x|\./)", re.I),
     "下载二进制并执行"),
]

SCRIPT_RULES: list[Rule] = [
    ("pipe-to-shell", "high", re.compile(r"(curl|wget)\s[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", re.I), "下载后直接执行脚本"),
    ("decode-exec", "high", re.compile(
        r"(exec|eval)\s*\([^\n]*(b64decode|base64|fromhex|codecs\.decode|zlib\.decompress|marshal\.loads)", re.I),
     "解码后执行（混淆代码）"),
    ("remote-exec", "high", re.compile(r"(exec|eval)\s*\(\s*(requests|urllib|httpx)\.[^\n]*\.(text|read|content)", re.I),
     "执行远程下载的代码"),
    ("credential-access", "high", re.compile(
        r"\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.netrc|security\s+find-(generic|internet)-password|"
        r"Login Data|logins\.json|Cookies\.binarycookies|\.kube/config|keychain", re.I),
     "读取凭据/密钥/浏览器数据"),
    ("persistence", "high", re.compile(r"launchctl\s+load|LaunchAgents|crontab\s|schtasks\s|"
                                       r"CurrentVersion\\\\?Run|systemctl\s+enable", re.I), "创建持久化（开机自启/定时任务）"),
    ("destructive", "high", re.compile(r"rm\s+-rf\s+(/|~|\$HOME)(\s|$|/\s)|shutil\.rmtree\(\s*(os\.path\.expanduser\(\s*['\"]~|['\"]/['\"])",
                                       re.I), "删除根目录/家目录"),
    ("raw-ip", "medium", re.compile(r"https?://\d{1,3}(\.\d{1,3}){3}"), "直连裸 IP 地址"),
    ("miner", "high", re.compile(r"xmrig|stratum\+tcp|coinhive|cryptonight", re.I), "挖矿程序特征"),
    ("sudo", "medium", re.compile(r"\bsudo\s"), "使用 sudo"),
    ("env-exfil", "medium", re.compile(r"(os\.environ|process\.env|getenv)[^\n]*(post|send|upload|webhook)", re.I),
     "疑似外传环境变量"),
]

_MAGIC = {b"\x7fELF": "ELF", b"MZ": "PE", b"\xcf\xfa\xed\xfe": "Mach-O", b"\xce\xfa\xed\xfe": "Mach-O",
          b"\xca\xfe\xba\xbe": "Mach-O universal"}
_LONG_B64 = re.compile(r"[A-Za-z0-9+/=]{400,}")


# Well-known official installer scripts. Piping them to a shell is still flagged, but as a warning instead of a
# block: legitimate skills often document e.g. the uv or rustup installer, while malicious "prerequisite" steps
# fetch from arbitrary hosts, paste sites or raw IPs.
OFFICIAL_INSTALLERS = ("astral.sh", "sh.rustup.rs", "rclone.org", "get.docker.com", "deno.land", "bun.sh",
                       "ollama.com", "install.python-poetry.org", "pyenv.run", "get.pnpm.io", "pixi.sh",
                       "micro.mamba.pm", "raw.githubusercontent.com/Homebrew/")
_URL = re.compile(r"https?://([^\s'\"|)]+)", re.I)


def _official_installer(line: str) -> str | None:
    """The installer host when every URL on the line is a known official installer, else None."""
    urls = _URL.findall(line)
    if not urls:
        return None
    hosts = []
    for url in urls:
        host, _, path = url.partition("/")
        host = host.lower()
        allowed = False
        for entry in OFFICIAL_INSTALLERS:
            entry_host, _, entry_path = entry.partition("/")
            same_host = host == entry_host or (not entry_path and host.endswith("." + entry_host))
            if same_host and path.startswith(entry_path):
                allowed = True
                break
        if not allowed:
            return None
        hosts.append(host)
    return hosts[0]


def _scan_lines(text: str, rules: list[Rule], rel: str, findings: list[Finding]) -> None:
    for lineno, line in enumerate(text.splitlines(), 1):
        for rule, severity, pattern, message in rules:
            match = pattern.search(line)
            if match:
                host = _official_installer(line) if rule == "pipe-to-shell" else None
                if host:
                    severity, message = "medium", f"{message}（官方安装脚本 {host}，请确认后再执行）"
                findings.append(Finding(severity=severity, rule=rule, file=rel, line=lineno, message=message,
                                        excerpt=line.strip()[:200]))
        if len(line) > 400 and _LONG_B64.search(line):
            findings.append(Finding(severity="medium", rule="obfuscated-blob", file=rel, line=lineno,
                                    message="很长的 base64/编码数据块", excerpt=line.strip()[:80] + "…"))


def scan_skill(directory: Path, *, external: bool = True) -> ScanReport:
    report = ScanReport()
    root = directory.resolve()
    findings: list[Finding] = []
    for path in sorted(directory.rglob("*")):
        rel = path.relative_to(directory).as_posix()
        if ".git" in path.parts:
            continue
        if path.is_symlink():
            target = path.resolve()
            escaping = root not in target.parents and target != root
            findings.append(Finding(severity="high" if escaping else "low", rule="symlink", file=rel,
                                    message="符号链接指向技能目录之外" if escaping else "符号链接"))
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        report.files += 1
        report.bytes += size
        if size > MAX_FILE_BYTES:
            findings.append(Finding(severity="medium", rule="large-file", file=rel, message=f"文件过大（{size} 字节）"))
            continue
        suffix = path.suffix.lower()
        head = path.read_bytes()[:4]
        magic = next((kind for sig, kind in _MAGIC.items() if head.startswith(sig)), None)
        if magic and suffix not in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico"}:
            findings.append(Finding(severity="high", rule="binary-executable", file=rel, message=f"包含可执行文件（{magic}）"))
            continue
        if suffix in ARCHIVE_SUFFIXES:
            findings.append(Finding(severity="medium", rule="archive", file=rel, message="包含压缩包（内容无法静态检查）"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if suffix in SCRIPT_SUFFIXES or text.startswith("#!"):
            _scan_lines(text, SCRIPT_RULES, rel, findings)
            _scan_lines(text, [r for r in TEXT_RULES if r[0] in ("prompt-injection", "hidden-unicode")], rel, findings)
        elif suffix in TEXT_SUFFIXES:
            _scan_lines(text, TEXT_RULES, rel, findings)
    if report.bytes > MAX_TOTAL_BYTES:
        findings.append(Finding(severity="medium", rule="large-skill", file=".", message=f"技能总大小 {report.bytes} 字节"))
    if external:
        report.external = _external_scan(directory, findings)
    report.findings = findings
    report.risk = max((f.severity for f in findings), key=lambda s: _RANK[s], default="low")
    return report


def _external_scan(directory: Path, findings: list[Finding]) -> str | None:
    """Merge results from cisco-ai-skill-scanner when it is installed (best effort)."""
    exe = shutil.which("skill-scanner")
    if exe is None or os.environ.get("A4S_NO_EXTERNAL_SCAN"):
        return None
    try:
        out = subprocess.run([exe, "scan", str(directory), "--format", "json"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return "skill-scanner 运行失败"
    for item in data.get("findings", []) if isinstance(data, dict) else []:
        sev = str(item.get("severity", "medium")).lower()
        findings.append(Finding(severity=sev if sev in _RANK else "medium", rule=f"cisco:{item.get('rule_id', '?')}",
                                file=str(item.get("file", "")), message=str(item.get("message", ""))[:300]))
    return "cisco-ai-skill-scanner"
