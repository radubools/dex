"""Task and event model shared by the queue, the runner, and the HTTP API."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    #: Blocked on a clarifying question or a tool approval from the operator.
    AWAITING_INPUT = "awaiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: Stopped mid-run and waiting to go again — because dex made room for
    #: higher-priority work, or because the server died under it. One state,
    #: because there is one thing to do about it: start it again. Not terminal;
    #: nobody needs to intervene.
    PAUSED = "paused"
    #: Taken out of the thread and out of every listing, without touching what
    #: it produced. Not a failure and not a deletion: the package it built is
    #: still on disk and still in the Library, but the task itself is done
    #: being looked at.
    ARCHIVED = "archived"

    @property
    def terminal(self) -> bool:
        return self in (
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.ARCHIVED,
        )

    @property
    def waiting(self) -> bool:
        """Will run again without anyone asking."""
        return self in (TaskState.QUEUED, TaskState.PAUSED)

    @property
    def resumable(self) -> bool:
        """Ended without finishing, so continuing it may still be worthwhile."""
        return self in (TaskState.FAILED, TaskState.CANCELLED)

    @property
    def continuable(self) -> bool:
        """Stopped short, and can be put straight back in the queue.

        Wider than `resumable`, which governs forking a continuation: a paused
        task has not failed at all, it is simply waiting, and the way to
        continue it is to queue the same row again.
        """
        return self in (TaskState.PAUSED, TaskState.FAILED, TaskState.CANCELLED)

    @property
    def pausable(self) -> bool:
        """Running or about to; pausing it means it goes again later."""
        return self in (
            TaskState.QUEUED,
            TaskState.RUNNING,
            TaskState.AWAITING_INPUT,
        )


EventType = Literal[
    "settings",
    "cost",
    "task_message",
    "task_message_delivered",
    "thread_message",
    "thread_busy",
    "task_created",
    "task_state",
    "text",
    "text_delta",
    "thinking",
    "thinking_delta",
    "block_end",
    "tool",
    "tool_result",
    "question",
    "question_answered",
    "approval",
    "approval_resolved",
    "diff",
    "asset",
    #: A package's tags changed, because its manifest did.
    "tags",
    "result",
    "error",
]


@dataclass(slots=True)
class Event:
    type: EventType
    data: dict[str, Any]
    task_id: str | None = None
    seq: int = 0
    ts: float = field(default_factory=time.time)
    #: Which project this event belongs to. Carried on the event because the
    #: live fan-out is in-process with no database round trip, so filtering a
    #: subscriber's stream to the projects they may see cannot join to `tasks`.
    project: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "taskId": self.task_id,
            **self.data,
        }


@dataclass
class Task:
    """One algorithm-generation request and everything the server knows about it."""

    problem: str
    title: str
    slug: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: TaskState = TaskState.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    cost_usd: float | None = None
    turns: int | None = None
    session_id: str | None = None
    #: The chat thread this task was started from, if any.
    thread_id: str | None = None
    #: The project whose directory and guide this task works under.
    project: str | None = None
    #: Which attempt this is, and the task it descends from.
    attempt: int = 1
    parent_id: str | None = None
    #: The agent session this run picked up, when it is a resume.
    resumed_from: str | None = None
    #: Directory to write into, when it differs from `slug` (a resumed attempt
    #: continues its parent's package rather than opening a new one).
    output_slug: str | None = None
    #: The model this attempt runs on, resolved when it was submitted.
    model: str | None = None
    #: True while `cost_usd` is a running estimate rather than the agent's own
    #: reported total.
    cost_is_estimate: bool = False
    #: Token counts from the agent's result. `thinking_tokens` is the part of
    #: `output_tokens` spent thinking, not an extra alongside it.
    tokens: dict[str, int] = field(default_factory=dict)
    #: Files the watcher has seen appear under this task's output directory.
    artifacts: list[str] = field(default_factory=list)
    #: Set when the task is executing, so it can be cancelled.
    runtime: asyncio.Task[None] | None = field(default=None, repr=False)
    #: question_id / approval_id -> future the agent is parked on.
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict, repr=False)
    #: Which of `pending` are tool approvals rather than questions. Only these
    #: may be released by the auto-approve toggle; a question needs a person.
    pending_approvals: set[str] = field(default_factory=set, repr=False)
    #: Set just before dex cancels a run to make room, so the runner records it
    #: as paused rather than cancelled.
    preempted: bool = field(default=False, repr=False)
    #: Set just before an *archive* cancels a run. Without it the runner writes
    #: the ending it would have written anyway -- a pause -- and although the
    #: database refuses to move a task out of `archived`, the event it emits
    #: has already told the UI otherwise. The row said archived and the screen
    #: said paused, which is what made archiving look like it did nothing.
    archived: bool = field(default=False, repr=False)
    #: Human-readable note about what the agent is doing right now.
    activity: str | None = None
    #: What this task is allowed to touch. `package` is one package directory,
    #: which is nearly everything. `project` widens it to the project as a
    #: whole, for a small uniform edit across packages that already exist —
    #: adding a field to every manifest, say. Never wider than one project.
    scope: str = "package"
    #: Paused by the operator rather than by dex making room. A task dex paused
    #: goes again by itself when there is capacity; one the operator paused
    #: waits to be told, or the button means nothing.
    held: bool = False
    #: Where in the operator's own material this task's work came from — a
    #: range of a PDF, a section of a document, a URL they gave. Set from the
    #: survey segment the planner built this task out of, and kept so the Files
    #: tab can show what went *in* beside what came out.
    anchor: dict[str, Any] | None = None

    @property
    def project_wide(self) -> bool:
        return self.scope == "project"

    def design_payload(self) -> dict[str, str]:
        """The conversation a design turn carries, unpacked.

        A task has one `problem` string and a design turn needs three things:
        what was just said, the guide as it stands, and the history. They are
        packed as JSON into `problem` rather than given columns of their own,
        because only this one scope has them and a nullable column per field
        would be three columns empty on every other row.
        """
        import json

        try:
            packed = json.loads(self.problem)
            if isinstance(packed, dict) and "message" in packed:
                return {
                    "message": str(packed.get("message", "")),
                    "guide": str(packed.get("guide", "")),
                    "history": str(packed.get("history", "(nothing yet)")),
                }
        except (ValueError, TypeError):
            pass
        # A design task created before this format, or by hand: the whole
        # problem is the message, which is still a usable turn.
        return {"message": self.problem, "guide": "", "history": "(nothing yet)"}

    @property
    def is_survey(self) -> bool:
        """A pre-planning pass over the attached sources, run as a task.

        Same reasoning as `is_design`: it is a real agent run with tools, so
        making it a task gives it the activity surface, the cost accounting and
        the retry behaviour rather than reimplementing all three beside the
        chat. It produces no package — its output is a JSON survey that the
        planner then reads.
        """
        return self.scope == "survey"

    def survey_payload(self) -> dict[str, Any]:
        """What a survey turn carries, unpacked.

        Packed into `problem` for the same reason a design turn's is: one
        string field, several things to say, and no appetite for four nullable
        columns that only one scope ever fills.
        """
        import json

        try:
            packed = json.loads(self.problem)
        except (ValueError, TypeError):
            packed = None
        if not isinstance(packed, dict):
            return {"message": self.problem, "attachments": [], "urls": [], "guide": ""}
        return {
            "message": str(packed.get("message", "")),
            "attachments": [str(a) for a in packed.get("attachments") or []],
            "urls": [str(u) for u in packed.get("urls") or []],
            "guide": str(packed.get("guide", "")),
        }

    @property
    def is_design(self) -> bool:
        """A turn of the project design chat, run as a task.

        It is a task so that it gets the whole activity surface for free —
        streamed text, collapsed thoughts, tool calls, diffs, and `ask_user` —
        rather than a second, thinner viewer reimplementing them.
        """
        return self.scope == "design"

    def project_dir(self, assets_dir: Path, project: str | None = None) -> Path:
        """The project's own directory, which is where its guide lives."""
        chosen = self.project or project
        return assets_dir / chosen if chosen else assets_dir

    @property
    def builds_package(self) -> bool:
        """Whether this task has a package directory of its own to fill.

        The three scopes that do not — a project-wide sweep, a design turn, a
        survey — work in the project root or write nothing at all, so neither
        their output directory nor the check on what landed in it can be about
        a package.
        """
        return not (self.project_wide or self.is_design or self.is_survey)

    def output_dir(self, assets_dir: Path, project: str | None = None) -> Path:
        """Where this task writes: its package, or the whole project.

        A project-wide task has no package of its own — it edits packages that
        are already there — so its directory is the project root.
        """
        root = self.project_dir(assets_dir, project)
        if not self.builds_package:
            return root
        return root / (self.output_slug or self.slug)

    def to_json(self, assets_dir: Path) -> dict[str, Any]:
        return {
            "id": self.id,
            "threadId": self.thread_id,
            "project": self.project,
            "title": self.title,
            "slug": self.slug,
            "problem": self.problem,
            "state": self.state.value,
            "activity": self.activity,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "error": self.error,
            "costUsd": self.cost_usd,
            "turns": self.turns,
            "outputDir": str(self.output_dir(assets_dir)),
            "artifacts": sorted(self.artifacts),
            "pending": sorted(self.pending),
            "attempt": self.attempt,
            "parentId": self.parent_id,
            "sessionId": self.session_id,
            "resumedFrom": self.resumed_from,
            # What the UI may offer for this task right now.
            "outputSlug": self.output_slug or self.slug,
            "model": self.model,
            "scope": self.scope,
            "held": self.held,
            "anchor": self.anchor,
            "costIsEstimate": self.cost_is_estimate,
            "tokens": self.tokens or None,
            "canResume": self.state.resumable,
            "canRerun": self.state.terminal,
            # Restart resurrects this task in place; rerun makes a new one.
            # Archiving is a decision to stop, so only the second is offered --
            # `restart()` refuses an archived task, and a button that quietly
            # does nothing is worse than no button.
            "canRestart": self.state is not TaskState.ARCHIVED,
            "canPause": self.state.pausable,
            "canContinue": self.state.continuable,
            # Anything can be archived except what is already archived; a
            # running task is stopped first.
            "canArchive": self.state is not TaskState.ARCHIVED,
        }


def slugify(text: str, taken: set[str] | None = None) -> str:
    """Directory-safe name for a problem, e.g. 'Two Sum' -> 'two-sum'."""
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in text.strip())
    slug = "-".join(part for part in cleaned.split("-") if part)[:48].strip("-") or "task"
    if taken and slug in taken:
        suffix = 2
        while f"{slug}-{suffix}" in taken:
            suffix += 1
        slug = f"{slug}-{suffix}"
    return slug


def continued_problem(task: "Task", note: str) -> str:
    """The brief for a follow-up or resumed attempt at `task`.

    An ordinary task's problem is prose, so the note is appended to it. A
    design turn's is JSON — the message, the guide as it stood, and the
    history — and appending prose to that leaves a string that no longer
    parses. `design_payload` then falls back to treating the whole blob as the
    message, so the continued turn ran with an empty guide and no history: it
    would rewrite the project's guide from nothing. The note goes inside the
    message instead, and the rest of the conversation is carried through
    untouched.
    """
    import json

    if not task.is_design:
        return f"{task.problem}\n\n{note}"

    payload = task.design_payload()
    return json.dumps({
        "message": f"{payload['message']}\n\n{note}",
        "guide": payload["guide"],
        "history": payload["history"],
    })
