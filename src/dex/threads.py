"""Chat thread view models.

A thread is the conversation: what the operator asked, the plans dex proposed,
and the tasks those plans became. These are plain dataclasses — reading and
writing them is `store.ThreadStore`, backed by Postgres.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("dex.threads")

MessageRole = Literal["user", "dex"]
MessageKind = Literal["text", "plan", "tasks", "error"]


@dataclass
class ThreadMessage:
    role: MessageRole
    kind: MessageKind = "text"
    text: str = ""
    #: Plan payload for `plan`, task snapshots for `tasks`.
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


ThreadKind = Literal["chat", "project_design"]


@dataclass
class Thread:
    title: str = "New thread"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[ThreadMessage] = field(default_factory=list)
    #: Ids of every task started from this thread, oldest first.
    task_ids: list[str] = field(default_factory=list)
    #: Total messages, which a summary carries without loading them all.
    message_count: int = 0
    #: The project this belongs to, and what it is for. A `project_design`
    #: thread shapes the project's guide; a `chat` thread plans and runs tasks.
    project: str | None = None
    kind: ThreadKind = "chat"

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "messageCount": self.message_count or len(self.messages),
            "taskIds": list(self.task_ids),
            "project": self.project,
            "kind": self.kind,
        }

    def to_json(self) -> dict[str, Any]:
        return {**self.summary(), "messages": [m.to_json() for m in self.messages]}
