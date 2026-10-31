"""Where a chat message belongs: its intent, and the task or thread for it (design/26)."""

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
    #: An existing task it continues, a thread to post in, or a new one's title. At most one.
    task_id: str | None = None
    thread_id: str | None = None
    new_thread: str = ""
    #: Posted in a task's activity, and it is other work: it doesn't go to that task.
    new_task: bool = False


def parse_route(reply: str, here: Any, threads: list[Any], tasks: list[dict[str, Any]]) -> Route | None:
    """What the reply can be trusted for. Ids are checked: it can only pick from what it was shown.

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
    known = {t.id: t for t in threads}
    new_task = data.get("new_task") is True
    # Where it stays if nothing below moves it.
    stay = Route(intent, reason, new_task=True) if new_task else None
    task_id, thread_id = data.get("task_id"), data.get("thread_id")
    if any(t["id"] == task_id for t in tasks):
        return Route(intent, reason, task_id=task_id)
    # The thread decides the intent of what is in it, so they have to agree: this
    # keeps design and skills apart even when the reply doesn't.
    if intent == "design":
        thread_id = next((t.id for t in threads if t.kind == "project_design"), None)
    target = known.get(thread_id)
    if target is not None and intent_of(target.kind, target.title) != intent:
        target = None
    if target is not None:
        return stay if target.id == here.id else Route(intent, reason, thread_id=target.id, new_task=new_task)
    title = " ".join(str(data.get("new_thread") or "").split())[:80]
    if intent == "skill" and title and intent_of(here.kind, here.title) != "skill":
        same = next((t for t in threads if t.title.lower() == title.lower()), None)
        if same is None:
            return Route(intent, reason, new_thread=title, new_task=new_task)
        return stay if same.id == here.id else Route(intent, reason, thread_id=same.id, new_task=new_task)
    # The design thread only shapes the project, so work can't stay there.
    if here.kind == "project_design" and intent != "design":
        main = next((t for t in threads if t.kind == "main"), None)
        return Route(intent, reason, thread_id=main.id, new_task=new_task) if main else stay
    return stay


async def route_message(
    message: str, config: Config, *, here: Any, threads: list[Any], tasks: list[dict[str, Any]],
    skills: list[str], guide: str, model: str | None = None, on_task: Any = None,
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
        [f"- {t.id} {intent_of(t.kind, t.title)}: {t.title}" for t in threads],
        [f"- {t['id']} [{t['state']}] {t['title']} (in {t['thread'] or 'no thread'})" for t in tasks],
        skills, guide,
    )
    chunks: list[str] = []
    try:
        async for event in query(prompt=prompt, options=options):
            if isinstance(event, AssistantMessage):
                chunks += [b.text for b in event.content if isinstance(b, TextBlock)]
    except Exception:
        log.exception("routing failed")
        return None
    return parse_route("".join(chunks), here, threads, tasks)
