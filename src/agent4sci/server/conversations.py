"""Conversations: persistent chats (DB) and temporary chats (memory only, no memory access)."""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ..agent.agent import Agent, AgentResult
from ..agent.context import NOTICE_KIND, notice_message
from ..agent.harness import HarnessBuilder, HarnessContext
from ..core.paths import conversation_files_dir, conversation_memory_dir, temp_root
from ..llm.types import Message, TextBlock
from ..tools.base import ToolError

if TYPE_CHECKING:  # pragma: no cover
    from ..core.app import AppServices

MemoryMode = Literal["global", "project", "conversation", "none"]


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: "c_" + uuid.uuid4().hex[:10])
    title: str = "新对话"
    project_id: str | None = None
    memory_mode: MemoryMode = "global"
    harness_id: str = "assistant"
    permission_mode: str | None = None  # None = whatever the harness says
    model_id: str | None = None
    temporary: bool = False
    archived: bool = False
    created: float = Field(default_factory=time.time)
    updated: float = Field(default_factory=time.time)
    system_snapshot: str | None = None
    attachments: list[str] = Field(default_factory=list)  # files the user handed over: readable outside the workspace
    # "allow, and stop asking" answers, kept here so they survive the agent being rebuilt.
    # Keys are tool categories; SHELL_GUARD_KEY is the risky-command guard.
    approval_overrides: dict[str, str] = Field(default_factory=dict)
    cost_usd: float = 0.0
    # What to do with a message sent while the agent is still working: cut in immediately (steer),
    # or hold it and send it as the next turn once this one finishes.
    steer_mode: Literal["insert", "queue"] = "insert"
    run_seconds: float = 0.0  # time the agent actually spent working, summed over every turn
    last_turn_seconds: float = 0.0
    # The agent's own task list for the job in hand (update_todos). Kept here, not on the Agent, so it
    # survives a rebuild and the UI can show it without replaying the transcript.
    todos: list[dict[str, Any]] = Field(default_factory=list)
    # Every project has one standing "main" conversation whose agent can open further conversations in
    # that project and hand them work; `spawned_by` is the main conversation that opened this one.
    is_main: bool = False
    spawned_by: str | None = None
    cost_unknown: bool = False  # some turns used a model without pricing, so cost_usd undercounts
    tokens: dict[str, int] = Field(default_factory=dict)
    status: Literal["idle", "running"] = "idle"

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"system_snapshot"})


SHELL_GUARD_KEY = "exec.shell:guard"


def message_view(data: dict[str, Any]) -> dict[str, Any]:
    """What the UI needs (drop provider raw payloads)."""
    return {k: v for k, v in data.items() if k != "raw"}


