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
    cost_usd: float = 0.0
    cost_unknown: bool = False  # some turns used a model without pricing, so cost_usd undercounts
    tokens: dict[str, int] = Field(default_factory=dict)
    status: Literal["idle", "running"] = "idle"

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"system_snapshot"})


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

    def update(self, conv_id: str, **fields: Any) -> Conversation:
        conv = self.get(conv_id)
        prompt_affecting = {"memory_mode", "harness_id", "model_id"}
        changed = {k for k, v in fields.items() if v is not None and getattr(conv, k) != v}
        for key, value in fields.items():
            if value is not None and hasattr(conv, key):
                setattr(conv, key, value)
        if conv.temporary:
            conv.memory_mode = "none"
        if conv.memory_mode == "project" and conv.project_id is None:
            conv.memory_mode = "conversation"
        if changed & prompt_affecting:
            conv.system_snapshot = None  # rebuilt on the next turn
            self.agents.pop(conv_id, None)
        return self.save(conv)

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
                self._persist_progress(conv, agent)

        history = [Message.model_validate({k: v for k, v in m.items() if k not in ("seq", "created")})
                   for m in self.raw_messages(conv.id)]
        ctx = HarnessContext(
            workspace=self.workspace(conv), project=project, conversation_id=conv.id, topics=[f"conv:{conv.id}"],
            memory_mode=conv.memory_mode, auto_mode=False, model_override=conv.model_id,
            permission_mode=conv.permission_mode,
        )
        agent = self.builder.build_agent(profile, ctx, history=history, system_override=conv.system_snapshot,
                                         on_message=on_message)
        holder["agent"] = agent
        agent.ctx.readonly_roots.extend(p for p in map(Path, conv.attachments) if p.exists())
        if conv.system_snapshot is None:
            conv.system_snapshot = agent.system_prompt
            self.save(conv)
        return agent

    async def send(self, conv_id: str, text: str, files: list[str] | None = None) -> dict[str, Any]:
        conv = self.get(conv_id)
        attached = self._attach(conv, files)
        if attached:
            text = text + "\n\n" + attached
        if self._running(conv_id):
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

    def _persist_progress(self, conv: Conversation, agent: Agent) -> None:
        """Write cost and tokens after every assistant turn: a crash must not lose the whole run's accounting."""
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
        try:
            result = await agent.run(text)
            conv = self.get(conv_id)
            self._persist_progress(conv, agent)
            self._explain_ending(conv, result)
        except asyncio.CancelledError:
            self.app.bus.publish(f"conv:{conv_id}", "agent.done", {"status": "cancelled"})
        except Exception as exc:  # surface configuration errors (missing key etc.) to the UI
            self.agents.pop(conv_id, None)
            self.app.bus.publish(f"conv:{conv_id}", "agent.error", {"error": str(exc)})
        finally:
            self.app.bus.publish(f"conv:{conv_id}", "conversation.status", {"status": "idle"})

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
        return HarnessContext(workspace=self.workspace(conv), project=project, conversation_id=conv.id,
                              topics=[f"conv:{conv.id}"], memory_mode=conv.memory_mode, model_override=conv.model_id)

    def transcript(self, conv_id: str) -> list[Message]:
        return [Message.model_validate({k: v for k, v in m.items() if k not in ("seq", "created")})
                for m in self.raw_messages(conv_id)]

    async def aclose(self) -> None:
        for conv_id in list(self.tasks):
            self.stop(conv_id)
        for conv_id in list(self.temp):
            shutil.rmtree(temp_root() / conv_id, ignore_errors=True)


def text_of(message: dict[str, Any]) -> str:
    return "".join(b.get("text", "") for b in message.get("content", []) if b.get("type") == "text")


__all__ = ["Conversation", "ConversationManager", "TextBlock", "message_view", "text_of"]
