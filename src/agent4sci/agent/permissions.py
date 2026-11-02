"""How much the agent asks before acting.

Three modes cover what people actually want, and the per-category matrix stays available as "custom":

- ``ask_all``    every write, command, install and network call asks first; reading and searching do not.
- ``ask_risky``  (default) work inside the project runs freely; anything that leaves the project, installs
                 code or looks dangerous asks. This is the real boundary: the file tools are confined to the
                 workspace, the shell is not.
- ``allow_all``  nothing asks, including the dangerous-command guard. For unattended runs you trust.
"""

from __future__ import annotations

from typing import Literal

PermissionMode = Literal["ask_all", "ask_risky", "allow_all", "custom"]
DEFAULT_MODE: PermissionMode = "ask_risky"

MODE_LABELS: dict[str, str] = {
    "ask_all": "总是询问",
    "ask_risky": "按需提问",
    "allow_all": "全部允许",
    "custom": "自定义",
}

MODE_HINTS: dict[str, str] = {
    "ask_all": "写文件、跑命令、装技能、联网都要你点同意；读取和检索不打扰你。",
    "ask_risky": "项目内的事直接做；命令伸到项目外、安装外部代码、或命中危险命令时才问你。",
    "allow_all": "什么都不问，连危险命令拦截也关掉。只在无人值守且信任该流程时用。",
    "custom": "按工具类别逐项设置（下面的审批策略表）。",
}

# tool category prefix → auto | ask | deny
_PRESETS: dict[str, dict[str, str]] = {
    "ask_all": {"fs.write": "ask", "exec": "ask", "web": "ask", "memory": "ask", "mcp": "ask",
                "skills.install": "ask", "literature.write": "ask", "research.write": "ask", "agent": "ask"},
    "ask_risky": {"skills.install": "ask"},  # plus the shell guard below, which is where the real risk is
    "allow_all": {},
}


def approvals_for(mode: str, custom: dict[str, str] | None = None) -> dict[str, str]:
    if mode == "custom":
        return dict(custom or {})
    return dict(_PRESETS.get(mode, _PRESETS["ask_risky"]))


def guards_shell(mode: str) -> bool:
    """Whether dangerous commands and commands leaving the workspace need a human."""
    return mode != "allow_all"