class ConversationManager:
    KIND = "conversation"

    def __init__(self, app: "AppServices", builder: HarnessBuilder) -> None:
        self.app = app
        self.builder = builder
        self.temp: dict[str, Conversation] = {}
        self._persisted: dict[str, tuple[float, int, int]] = {}  # conv id → (cost, input, output) already saved
        self.temp_messages: dict[str, list[dict[str, Any]]] = {}
        self.agents: dict[str, Agent] = {}
        self.queued: dict[str, list[str]] = {}  # messages waiting for the current turn to finish
        self.tasks: dict[str, asyncio.Task] = {}

    # ---- CRUD ----------------------------------------------------------------------
    def list(self, project_id: str | None = None, include_archived: bool = False) -> list[Conversation]:
        items = [Conversation(**d) for d in self.app.db.list(self.KIND)]
        items += list(self.temp.values())
        if project_id is not None:
            items = [c for c in items if c.project_id == project_id]
        if not include_archived:
            items = [c for c in items if not c.archived]
        for c in items:
            c.status = "running" if self._running(c.id) else "idle"
        return sorted(items, key=lambda c: -c.updated)

    def get(self, conv_id: str) -> Conversation:
        if conv_id in self.temp:
            conv = self.temp[conv_id]
        else:
            data = self.app.db.get(self.KIND, conv_id)
            if data is None:
                raise KeyError(conv_id)
            conv = Conversation(**data)
        conv.status = "running" if self._running(conv_id) else "idle"
        return conv

    def _running(self, conv_id: str) -> bool:
        task = self.tasks.get(conv_id)
        return task is not None and not task.done()

    def save(self, conv: Conversation) -> Conversation:
        conv.updated = time.time()
        if conv.temporary:
            self.temp[conv.id] = conv
        else:
            self.app.db.put(self.KIND, conv.id, conv.model_dump(mode="json"), parent=conv.project_id)
        return conv

    def create(self, *, project_id: str | None = None, title: str | None = None, memory_mode: MemoryMode | None = None,
               harness_id: str = "assistant", model_id: str | None = None, temporary: bool = False) -> Conversation:
        project = self.app.projects.get(project_id) if project_id else None
        if project_id and project is None:
            raise KeyError(project_id)
        if temporary:
            mode: MemoryMode = "none"
        elif memory_mode:
            mode = memory_mode
        else:
            mode = project.settings.default_memory_mode if project else "global"
        if mode == "project" and project is None:
            mode = "conversation"
        conv = Conversation(title=title or ("临时对话" if temporary else "新对话"), project_id=project_id,
                            memory_mode=mode, harness_id=harness_id, model_id=model_id, temporary=temporary)
        if temporary:
            self.temp_messages[conv.id] = []
        return self.save(conv)

    def main_conversation(self, project_id: str) -> Conversation:
        """The project's standing main conversation, created the first time it is asked for."""
        project = self.app.projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        for conv in sorted(self.list(project_id, include_archived=True), key=lambda c: c.created):
            if conv.is_main:
                if conv.archived:  # asking for it again is how you get it back
                    conv.archived = False
                    self.save(conv)
                return conv
        conv = self.create(project_id=project_id, title=f"{project.name} · 主对话")
        conv.is_main = True
        return self.save(conv)

    def update(self, conv_id: str, **fields: Any) -> Conversation:
        conv = self.get(conv_id)
        prompt_affecting = {"memory_mode", "harness_id", "model_id"}
        # Changing these does not alter the system prompt, but it is baked into the Agent at build time,
        # so the cached agent has to go or the setting only takes effect after an app restart.
        agent_affecting = {"permission_mode"}
        # A caller that passes an explicit null means "clear it" — only refuse None on fields that
        # cannot hold it, or "follow the harness" becomes a one-way door.
        nullable = {name for name, f in Conversation.model_fields.items()
                    if type(None) in getattr(f.annotation, "__args__", ())}
        changed = {k for k, v in fields.items()
                   if hasattr(conv, k) and (v is not None or k in nullable) and getattr(conv, k) != v}
        for key, value in fields.items():
            if hasattr(conv, key) and (value is not None or key in nullable):
                setattr(conv, key, value)
        if conv.temporary:
            conv.memory_mode = "none"
        if conv.memory_mode == "project" and conv.project_id is None:
            conv.memory_mode = "conversation"
        if changed & prompt_affecting:
            conv.system_snapshot = None  # rebuilt on the next turn
        if changed & (prompt_affecting | agent_affecting):
            self.agents.pop(conv_id, None)
        return self.save(conv)

    def remember_always_allow(self, conv_id: str, key: str) -> None:
        """Record an "allow, and stop asking" answer so a rebuilt agent honours it too."""
        conv = self.get(conv_id)
        if conv.approval_overrides.get(key) == "auto":
            return
        conv.approval_overrides = {**conv.approval_overrides, key: "auto"}
        self.save(conv)
        agent = self.agents.get(conv_id)
        if agent is not None:  # the run is still going: apply it now, not just on the next rebuild
            if key == SHELL_GUARD_KEY:
                agent.ctx.shell_guard_allowed = True
            else:
                agent.config.approvals[key] = "auto"

    def drop_agents_using(self, harness_id: str) -> int:
        """Forget cached agents built from this harness, so an edit lands without restarting the app."""
        stale = [c.id for c in self.list() if c.harness_id == harness_id and c.id in self.agents
                 and not self._running(c.id)]
        for conv_id in stale:
            self.agents.pop(conv_id, None)
        return len(stale)

    def delete(self, conv_id: str) -> None:
        self.stop(conv_id)
        conv = self.get(conv_id)
        self.agents.pop(conv_id, None)
        if conv.temporary:
            self.temp.pop(conv_id, None)
            self.temp_messages.pop(conv_id, None)
            shutil.rmtree(temp_root() / conv_id, ignore_errors=True)
            return
        self.app.db.delete(self.KIND, conv_id)
        self.app.db.delete_messages(conv_id)
        self.app.db.delete_events(f"conv:{conv_id}")
        shutil.rmtree(conversation_memory_dir(conv_id), ignore_errors=True)
        if conv.project_id is None:
            shutil.rmtree(conversation_files_dir(conv_id), ignore_errors=True)

    def persist(self, conv_id: str) -> Conversation:
        """Turn a temporary chat into a normal one (memory stays off unless changed)."""
        conv = self.get(conv_id)
        if not conv.temporary:
            return conv
        messages = self.temp_messages.pop(conv_id, [])
        self.temp.pop(conv_id, None)
        conv.temporary = False
        conv.title = conv.title.replace("临时对话", "对话") or "对话"
        old_ws = temp_root() / conv_id
        if conv.project_id is None and old_ws.exists():
            shutil.copytree(old_ws, conversation_files_dir(conv_id), dirs_exist_ok=True)
            shutil.rmtree(old_ws, ignore_errors=True)
            self.agents.pop(conv_id, None)
        self.save(conv)
        for m in messages:
            self.app.db.add_message(conv_id, m)
        return conv

    # ---- messages ------------------------------------------------------------------------
    def raw_messages(self, conv_id: str) -> list[dict[str, Any]]:
        if conv_id in self.temp:
            return list(self.temp_messages.get(conv_id, []))
        return self.app.db.list_messages(conv_id)

    def messages(self, conv_id: str) -> list[dict[str, Any]]:
        return [message_view(m) for m in self.raw_messages(conv_id)]

    def workspace(self, conv: Conversation) -> Path:
        if conv.project_id:
            project = self.app.projects.get(conv.project_id)
            if project is not None:
                return project.workspace
        if conv.temporary:
            path = temp_root() / conv.id
            path.mkdir(parents=True, exist_ok=True)
            return path
        return conversation_files_dir(conv.id)

    def _persist_message(self, conv: Conversation, message: Message) -> None:
        data = message.model_dump(mode="json")
        data["created"] = time.time()
        if conv.temporary:
            self.temp_messages.setdefault(conv.id, []).append(data)
        else:
            self.app.db.add_message(conv.id, data)

    def _build_agent(self, conv: Conversation) -> Agent:
        project = self.app.projects.get(conv.project_id) if conv.project_id else None
        profile = self.builder.harnesses.get(conv.harness_id)
        self._persisted.pop(conv.id, None)  # a fresh agent starts its usage counters at zero
        holder: dict[str, Agent] = {}

        def on_message(message: Message) -> None:
            self._persist_message(conv, message)
            agent = holder.get("agent")
            if agent is not None and message.role == "assistant":
                self._persist_progress(conv.id, agent)

        history = [Message.model_validate({k: v for k, v in m.items() if k not in ("seq", "created")})
                   for m in self.raw_messages(conv.id)]
        ctx = HarnessContext(
            workspace=self.workspace(conv), project=project, conversation_id=conv.id, topics=[f"conv:{conv.id}"],
            memory_mode=conv.memory_mode, auto_mode=False, model_override=conv.model_id,
            permission_mode=conv.permission_mode,
        )
        self._attach_services(conv, ctx)
        agent = self.builder.build_agent(profile, ctx, history=history, system_override=conv.system_snapshot,
                                         on_message=on_message)
        # Restore the "stop asking me this" answers from earlier turns.
        overrides = dict(conv.approval_overrides)
        agent.ctx.shell_guard_allowed = overrides.pop(SHELL_GUARD_KEY, None) == "auto"
        agent.config.approvals.update(overrides)
        holder["agent"] = agent
        agent.ctx.readonly_roots.extend(p for p in map(Path, conv.attachments) if p.exists())
        if conv.system_snapshot is None:
            conv.system_snapshot = agent.system_prompt
            self.save(conv)
        return agent

    def _attach_services(self, conv: Conversation, ctx: HarnessContext) -> None:
        """Services this conversation's agent gets on top of the harness's own.

        The task list is for every conversation; the project desk only for the main one — which is what
        stops a spawned conversation from spawning more. Done here rather than in a tool group so the
        harness preview shows the same thing the agent actually gets.
        """
        ctx.extra_services["todos"] = ConversationTodos(self, conv.id)
        if not (conv.is_main and ctx.project is not None):
            return
        from ..tools.project_agents import PROJECT_AGENT_TOOLS

        desk = ProjectDesk(self, conv.id)
        ctx.extra_tools.extend(PROJECT_AGENT_TOOLS)
        ctx.extra_services["project_desk"] = desk
        ctx.extra_sections.append(desk.prompt_section(self.builder.harnesses.get(conv.harness_id)))

    async def send(self, conv_id: str, text: str, files: list[str] | None = None) -> dict[str, Any]:
        conv = self.get(conv_id)
        attached = self._attach(conv, files)
        if attached:
            text = text + "\n\n" + attached
        if self._running(conv_id):
            if conv.steer_mode == "queue":
                self.queued.setdefault(conv_id, []).append(text)
                self.app.bus.publish(f"conv:{conv_id}", "message.queued",
                                     {"pending": len(self.queued[conv_id])})
                return {"status": "queued", "pending": len(self.queued[conv_id])}
            self.agents[conv_id].steer(text)
            return {"status": "steered"}
        agent = self.agents.get(conv_id)
        if agent is None:
            agent = self._build_agent(conv)
            self.agents[conv_id] = agent
        if conv.title in ("新对话", "临时对话") and text.strip():
            conv.title = text.strip().splitlines()[0][:40]
            self.save(conv)
        self.tasks[conv_id] = asyncio.create_task(self._run(conv_id, agent, text), name=f"conv:{conv_id}")
        self.app.bus.publish(f"conv:{conv_id}", "conversation.status", {"status": "running"})
        return {"status": "started"}

    def _attach(self, conv: Conversation, files: list[str] | None) -> str:
        """Register files the user dropped or pasted and describe them in the message."""
        lines = []
        for raw in list(files or [])[:20]:
            path = Path(str(raw)).expanduser()
            if not path.exists():
                raise ValueError(f"找不到文件：{raw}")
            resolved = path.resolve()
            if str(resolved) not in conv.attachments:
                conv.attachments.append(str(resolved))
            agent = self.agents.get(conv.id)
            if agent is not None and resolved not in agent.ctx.readonly_roots:
                agent.ctx.readonly_roots.append(resolved)
            kind = "目录" if resolved.is_dir() else f"{resolved.stat().st_size / 1024:.0f} KB"
            lines.append(f"- {resolved} （{kind}）")
        if not lines:
            return ""
        self.save(conv)
        return "用户附带的文件（已获授权，可直接用 read_file / list_files 读取绝对路径）：\n" + "\n".join(lines)

    def _persist_progress(self, conv_id: str, agent: Agent) -> None:
        """Write cost and tokens after every assistant turn: a crash must not lose the whole run's accounting.

        Re-read the conversation rather than saving a copy captured when the agent was built: tools write to
        the same record while the turn runs (the task list, "stop asking me this"), and a stale copy saved on
        top of them would quietly throw those away.
        """
        conv = self.get(conv_id)
        usage = agent.usage
        inputs = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
        before = self._persisted.get(conv.id, (0.0, 0, 0))
        delta = (agent.cost - before[0], inputs - before[1], usage.output_tokens - before[2])
        if delta[0] <= 0 and delta[1] <= 0 and delta[2] <= 0:
            return
        conv.cost_usd = round(conv.cost_usd + delta[0], 6)
        conv.cost_unknown = conv.cost_unknown or agent.cost_unknown
        conv.tokens = {"input": conv.tokens.get("input", 0) + delta[1],
                       "output": conv.tokens.get("output", 0) + delta[2]}
        self._persisted[conv.id] = (agent.cost, inputs, usage.output_tokens)
        self.save(conv)

    def sweep_interrupted(self) -> int:
        """After a crash or a quit mid-reply, say so in the chat instead of leaving a half-finished turn."""
        count = 0
        for conv in self.list():
            messages = self.raw_messages(conv.id)
            if not messages:
                continue
            last = messages[-1]
            if last.get("role") != "assistant" or last.get("meta", {}).get("kind") == NOTICE_KIND:
                continue
            if not any(b.get("type") == "tool_call" for b in last.get("content", [])):
                continue
            notice = notice_message("上次的回复在调用工具时被中断（应用退出或后端重启）。"
                                    "可以点下面的「继续」接着做；那次工具调用可能已经改过文件，继续前最好先确认。")
            notice.meta["action"] = "resume"
            self._persist_message(conv, notice)
            count += 1
        return count

    async def _run(self, conv_id: str, agent: Agent, text: str) -> None:
        started = time.monotonic()
        try:
            result = await agent.run(text)
            conv = self.get(conv_id)
            self._record_duration(conv, time.monotonic() - started)
            self._persist_progress(conv_id, agent)
            self._explain_ending(conv, result)
        except asyncio.CancelledError:
            self._record_duration(self.get(conv_id), time.monotonic() - started)
            self.app.bus.publish(f"conv:{conv_id}", "agent.done", {"status": "cancelled"})
        except Exception as exc:  # surface configuration errors (missing key etc.) to the UI
            self.agents.pop(conv_id, None)
            self._record_duration(self.get(conv_id), time.monotonic() - started)
            self.app.bus.publish(f"conv:{conv_id}", "agent.error", {"error": str(exc)})
        finally:
            self.app.bus.publish(f"conv:{conv_id}", "conversation.status", {"status": "idle"})
            if self.queued.get(conv_id):
                asyncio.create_task(self._drain(conv_id), name=f"conv-queue:{conv_id}")

    async def _drain(self, conv_id: str) -> None:
        """Send whatever piled up while the agent was busy, once it really has stopped."""
        for _ in range(100):
            if not self._running(conv_id):
                break
            await asyncio.sleep(0.05)
        pending = self.queued.pop(conv_id, [])
        if pending:
            await self.send(conv_id, "\n\n".join(pending))

    async def wait_idle(self, conv_id: str, timeout: float) -> bool:
        """Wait until that conversation stops working. False means it timed out and is still going."""
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            if not self._running(conv_id) and not self.queued.get(conv_id):
                return True
            await asyncio.sleep(0.25)
        return not self._running(conv_id)

    def _record_duration(self, conv: Conversation, seconds: float) -> None:
        """How long this turn took, and how long the conversation has cost in total."""
        conv.last_turn_seconds = round(seconds, 1)
        conv.run_seconds = round(conv.run_seconds + seconds, 1)
        self.save(conv)
        self.app.bus.publish(f"conv:{conv.id}", "turn.finished",
                             {"seconds": conv.last_turn_seconds, "total_seconds": conv.run_seconds,
                              "at": time.time()})

    def _explain_ending(self, conv: Conversation, result: AgentResult) -> None:
        """A run that stops without an answer must say so: silence looks like the app hanging."""
        if result.status == "max_turns":
            text = (f"已达到最大轮数（{result.turns} 轮）后停止，没有给出最终答复。"
                    "可以直接说“继续”，或在「Harness → 运行上限」里调大轮数（填 0 表示无上限）。")
        elif result.status == "budget":
            text = (f"预算已用完（已花约 {result.cost_usd:.2f} 美元），运行停止。"
                    "可以在「Harness → 运行上限」里调整预算，或继续对话时换用更便宜的模型。")
        elif result.status == "error":
            text = f"运行出错，已停止：{result.error}"
        elif result.status == "done" and not result.text.strip():
            text = "模型这一轮没有输出文字（只调用了工具就结束了）。可以说“继续”或直接追问。"
        elif result.status == "done" and result.budget_wrapped:
            text = "预算接近上限，模型被要求提前收尾，上面的回答可能不完整。"
        else:
            return
        notice = notice_message(text)
        self._persist_message(conv, notice)
        self.app.bus.publish(f"conv:{conv.id}", "agent.message", {"message": notice.model_dump(mode="json")})

    def stop(self, conv_id: str) -> bool:
        task = self.tasks.get(conv_id)
        self.app.interactions.cancel(f"conv:{conv_id}")
        # "Stop" has to mean stopped: anything queued behind this turn would otherwise start a new one.
        dropped = self.queued.pop(conv_id, [])
        if dropped:
            self._persist_message(self.get(conv_id), notice_message(
                f"已停止。排在后面的 {len(dropped)} 条消息没有发出去：\n\n"
                + "\n\n".join(f"> {t.strip()[:500]}" for t in dropped)))
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def steer(self, conv_id: str, text: str) -> bool:
        agent = self.agents.get(conv_id)
        if agent is None or not self._running(conv_id):
            return False
        agent.steer(text)
        return True

    def set_effort(self, conv_id: str, effort: str | None) -> None:
        agent = self.agents.get(conv_id)
        if agent is not None:
            agent.set_effort(effort)

    def harness_context(self, conv: Conversation) -> HarnessContext:
        project = self.app.projects.get(conv.project_id) if conv.project_id else None
        ctx = HarnessContext(workspace=self.workspace(conv), project=project, conversation_id=conv.id,
                             topics=[f"conv:{conv.id}"], memory_mode=conv.memory_mode, model_override=conv.model_id)
        self._attach_services(conv, ctx)
        return ctx

    def transcript(self, conv_id: str) -> list[Message]:
        return [Message.model_validate({k: v for k, v in m.items() if k not in ("seq", "created")})
                for m in self.raw_messages(conv_id)]

    async def aclose(self) -> None:
        for conv_id in list(self.tasks):
            self.stop(conv_id)
        for conv_id in list(self.temp):
            shutil.rmtree(temp_root() / conv_id, ignore_errors=True)



