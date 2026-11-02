"""Loop graph model (what the visual editor edits) and validation.

A graph is a set of nodes and edges. Containers (``loop`` and ``parallel``) hold
child nodes via ``parent``. Apart from containers, every scope must be acyclic —
repetition is always explicit, bounded and visible.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

NodeType = Literal[
    "start", "end", "agent", "tool", "command", "human", "condition", "loop", "parallel", "subflow",
    "memory", "distill", "tournament", "checks", "render", "note",
]
CONTAINER_TYPES = ("loop", "parallel")

NODE_TYPE_INFO: dict[str, dict[str, str]] = {
    "start": {"label": "开始", "description": "流程入口"},
    "end": {"label": "结束", "description": "流程出口"},
    "agent": {"label": "Agent", "description": "用某个 Harness 运行一个 Agent（可要求结构化输出）"},
    "tool": {"label": "工具", "description": "直接调用一个工具"},
    "command": {"label": "命令/验证门", "description": "运行命令或脚本，可用退出码或指标作为验证门"},
    "human": {"label": "人工检查点", "description": "批准 / 反馈 / 选择"},
    "condition": {"label": "条件分支", "description": "根据运行状态选择分支"},
    "loop": {"label": "循环", "description": "重复执行子图，直到条件满足或达到上限；支持指标与保留/丢弃"},
    "parallel": {"label": "并行展开", "description": "对列表中的每一项并行执行子图"},
    "subflow": {"label": "子流程", "description": "调用另一个 Loop"},
    "memory": {"label": "记忆", "description": "写入记忆或从运行中整理记忆"},
    "distill": {"label": "技能沉淀", "description": "从运行轨迹提炼可复用技能（待审批）"},
    "tournament": {"label": "想法锦标赛", "description": "生成候选 → 新颖性检索 → 打分 → Elo 两两对比"},
    "checks": {"label": "规则检查", "description": "引用、产物、受保护文件、数字溯源"},
    "render": {"label": "渲染文档", "description": "用结果文件填充论文中的数字占位符"},
    "note": {"label": "注释", "description": "说明文字（不执行）"},
}


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class Node(BaseModel):
    id: str
    type: NodeType
    label: str = ""
    position: Position = Field(default_factory=Position)
    parent: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    width: float | None = None
    height: float | None = None


class Edge(BaseModel):
    id: str = Field(default_factory=lambda: "e_" + uuid.uuid4().hex[:8])
    source: str
    target: str
    condition: str | None = None  # "true"/"false"/"else" or an expression (edges leaving a condition node)
    label: str = ""


class InputSpec(BaseModel):
    label: str = ""
    type: Literal["string", "text", "number", "boolean"] = "text"
    required: bool = True
    default: Any = None


class GraphSettings(BaseModel):
    max_cost_usd: float | None = None
    memory_mode: Literal["global", "project", "conversation", "none"] | None = None
    model_id: str | None = None
    auto_commit: bool = True


class LoopGraph(BaseModel):
    id: str = Field(default_factory=lambda: "loop_" + uuid.uuid4().hex[:8])
    name: str
    description: str = ""
    category: str = "custom"
    version: int = 1
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    settings: GraphSettings = Field(default_factory=GraphSettings)
    builtin: bool = False

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def children(self, parent: str | None) -> list[Node]:
        return [n for n in self.nodes if n.parent == parent and n.type != "note"]

    def edges_in_scope(self, parent: str | None) -> list[Edge]:
        ids = {n.id for n in self.children(parent)}
        return [e for e in self.edges if e.source in ids and e.target in ids]


class ValidationIssue(BaseModel):
    level: Literal["error", "warning"]
    message: str
    node: str | None = None
    edge: str | None = None


REQUIRED_CONFIG: dict[str, list[str]] = {
    "agent": ["prompt"],
    "tool": ["tool"],
    "command": ["command"],
    "condition": [],
    "loop": ["max_iterations"],
    "parallel": ["items"],
    "subflow": ["loop_id"],
    "tournament": ["prompt"],
}


def validate_graph(graph: LoopGraph) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    ids = [n.id for n in graph.nodes]
    dupes = {i for i in ids if ids.count(i) > 1}
    for d in dupes:
        issues.append(ValidationIssue(level="error", message=f"节点 id 重复: {d}", node=d))
    by_id = {n.id: n for n in graph.nodes}
    for n in graph.nodes:
        if n.parent is not None:
            parent = by_id.get(n.parent)
            if parent is None or parent.type not in CONTAINER_TYPES:
                issues.append(ValidationIssue(level="error", message="父节点不存在或不是容器", node=n.id))
        for key in REQUIRED_CONFIG.get(n.type, []):
            if n.config.get(key) in (None, ""):
                issues.append(ValidationIssue(level="error", message=f"缺少配置项 {key}", node=n.id))
        if n.type == "loop":
            try:
                if int(n.config.get("max_iterations", 0)) < 1:
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(ValidationIssue(level="error", message="max_iterations 必须是正整数", node=n.id))
        if n.type in CONTAINER_TYPES and not graph.children(n.id):
            issues.append(ValidationIssue(level="error", message="容器内没有节点", node=n.id))
    for e in graph.edges:
        if e.source not in by_id or e.target not in by_id:
            issues.append(ValidationIssue(level="error", message="连线指向不存在的节点", edge=e.id))
            continue
        if by_id[e.source].parent != by_id[e.target].parent:
            issues.append(ValidationIssue(level="error", message="连线不能跨越容器边界（请连到容器本身）", edge=e.id))
    scopes = {None} | {n.id for n in graph.nodes if n.type in CONTAINER_TYPES}
    for scope in scopes:
        nodes = graph.children(scope)
        if not nodes:
            continue
        edges = graph.edges_in_scope(scope)
        if _has_cycle([n.id for n in nodes], edges):
            issues.append(ValidationIssue(level="error", message="出现环路：请用“循环”容器表达重复", node=scope))
        incoming = {e.target for e in edges}
        entries = [n for n in nodes if n.id not in incoming]
        if not entries:
            issues.append(ValidationIssue(level="error", message="找不到入口节点", node=scope))
        elif len(entries) > 1 and not any(n.type == "start" for n in entries):
            issues.append(ValidationIssue(level="warning", message=f"有 {len(entries)} 个入口节点，将按顺序执行",
                                          node=scope))
        for n in nodes:
            outs = [e for e in edges if e.source == n.id]
            if n.type == "condition" and len(outs) < 2:
                issues.append(ValidationIssue(level="warning", message="条件节点通常需要两条以上出边", node=n.id))
    return issues


def _has_cycle(node_ids: list[str], edges: list[Edge]) -> bool:
    graph: dict[str, list[str]] = {n: [] for n in node_ids}
    for e in edges:
        graph.setdefault(e.source, []).append(e.target)
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        state[n] = 1
        for m in graph.get(n, []):
            if state.get(m) == 1 or (state.get(m) is None and visit(m)):
                return True
        state[n] = 2
        return False

    return any(state.get(n) is None and visit(n) for n in node_ids)


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.level == "error" for i in issues)
