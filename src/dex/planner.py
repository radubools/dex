"""Turns a chat message into a confirmable plan of parallel tasks."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from .config import Config
from .models import slugify
from .prompts import PLANNER_SYSTEM, planner_prompt

log = logging.getLogger("dex.planner")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class PlannedTask:
    title: str
    problem: str
    slug: str
    #: An existing package this rewrites in place. Empty for new work. Without
    #: it a "regenerate X" request built `x-2` beside `x` and left the original
    #: sitting there, which is how one problem ended up with three directories.
    updates: str = ""
    #: A package this task fills together with its siblings, when one piece of
    #: work genuinely cannot be cut into independent packages. Unlike `updates`
    #: it need not exist yet. Rare on purpose: the ordinary plan is separate
    #: tasks with separate directories, which is the only arrangement where one
    #: task failing leaves the others whole.
    package: str = ""
    #: "package" for ordinary work, "project" for one small uniform edit across
    #: every package the project already has.
    scope: str = "package"
    #: Where in the attached material this task's work is, when a survey found
    #: it. Carried as the survey emitted it so the operator can open the source
    #: at that spot, and echoed into `problem` so the worker knows its bounds.
    anchor: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "problem": self.problem,
            "slug": self.slug,
            "updates": self.updates,
            "package": self.package,
            "scope": self.scope,
            "anchor": self.anchor,
        }


@dataclass
class Plan:
    tasks: list[PlannedTask] = field(default_factory=list)
    notes: str = ""
    needs_clarification: str = ""
    #: Concrete answers offered with `needs_clarification`. A question with no
    #: options is a prompt to type prose, which is a worse thing to hand
    #: somebody on a phone than two buttons.
    options: list[str] = field(default_factory=list)
    #: What this plan did not cover, empty when it covered everything. A
    #: request that implies hundreds of tasks is planned in batches, and this
    #: is what carries the rest into the next one.
    remaining: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "tasks": [t.to_json() for t in self.tasks],
            "notes": self.notes,
            "needsClarification": self.needs_clarification,
            "options": self.options,
            "remaining": self.remaining,
        }


async def plan_from_message(
    message: str,
    config: Config,
    existing: list[str],
    model: str | None = None,
    project: str | None = None,
    guide: str = "",
    survey: str = "",
) -> Plan:
    """Ask a short agent run to split the message into independent tasks.

    Deliberately tool-less: planning reads nothing and writes nothing, so the
    run cannot wander into the filesystem.
    """
    if config.fake_agent:
        return _offline_plan(message, existing)

    options = ClaudeAgentOptions(
        system_prompt=PLANNER_SYSTEM,
        model=model or config.planner_model,
        allowed_tools=[],
        max_turns=1,
        permission_mode="dontAsk",
        setting_sources=[],
        cwd=str(config.workspace),
    )

    chunks: list[str] = []
    prompt = planner_prompt(message, existing, project, guide, survey)
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            chunks.extend(b.text for b in msg.content if isinstance(b, TextBlock))

    return parse_plan("".join(chunks), existing)


def _offline_plan(message: str, existing: list[str]) -> Plan:
    """Crude split used only by DEX_FAKE_AGENT, so the UI has a plan to confirm."""
    import re

    parts = [p.strip(" .") for p in re.split(r"\s*(?:,|;|\band\b)\s*", message) if p.strip(" .")]
    taken = set(existing)
    tasks = []
    for part in parts[:4]:
        slug = slugify(part, taken)
        taken.add(slug)
        tasks.append(
            PlannedTask(title=part[:60], problem=f"{part}. (offline stub — DEX_FAKE_AGENT is set.)", slug=slug)
        )
    if not tasks:
        return Plan(needs_clarification="Which problem should I work on?")
    return Plan(tasks=tasks, notes="offline planner: split on commas and 'and'")


def _balanced_objects(raw: str) -> list[str]:
    """Every balanced `{...}` in the text, outermost first.

    Used to rescue work from a reply that was cut off mid-JSON: the outer
    object never closes, but the task objects already emitted are complete.
    """
    found: list[str] = []
    # A stack, not a depth counter: when the reply is cut off the outer object
    # never closes, so anything that only reports depth-zero objects reports
    # nothing at all — which is exactly the case this exists for.
    starts: list[int] = []
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            starts.append(index)
        elif char == "}" and starts:
            found.append(raw[starts.pop() : index + 1])
    return found


def _salvage_tasks(raw: str) -> list[dict[str, Any]]:
    """The task objects from a truncated reply, in the order they appear."""
    salvaged: list[dict[str, Any]] = []
    for chunk in _balanced_objects(raw):
        try:
            entry = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and str(entry.get("problem", "")).strip():
            salvaged.append(entry)
    return salvaged


def _anchor_of(entry: dict[str, Any]) -> dict[str, Any] | None:
    """A task's anchor, kept only when it says something.

    Normalised through `Anchor` rather than passed along raw: the planner is
    copying a nested object by hand and may hand back a string, a half-filled
    dict, or page numbers as text. An anchor that names no source is dropped —
    the UI turns one into a button that opens the document, and a button
    pointing at nothing is worse than no button.
    """
    from .sources import Anchor

    raw = entry.get("anchor")
    if not isinstance(raw, dict):
        return None
    anchor = Anchor.from_json(raw)
    if not anchor.source and not anchor.url:
        return None
    if not (anchor.page or anchor.line or anchor.sheet or anchor.heading or anchor.url):
        return None
    return anchor.to_json()


def parse_plan(raw: str, existing: list[str]) -> Plan:
    """Extract the plan JSON, tolerating prose or a missing fence around it."""
    payload: dict[str, Any] | None = None
    match = _JSON_FENCE.search(raw)
    candidates = [match.group(1)] if match else []
    if not match:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue

    if not isinstance(payload, dict):
        # A reply cut off mid-array leaves no closing fence and no balanced
        # outer object, but the tasks it did finish are still usable — throwing
        # them away cost a twelve-minute planning run.
        rescued = _salvage_tasks(raw)
        if rescued:
            log.warning(
                "planner output was cut off; rescued %d task(s) from it", len(rescued)
            )
            payload = {
                "tasks": rescued,
                "notes": (
                    f"The plan was cut off part-way; these {len(rescued)} tasks are what "
                    "survived."
                ),
                "remaining": "the rest of the request — the reply was cut off",
            }
        else:
            # Both ends: the start says what it was doing, the end says where it
            # stopped, which is the half that matters for a truncated reply.
            log.warning(
                "planner returned unparseable output: %s … %s", raw[:300], raw[-300:]
            )
            return Plan(
                needs_clarification="I could not turn that into a task list — can you restate it?"
            )

    taken = set(existing)
    tasks: list[PlannedTask] = []
    for entry in payload.get("tasks") or []:
        if not isinstance(entry, dict):
            continue
        problem = str(entry.get("problem", "")).strip()
        if not problem:
            continue
        title = str(entry.get("title") or problem.splitlines()[0])[:80]
        # What it rewrites has to be a package that exists; anything else is
        # the planner guessing, and would write into a directory of its own.
        updates = str(entry.get("updates") or "").strip()
        if updates and updates not in existing:
            updates = ""
        # Anything the planner invents here would silently widen what a task
        # may touch, so only the one alternative is accepted.
        scope = "project" if str(entry.get("scope") or "").strip() == "project" else "package"
        # A shared directory, which — unlike `updates` — is allowed not to
        # exist yet: that is the whole point of it. So it is normalised rather
        # than checked against the list, and it never outranks `updates`, which
        # names a package that is certainly there.
        shared = str(entry.get("package") or "").strip()
        package = slugify(shared) if shared else ""
        # A sweep has no package of its own; carrying `updates` too would say
        # it writes into one particular package, which is not what it does.
        if scope == "project":
            updates = package = ""
        slug = slugify(str(entry.get("slug") or title), taken)
        taken.add(slug)
        tasks.append(
            PlannedTask(
                title=title, problem=problem, slug=slug, updates=updates,
                package=package, scope=scope, anchor=_anchor_of(entry),
            )
        )

    # Options only mean anything beside a question, and a model that offers
    # twelve has not narrowed anything down. Trimmed rather than trusted: each
    # becomes a button, and a button with a paragraph on it is not a button.
    question = str(payload.get("needs_clarification") or "")
    options = [
        str(o).strip()[:80]
        for o in (payload.get("options") or [])
        if isinstance(o, (str, int, float)) and str(o).strip()
    ][:4] if question else []

    return Plan(
        tasks=tasks,
        notes=str(payload.get("notes") or ""),
        needs_clarification=question,
        options=options,
        remaining=str(payload.get("remaining") or ""),
    )
