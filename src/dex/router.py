"""Where a chat message belongs: its intent, and the task or thread for it (design/26).

A model reads the message, but it only ever names things that exist: a task, a
thread or a skill it was shown. Which thread a skill's work goes in is not its
call. That is the tie between them (`threads.skill`), so it can't open threads.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from .config import Config
from .permissions import NO_TOOLS
from .prompts import ROUTER_SYSTEM, router_prompt
from .threads import intent_of

log = logging.getLogger("dex.router")

_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Route:
    intent: str
    reason: str = ""
    #: An existing task it continues, or the thread to post in. At most one.
    task_id: str | None = None
    thread_id: str | None = None
    #: A skill whose thread doesn't exist yet: dex makes the one it declares.
    skill: str = ""
    #: Posted in a task's activity, and it is other work: it doesn't go to that task.
    new_task: bool = False


def _intent(thread: Any) -> str:
    return intent_of(thread.kind, getattr(thread, "skill", None))


def parse_route(
    reply: str, here: Any, threads: list[Any], tasks: list[dict[str, Any]], declared: dict[str, str] | None = None,
) -> Route | None:
    """What the reply can be trusted for: ids and skills are checked against what it was shown.

    `declared` is each enabled skill's thread title, for those that have one.
    None means the message stays where it was posted.
    """
    found = _OBJECT.search(reply)
    try:
        data = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or data.get("intent") not in ("design", "skill", "task"):
        return None
    intent, reason = data["intent"], str(data.get("reason") or "")[:200]
    new_task = data.get("new_task") is True
    # Where it stays if nothing below moves it.
    stay = Route(intent, reason, new_task=True) if new_task else None
    task_id, thread_id = data.get("task_id"), data.get("thread_id")
    if any(t["id"] == task_id for t in tasks):
        return Route(intent, reason, task_id=task_id)

    def to(thread: Any) -> Route | None:
        return stay if thread.id == here.id else Route(intent, reason, thread_id=thread.id, new_task=new_task)

    if intent == "design":
        design = next((t for t in threads if t.kind == "project_design"), None)
        return to(design) if design else stay
    if intent == "skill":
        skill = str(data.get("skill") or "") or next((getattr(t, "skill", None) for t in threads if t.id == thread_id), "") or ""
        tied = [t for t in threads if getattr(t, "skill", None) == skill] if skill else []
        if tied:
            # Several threads may share a skill: the reply may pick one, not leave them.
            return to(next((t for t in tied if t.id == thread_id), tied[0]))
        if skill in (declared or {}):
            return Route(intent, reason, skill=skill, new_task=new_task)
    elif intent == "task":
        target = next((t for t in threads if t.id == thread_id and _intent(t) == "task"), None)
        if target is not None:
            return to(target)
    # The design thread only shapes the project, so work can't stay there.
    if here.kind == "project_design":
        main = next((t for t in threads if t.kind == "main"), None)
        return Route(intent, reason, thread_id=main.id, new_task=new_task) if main else stay
    return stay


async def route_message(
    message: str, config: Config, *, here: Any, threads: list[Any], tasks: list[dict[str, Any]],
    skills: list[str], guide: str, model: str | None = None, on_task: Any = None,
    declared: dict[str, str] | None = None,
) -> Route | None:
    """One tool-less call, like planning. If it fails the message stays where it is."""
    if config.fake_agent:
        return None
    options = ClaudeAgentOptions(
        system_prompt=ROUTER_SYSTEM, model=model or config.planner_model, allowed_tools=[],
        disallowed_tools=NO_TOOLS, max_turns=1, permission_mode="dontAsk", setting_sources=[],
        cwd=str(config.workspace),
    )
    prompt = router_prompt(
        message,
        f"the activity of task {on_task.id} “{on_task.title}”, in the thread {here.title} ({here.id})"
        if on_task else f"{here.title} ({here.id})",
        [f"- {t.id} {_intent(t)}{f' ({t.skill})' if getattr(t, 'skill', None) else ''}: {t.title}" for t in threads],
        [f"- {t['id']} [{t['state']}] {t['title']} (in {t['thread'] or 'no thread'})" for t in tasks],
        [f"- {name}" + (f" (its thread: {(declared or {})[name]})" if name in (declared or {}) else "") for name in skills],
        guide,
    )
    chunks: list[str] = []
    try:
        async for event in query(prompt=prompt, options=options):
            if isinstance(event, AssistantMessage):
                chunks += [b.text for b in event.content if isinstance(b, TextBlock)]
    except Exception:
        log.exception("routing failed")
        return None
    return parse_route("".join(chunks), here, threads, tasks, declared)