def _ago(ts: float) -> str:
    gap = max(0.0, time.time() - ts)
    if gap < 90:
        return "刚刚"
    if gap < 3600:
        return f"{gap / 60:.0f} 分钟前"
    if gap < 86400:
        return f"{gap / 3600:.0f} 小时前"
    return f"{gap / 86400:.0f} 天前"


def _took(seconds: float) -> str:
    return f"{seconds / 60:.0f}分{seconds % 60:.0f}秒" if seconds >= 60 else f"{seconds:.0f}秒"


class ConversationTodos:
    """The task list behind ``update_todos``, stored on the conversation so a rebuild does not lose it."""

    def __init__(self, manager: "ConversationManager", conv_id: str) -> None:
        self.manager = manager
        self.conv_id = conv_id

    def read(self) -> list[dict[str, Any]]:
        return list(self.manager.get(self.conv_id).todos)

    def write(self, items: list[dict[str, Any]]) -> None:
        conv = self.manager.get(self.conv_id)
        conv.todos = list(items)
        self.manager.save(conv)


class ProjectDesk:
    """What a project's main conversation can do with the other conversations in its project.

    Installed as the ``project_desk`` service for the main conversation only, which is also what stops a
    spawned conversation from spawning more.
    """

    def __init__(self, manager: "ConversationManager", conv_id: str) -> None:
        self.manager = manager
        self.conv_id = conv_id

    # ---- prompt ----------------------------------------------------------------------
    def prompt_section(self, profile: Any) -> str:
        mode = self.manager.get(self.conv_id).permission_mode or getattr(profile, "permission_mode", "ask_risky")
        labels = {"ask_all": "总是询问", "ask_risky": "按需询问", "allow_all": "全部允许", "custom": "自定义"}
        return (
            "## 这是本项目的主对话\n\n"
            "你常驻在这个项目里。除了自己动手，你还能在这个项目内**开新对话**，把活分给别的 agent：\n\n"
            "- `start_conversation`：开一个对话并派活。它是**真实对话**，会出现在侧边栏，研究者随时能点进去看、"
            "接着聊、或者直接接手。所以标题要写清楚，任务说明要自带上下文（对方看不到这边说过的话）。\n"
            "- 默认 `wait=true`，等它做完把报告给你。几件互不依赖的事，先用 `wait=false` 全派出去，"
            "再用 `list_conversations` / `read_conversation` 逐个收。\n"
            "- `send_to_conversation` 追加要求或纠偏；`stop_conversation` 停掉跑偏的。\n\n"
            "什么时候派活：一件事能独立交付、需要自己的上下文、或者几件事可以同时做。什么时候自己做：一两步就完事，"
            "或者要用到你这边已有的上下文。\n"
            "和 `delegate` 的区别：`delegate` 是一次性子代理，研究者看不到、也没法接手；派生对话会留下来。\n"
            f"派生对话默认沿用本对话的 Harness、模型和安全级别（现在是：{labels.get(mode, mode)}）。"
            f"每个派生对话都要花钱，所以同时最多 {self.MAX_RUNNING} 个在跑、未归档的最多 {self.MAX_LIVE} 个。"
        )

    # ---- helpers ---------------------------------------------------------------------
    def _main(self) -> Conversation:
        return self.manager.get(self.conv_id)

    def _target(self, conv_id: str) -> Conversation:
        main = self._main()
        if conv_id == main.id:
            raise ToolError("那是本对话自己。")
        try:
            conv = self.manager.get(conv_id)
        except KeyError:
            raise ToolError(f"找不到对话 {conv_id}。先用 list_conversations 看看有哪些。") from None
        if conv.project_id != main.project_id:
            raise ToolError("那个对话不属于本项目，你管不到它。")
        return conv

    def _readable(self, conv_id: str) -> list[dict[str, Any]]:
        """Messages worth reading: anything with text, plus which tools were called. Bare tool results are noise."""
        out = []
        for m in self.manager.raw_messages(conv_id):
            calls = [b.get("name") for b in m.get("content", []) if b.get("type") == "tool_call" and b.get("name")]
            text = text_of(m).strip()
            if text or calls:
                out.append({"role": m.get("role"), "text": text, "calls": calls})
        return out

    # ---- the tools' actual work -------------------------------------------------------
    MAX_RUNNING = 6   # dispatched at once: beyond this it is a runaway loop, not parallelism
    MAX_LIVE = 24     # dispatched and not archived: a cap the researcher can lift by archiving

    async def start(self, title: str, task: str, harness: str | None, wait: bool, timeout: float) -> str:
        main = self._main()
        if main.project_id is None:
            raise ToolError("本对话不属于任何项目。")
        mine = [c for c in self.manager.list(main.project_id) if c.spawned_by == main.id]
        running = [c for c in mine if c.status == "running"]
        if len(running) >= self.MAX_RUNNING:
            raise ToolError(f"已经有 {len(running)} 个派生对话在跑了，先用 read_conversation 收掉几个再派。")
        if len(mine) >= self.MAX_LIVE:
            raise ToolError(f"这个项目里你派生的对话已经有 {len(mine)} 个。先归档用不上的，或者自己做。")
        if harness:
            known = {h.id for h in self.manager.builder.harnesses.list()}
            if harness not in known:
                raise ToolError(f"没有这个 Harness：{harness}。可选：{'、'.join(sorted(known))}")
        child = self.manager.create(project_id=main.project_id, title=(title.strip()[:60] or "派生对话"),
                                    harness_id=harness or main.harness_id, model_id=main.model_id)
        child.spawned_by = main.id
        child.permission_mode = main.permission_mode
        child.steer_mode = main.steer_mode
        self.manager.save(child)
        return await self._hand_over(child.id, task, wait, timeout, opened=True)

    async def send(self, conv_id: str, text: str, wait: bool, timeout: float) -> str:
        conv = self._target(conv_id)
        return await self._hand_over(conv.id, text, wait, timeout, opened=False)

    async def _hand_over(self, conv_id: str, text: str, wait: bool, timeout: float, *, opened: bool) -> str:
        outcome = await self.manager.send(conv_id, text)
        conv = self.manager.get(conv_id)
        tag = f"「{conv.title}」{conv.id}"
        if outcome.get("status") == "queued":
            return f"[{tag} 正在忙，这条排在它后面（队列里 {outcome['pending']} 条）。]"
        if not wait:
            verb = "已派出" if opened else "已送到"
            return (f"[{verb}：{tag}。它在后台跑着，用 list_conversations / read_conversation 跟进；"
                    "研究者也能在侧边栏点进去看。]")
        finished = await self.manager.wait_idle(conv_id, timeout)
        conv = self.manager.get(conv_id)
        if not finished:
            return (f"[{tag} 还在跑（等了 {_took(timeout)}）。它没有被停掉——晚点用 "
                    f'read_conversation("{conv.id}") 收，或者 stop_conversation 停掉。]')
        readable = self._readable(conv_id)
        reply = next((m["text"] for m in reversed(readable) if m["role"] == "assistant" and m["text"]), "")
        head = (f"[{tag} 跑完了，用时 {_took(conv.last_turn_seconds)}，花费 ${conv.cost_usd:.4f}]")
        return f"{head}\n{reply or '（它没有留下文字结论，用 read_conversation 看它做了什么。）'}"

    def listing(self) -> str:
        main = self._main()
        rows = [c for c in self.manager.list(main.project_id, include_archived=True) if c.id != main.id]
        if not rows:
            return "本项目里除了主对话还没有别的对话。用 start_conversation 开一个。"
        lines = []
        for c in rows[:40]:
            last = next((m for m in reversed(self._readable(c.id))), None)
            preview = (last["text"] or ("调用 " + "、".join(last["calls"]))) if last else "还没说话"
            marks = ["正在运行" if c.status == "running" else "空闲"]
            if c.spawned_by == main.id:
                marks.append("你派的")
            if c.archived:
                marks.append("已归档")
            lines.append(f"- {c.id}｜{c.title}｜{c.harness_id}｜{'，'.join(marks)}｜${c.cost_usd:.4f}｜"
                         f"{_ago(c.updated)}\n  最后：{preview[:120].replace(chr(10), ' ')}")
        return "\n".join(lines)

    def read(self, conv_id: str, last: int) -> str:
        conv = self._target(conv_id)
        messages = self._readable(conv.id)[-last:]
        state = "正在运行" if conv.status == "running" else "空闲"
        head = f"对话「{conv.title}」（{conv.id}，{conv.harness_id}，{state}，${conv.cost_usd:.4f}）最近 {len(messages)} 条："
        if not messages:
            return head + "（还是空的）"
        blocks = []
        for m in messages:
            who = "研究者/派活" if m["role"] == "user" else "它"
            body = m["text"][:2000] or "（没有文字）"
            if m["calls"]:
                body += f"\n（调用了工具：{'、'.join(m['calls'][:12])}）"
            blocks.append(f"[{who}] {body}")
        return head + "\n\n" + "\n\n".join(blocks)

    def stop(self, conv_id: str) -> bool:
        return self.manager.stop(self._target(conv_id).id)


def text_of(message: dict[str, Any]) -> str:
    return "".join(b.get("text", "") for b in message.get("content", []) if b.get("type") == "text")


__all__ = ["Conversation", "ConversationManager", "ConversationTodos", "ProjectDesk", "TextBlock", "message_view", "text_of"]
