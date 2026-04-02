# -*- coding: utf-8 -*-
"""
事件总线：所有 Agent 共享的消息中枢。
- 每条消息都有 role / event_type / content / timestamp
- Agent 订阅感兴趣的事件类型，事件到来时自动触发
- 持久化到 runs/<run_id>/bus.json，全程可追溯
"""
import json
import os
import time
import threading
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional
from pathlib import Path


class Event:
    USER_REQUIREMENT = "user.requirement"

    PLAN_STARTED    = "plan.started"
    PLAN_DONE       = "plan.done"
    PLAN_FAILED     = "plan.failed"

    CODE_STARTED    = "code.started"
    CODE_STEP_DONE  = "code.step.done"
    CODE_STEP_FAILED = "code.step.failed"
    CODE_DONE       = "code.done"
    CODE_FAILED     = "code.failed"

    REVIEW_STARTED  = "review.started"
    REVIEW_DONE     = "review.done"

    FIX_STARTED     = "fix.started"
    FIX_DONE        = "fix.done"

    PR_CREATED      = "pr.created"
    PIPELINE_DONE   = "pipeline.done"
    PIPELINE_FAILED = "pipeline.failed"

    AGENT_LOG       = "agent.log"
    BUILD_ERROR     = "build.error"
    BUILD_OK        = "build.ok"


@dataclass
class Message:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    run_id: str = ""
    timestamp: float = field(default_factory=time.time)
    event_type: str = ""
    role: str = ""
    content: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(**d)


class EventBus:
    """线程安全的事件总线，支持持久化。"""

    def __init__(self, run_id: str, runs_dir: str = "runs"):
        self.run_id = run_id
        self.run_dir = Path(runs_dir) / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.bus_file = self.run_dir / "bus.json"
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[Callable]] = {}
        self._messages: list[Message] = self._load()

    def publish(self, event_type: str, role: str, content: str, data: dict = None) -> Message:
        msg = Message(
            run_id=self.run_id,
            event_type=event_type,
            role=role,
            content=content,
            data=data or {},
        )
        with self._lock:
            self._messages.append(msg)
            self._save()

        ts = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
        print(f"  [{ts}] [{role.upper():<10}] {event_type}: {content[:80]}", flush=True)

        for cb in self._subscribers.get(event_type, []):
            threading.Thread(target=cb, args=(msg,), daemon=True).start()
        for cb in self._subscribers.get("*", []):
            threading.Thread(target=cb, args=(msg,), daemon=True).start()

        return msg

    def log(self, role: str, content: str):
        self.publish(Event.AGENT_LOG, role, content)

    def subscribe(self, event_type: str, callback: Callable):
        self._subscribers.setdefault(event_type, []).append(callback)

    def get_messages(self, role: str = None, event_type: str = None) -> list[Message]:
        with self._lock:
            msgs = list(self._messages)
        if role:
            msgs = [m for m in msgs if m.role == role]
        if event_type:
            msgs = [m for m in msgs if m.event_type == event_type]
        return msgs

    def get_context_for_llm(self, max_messages: int = 20) -> list[dict]:
        skip = {Event.AGENT_LOG, Event.BUILD_ERROR, Event.BUILD_OK}
        msgs = [m for m in self._messages if m.event_type not in skip]
        msgs = msgs[-max_messages:]
        return [{"role": m.role, "content": f"[{m.event_type}] {m.content}"} for m in msgs]

    def get_latest(self, event_type: str) -> Optional[Message]:
        msgs = self.get_messages(event_type=event_type)
        return msgs[-1] if msgs else None

    def wait_for(self, event_type: str, timeout: float = 600) -> Optional[Message]:
        event = threading.Event()
        result: list[Message] = []

        def handler(msg: Message):
            result.append(msg)
            event.set()

        self.subscribe(event_type, handler)
        event.wait(timeout=timeout)
        return result[0] if result else None

    def _save(self):
        self.bus_file.parent.mkdir(parents=True, exist_ok=True)
        data = [m.to_dict() for m in self._messages]
        self.bus_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> list[Message]:
        if not self.bus_file.exists():
            return []
        try:
            data = json.loads(self.bus_file.read_text(encoding="utf-8"))
            return [Message.from_dict(d) for d in data]
        except Exception:
            return []

    def format_report(self) -> str:
        lines = [f"# Agent Run Report — {self.run_id}", ""]
        role_icons = {
            "user": "User", "planner": "Planner", "coder": "Coder",
            "reviewer": "Reviewer", "fixer": "Fixer", "system": "System",
        }
        for msg in self._messages:
            if msg.event_type == Event.AGENT_LOG:
                continue
            ts = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
            icon = role_icons.get(msg.role, msg.role)
            lines.append(f"**[{ts}] {icon}** `{msg.event_type}`")
            lines.append(f"> {msg.content}")
            lines.append("")
        return "\n".join(lines)
