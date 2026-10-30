"""Game chat: a game talks to dex from inside the game.

A player types into a chat panel in a game (Many Builders, Model Workshop);
the game sends the message here, and dex answers by running a task in that
game's project, in its "Chats" thread. The task is a `chat` scope task: it
answers conversationally, and when asked for an asset it builds it with the
project's skills, installs it into the game folder and, when needed, changes
the game's code so the game uses it.

Everything is under `/api/game-chat/<project>/…` and authenticated by a
per-project token (`Authorization: Bearer <token>`), so a game holds only the
key to its own project's chat and nothing else in dex. The settings live in
`.dex/game-chat.json` (gitignored), re-read on every request:

    { "projects": { "ml": { "game": "Model Workshop",
                            "root": "/Users/…/games/model-workshop",
                            "token": "…", "threadId": "…" } } }

Routes:

- `GET  …/threads/<thread>/chats`: the chats in the thread, newest first (one
  per conversation; follow-up attempts fold into it).
- `POST …/threads/<thread>/messages {text, chatId?, todo?, context?}`: without
  `chatId` starts a chat; with one it goes to that chat at once: straight
  into the running agent (it interrupts its turn), or, if the chat has
  stopped, as the next attempt that resumes the same session. With `todo`
  it always starts a new chat, a `todos` run that splits the message into
  todos for when dex is idle (design/24).
- `GET  …/chats/<chat>/events?after=<seq>`: the chat's events after `seq`
  (none or 0: all of them), with streamed deltas merged, for polling.
- `POST …/chats/<chat>/pause | resume | cancel`.
"""

from __future__ import annotations

import hmac
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from .config import Config
from .models import Event, Task, TaskState
from .store import CostStore
from .threads import ThreadMessage

PREFIX = "/api/game-chat"
SETTINGS_FILE = "game-chat.json"
#: Biggest page of events one poll returns; the client asks again with `after`.
PAGE = 2000
#: Tool output is for a glance in a game's panel, not the whole log.
MAX_OUTPUT = 4000
#: The project's thread for game chats.
CHATS_TITLE = "Chats"


# ---------------------------------------------------------------- settings


def settings_path(config: Config) -> Path:
    return config.state_dir / SETTINGS_FILE


def load_settings(config: Config) -> dict[str, dict[str, Any]]:
    """Per-project game-chat settings; {} when none are configured."""
    try:
        data = json.loads(settings_path(config).read_text())
    except (OSError, ValueError):
        return {}
    projects = data.get("projects") if isinstance(data, dict) else None
    return projects if isinstance(projects, dict) else {}


def game_root(config: Config, project: str | None) -> Path | None:
    """The game folder a project's chats work on, if one is configured."""
    entry = load_settings(config).get(project or "") or {}
    root = entry.get("root")
    return Path(root).expanduser().resolve() if root else None


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def project_access(config: Config):
    """Dependency: the project in the path, if the request's token is its chat token."""

    def check(project: str, request: Request) -> dict[str, Any]:
        entry = load_settings(config).get(project)
        token = _bearer(request)
        if not entry or not entry.get("token") or not token or not hmac.compare_digest(str(entry["token"]), token):
            raise HTTPException(401, "game chat: unknown project or wrong token")
        return {"project": project, **entry}

    return check


# -------------------------------------------------------------------- CORS


class GameChatCors:
    """CORS for the game-chat routes only: games are served from other origins.

    Any origin may call them; each call still needs the project's token. The
    rest of dex keeps its own rules.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith(PREFIX):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        origin = headers.get(b"origin", b"*").decode() or "*"
        cors = [
            (b"access-control-allow-origin", origin.encode()),
            (b"vary", b"Origin"),
            (b"access-control-allow-headers", b"Authorization, Content-Type"),
            (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
            (b"access-control-max-age", b"600"),
        ]
        if scope.get("method") == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": cors})
            await send({"type": "http.response.body", "body": b""})
            return

        async def with_cors(message: dict) -> None:
            if message.get("type") == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *cors]}
            await send(message)

        await self.app(scope, receive, with_cors)


# ------------------------------------------------------------------- briefs


class ChatContext(BaseModel):
    #: What the player is looking at, in the game's words ("the k-means station, medium lesson").
    view: str = ""
    #: Where each kind of asset goes in the game, relative to its folder
    #: ({"model": "public/assets/<kind>/<name>/", "story": "public/assets/story/<name>/"}).
    destinations: dict[str, str] = Field(default_factory=dict)
    #: The lesson as the player sees it, when they are in one: the game's
    #: snapshot of this run of the story so far (the beat as shown, variables,
    #: lab state, stage, data) and the story JSON it was instantiated from.
    #: Hidden from the player: appended to their message for the agent only,
    #: in a <dex-context> block, with the whole of it saved to a file.
    story: dict[str, Any] | None = None
    #: Whether end-to-end tests run, in this chat and every task it starts or
    #: continues (the chat panel's "E2E tests" switch). Off: quicker, untested.
    e2e: bool = True

    @field_validator("story")
    @classmethod
    def _not_huge(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(json.dumps(v, default=str)) > MAX_CONTEXT_BYTES:
            raise ValueError(f"story context over {MAX_CONTEXT_BYTES // 1_000_000} MB")
        return v


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    #: The chat to continue (its first task's id, as listed); none starts a new one.
    chatId: str | None = None
    #: Don't run this now: split it into todos, for when dex is idle (todos.py).
    #: Always a new chat, so it leaves whatever is open running as it was.
    todo: bool = False
    #: A short name for a new chat; defaults to the message's first line.
    title: str | None = None
    context: ChatContext = Field(default_factory=ChatContext)


def _inside(root: Path, rel: str) -> str | None:
    """`rel` under `root` as an absolute path, or None if it would leave it."""
    rel = rel.strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    # Placeholders like <kind> stay as they are; only the real part is checked.
    real = Path(re.sub(r"<[^>]*>.*$", "", rel) or ".")
    target = (root / real).resolve()
    return str(root / rel) if target == root or root in target.parents else None


def pull_command(entry: dict[str, Any], e2e: bool = True) -> str | None:
    """How a chat pulls dex packages into its game: the game's own puller, if it has one.

    `entry["pull"]` names the script; otherwise a `tools/pull_dex_assets.py`
    beside the game folder (the games monorepo's shared puller) is used.
    """
    root = Path(entry.get("root") or ".").expanduser()
    script = Path(entry["pull"]).expanduser() if entry.get("pull") else root.parent / "tools" / "pull_dex_assets.py"
    if not script.is_file():
        return None
    return f"{sys.executable} {script} {root}" + ("" if e2e else " --allow-untested")


#: The largest lesson snapshot a game may send with a message.
MAX_CONTEXT_BYTES = 4_000_000
#: Wraps the hidden context appended to a player's message: the agent reads it,
#: a game's chat panel strips it (engine chat/index.js `playerText`).
CONTEXT_OPEN, CONTEXT_CLOSE = "<dex-context>", "</dex-context>"


def _short(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + " …"


def context_file(stamp: float | None = None) -> str:
    """Where a message's lesson snapshot is saved, relative to the chat's task directory."""
    return f"context/snapshot-{int((stamp or time.time()) * 1000)}.json"


def save_context(task_dir: Path, rel: str, story: dict[str, Any]) -> None:
    path = task_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(story, ensure_ascii=False, indent=1, default=str))


def context_block(story: dict[str, Any] | None, rel: str | None, e2e: bool = True) -> str:
    """The hidden part of a player's message: what is on their screen, in brief, and whether
    end-to-end tests run (for this chat and every task it starts or continues)."""
    if not story and e2e:
        return ""
    tests = ("" if e2e else
             "\n**End-to-end tests are off** (the player's switch): skip them here and in every task "
             "you start or continue (dex passes it on); pull with `--allow-untested`.\n")
    if not story:
        return "\n\n" + CONTEXT_OPEN + tests + CONTEXT_CLOSE
    lines = ["", "", CONTEXT_OPEN + tests,
             "## On the player's screen (a snapshot of this run of the lesson so far)", "",
             "This is an instantiation of the lesson's state so far: the story's values are "
             "filled in from this player's run (what they did, the numbers the lab produced), "
             "so text and numbers can differ from the template in the story JSON. Use it to "
             "tell what the player means by \"this\", \"here\" or \"that number\"; a change "
             "goes into the story (the template) or the asset, never into this snapshot.", ""]
    meta = story.get("story") or {}
    lines.append(f"- Station `{story.get('algorithm')}`, {story.get('mode')} mode, level {story.get('level')}.")
    if meta:
        lines.append(f"- Lesson: “{meta.get('title')}” (story `{meta.get('id')}`, schema {meta.get('schema')}).")
    beat = story.get("beat")
    if beat:
        lines.append(f"- Beat {beat.get('index', 0) + 1} of {beat.get('of')}: `{beat.get('beat')}`"
                     + (f", section “{beat.get('section')}”" if beat.get("section") else "")
                     + (" (its step is done)" if beat.get("done") else ""))
        if beat.get("title"):
            lines.append(f"- The card's title: {beat['title']}")
        if beat.get("text"):
            lines.append(f"- The card's text, as shown: {beat['text']}")
        if beat.get("choice"):
            c = beat["choice"]
            lines.append(f"- Its question: {c.get('prompt')} Options: " + " | ".join(c.get("options") or []))
        if beat.get("input"):
            lines.append(f"- It asks for a number: {beat['input'].get('prompt')}")
        if beat.get("path"):
            lines.append(f"- Beats played so far: {' → '.join(map(str, beat['path'][-12:]))}")
        if beat.get("vars"):
            lines.append(f"- The story's variables now: {_short(beat['vars'], 1500)}")
    lab = story.get("lab") or {}
    if lab:
        if lab.get("state"):
            lines.append(f"- The lab machine's state: `{lab['state']}`")
        ents = lab.get("entities") or []
        if ents:
            lines.append(f"- On the stage ({len(ents)}): " + ", ".join(f"`{e.get('id')}` ({e.get('asset')})" for e in ents[:30])
                         + (" …" if len(ents) > 30 else ""))
        labels = [l for l in lab.get("labels") or [] if l.get("text")]
        if labels:
            lines.append("- Labels on the floor: " + " | ".join(str(l["text"]) for l in labels[:20]) + (" …" if len(labels) > 20 else ""))
        if lab.get("params"):
            lines.append(f"- The bench's values: {_short(lab['params'][:14], 1200)}")
        if lab.get("points"):
            lines.append(f"- Data points: {len(lab['points'])} (in the file)")
    if rel:
        lines.append(f"\nThe whole snapshot, with the story JSON it was instantiated from (`storyJson`), "
                     f"is in `{rel}` in your task directory.")
    lines.append(CONTEXT_CLOSE)
    return "\n".join(lines)


def chat_brief(entry: dict[str, Any], message: ChatMessage, context_rel: str | None = None) -> str:
    """The task brief for a new chat: who is talking, where the game is, then the message."""
    game = entry.get("game") or entry.get("project")
    root = Path(entry.get("root") or ".").expanduser()
    dests = [(k, _inside(root, v)) for k, v in sorted(message.context.destinations.items())]
    dest_lines = "\n".join(f"  - {k}: `{v}`" for k, v in dests if v) or "  - (the game gave none; use `public/assets/<kind>/<name>/`)"
    view = message.context.view.strip()
    pull = pull_command(entry, message.context.e2e)
    pull_line = (
        f"run the game's own pull for exactly those packages, one `--only` per task directory "
        f"(the last part of each package path dex gives you):\n\n    {pull} --only <task-dir>\n\n"
        "It validates each package and, for a story, insists on its passing in-game test. A `FAIL` "
        "line means the game refused it: tell the player why, and never force it."
        if pull else ("tell the player it is ready in dex: this game has no pull for a chat to "
                      "run, so its operator installs it")
    )
    return f"""# In-game chat: {game}

You are talking with the player of **{game}** from inside the game (dex's
game chat). Your final message each turn is what the player reads in the
game's chat panel: answer conversationally and briefly, in plain words, and
say what you changed. They may type again while you work; their message then
arrives as your next turn.

## The game

- Its folder: `{root}`. You may read and write there.
- Where each kind of asset goes in the game:
{dest_lines}
- Read the game's `AGENTS.md` first (it says how the game is built and
  tested, and its engine-first rule), and this project's brief and skills
  for how assets are made.

## When the player asks for something to be made or changed

Assets and lessons are never made or edited in this chat. Each was made by
its own task, in its own thread of this project, which holds its history
and its checks; a change goes there, and so does anything new (dex refuses
package files and the game's installed assets here).

1. **Look it up.** `find_tasks` with words from its name or from what is on
   the player's screen (the hidden context below names the lesson, the beat,
   what stands on the stage). It exists: `continue_task` it with exactly what
   to change and why.
2. **Something new**: `start_task` in the thread for its kind (the project
   brief's "Which thread" table; `project_threads` lists them), with a brief
   that stands on its own. Be judicious: every task spends the operator's
   usage; never start one for a question you can answer.
   **A new set of lessons** (a new section of the game's lesson map, or new
   lessons in one) starts with the game's **story catalog**: continue the
   catalog task (`find_tasks` "catalog") or start one in the catalog thread
   with the new section or tiles, as placeholders with their story names.
   Only when it is done (and pulled, so the game shows the set as coming
   soon) start one story task per lesson, each brief naming its exact
   `story/<name>` from the catalog, and any asset tasks they need.
3. **Tell the player** in a sentence or two that it is under way and where,
   and end your turn. This chat is then blocked until that work is done, and
   dex resumes it with how each went.
4. **When resumed**, check each result, {pull_line}
5. **If the game needs code to use it** (a new model in a scene, a new sound
   on an event, a new lesson in a menu), change the game's code under
   `{root}`, keeping to its `AGENTS.md`, and run its tests (`npx vitest run`
   in that folder). Code is yours to change; assets are not.
6. Do not commit, push or publish anything, and do not touch other games or
   dex itself. Finish by telling the player what is now in the game: it
   reloads by itself when new assets are installed.

## Where the player is

{view or "(the game did not say)"}

## The player's message

{message.text.strip()}{context_block(message.context.story, context_rel, message.context.e2e)}
"""


# ------------------------------------------------------------------ helpers


def _chains(tasks: list[Task]) -> dict[str, list[Task]]:
    """Tasks grouped into chats: root id -> its attempts, oldest first."""
    by_id = {t.id: t for t in tasks}

    def root(t: Task) -> str:
        seen = set()
        while t.parent_id and t.parent_id in by_id and t.id not in seen:
            seen.add(t.id)
            t = by_id[t.parent_id]
        return t.id

    chains: dict[str, list[Task]] = {}
    for t in tasks:
        chains.setdefault(root(t), []).append(t)
    for members in chains.values():
        members.sort(key=lambda t: (t.attempt, t.created_at))
    return chains


def _summary(chat_id: str, members: list[Task], costs: dict[str, float]) -> dict[str, Any]:
    first, last = members[0], members[-1]
    stamps = [x for t in members for x in (t.created_at, t.started_at, t.finished_at) if x]
    return {
        "chatId": chat_id,
        "taskId": last.id,
        "title": first.title,
        "state": last.state.value,
        "activity": last.activity,
        "error": last.error,
        "attempts": len(members),
        # From the rollup: each attempt of a resumed session reports everything
        # before it too, so `cost_usd` cannot be added up.
        "costUsd": round(sum(costs.get(t.id, 0.0) for t in members), 4),
        "createdAt": first.created_at,
        "updatedAt": max(stamps) if stamps else first.created_at,
    }


def _merge(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fewer events, same feed: runs of deltas for one block become one delta,
    and only the last cost tick of a run is kept. Tool output is trimmed."""
    out: list[dict[str, Any]] = []
    for e in events:
        prev = out[-1] if out else None
        kind = e.get("type")
        if kind in ("text_delta", "thinking_delta") and prev and prev.get("type") == kind and prev.get("id") == e.get("id") and prev.get("taskId") == e.get("taskId"):
            prev["delta"] = prev.get("delta", "") + e.get("delta", "")
            prev["seq"] = e["seq"]
            continue
        if kind == "cost" and prev and prev.get("type") == "cost" and prev.get("taskId") == e.get("taskId"):
            out[-1] = e
            continue
        if kind == "tool_result" and isinstance(e.get("output"), str) and len(e["output"]) > MAX_OUTPUT:
            e = {**e, "output": e["output"][:MAX_OUTPUT] + "\n…", "truncated": True}
        out.append(e)
    return out


# ------------------------------------------------------------------- router


def build_game_chat_router(config: Config, db: Any, tasks: Any, threads: Any, publish_message: Any) -> APIRouter:
    router = APIRouter(prefix=PREFIX)
    access = project_access(config)

    async def chats_thread(entry: dict[str, Any], thread_id: str) -> Any:
        thread = await threads.get(thread_id)
        if thread is None or thread.project != entry["project"]:
            raise HTTPException(404, "no such thread in this project")
        configured = entry.get("threadId")
        if configured and configured != thread_id:
            raise HTTPException(403, "this game's chats go to its own Chats thread")
        return thread

    async def chain(entry: dict[str, Any], chat_id: str) -> list[Task]:
        first = await tasks.tasks.get(chat_id)
        if first is None or first.project != entry["project"] or not (first.is_chat or first.is_todos):
            raise HTTPException(404, "no such chat")
        members = _chains(await tasks.tasks.list(thread_id=first.thread_id, limit=10_000, include_archived=True)).get(chat_id)
        if not members:
            raise HTTPException(404, "no such chat")
        return members

    async def note(thread_id: str, text: str) -> None:
        await publish_message(thread_id, ThreadMessage(role="user", kind="text", text=text))
        await threads.touch(thread_id)

    @router.get("/{project}/threads/{thread_id}/chats")
    async def list_chats(thread_id: str, entry: dict = Depends(access)) -> dict[str, Any]:
        await chats_thread(entry, thread_id)
        found = [t for t in await tasks.tasks.list(thread_id=thread_id, limit=10_000)
                 if t.is_chat or t.is_todos]
        costs = await CostStore(db).by_tasks([t.id for t in found])
        chats = [_summary(cid, m, costs) for cid, m in _chains(found).items()]
        chats.sort(key=lambda c: c["updatedAt"], reverse=True)
        return {"project": entry["project"], "threadId": thread_id, "chats": chats}

    @router.post("/{project}/threads/{thread_id}/messages")
    async def send(thread_id: str, body: ChatMessage, entry: dict = Depends(access)) -> dict[str, Any]:
        await chats_thread(entry, thread_id)
        text = body.text.strip()
        story = body.context.story
        rel = context_file() if story else None
        # The chat runs as the switch says, and so does every task it starts.
        options = {"e2e": body.context.e2e}
        if not body.chatId or body.todo:
            title = (body.title or text.splitlines()[0])[:80]
            if body.todo:
                task = await tasks.submit(chat_brief(entry, body, rel), title=title, slug=f"todos-{title}"[:60],
                                          thread_id=thread_id, project=entry["project"], scope="todos",
                                          options={**options, "todo": True})
            else:
                task = await tasks.submit(chat_brief(entry, body, rel), title=title, slug=f"chat-{title}"[:60],
                                          thread_id=thread_id, project=entry["project"], scope="chat",
                                          options=options)
            if story:
                save_context(task.output_dir(config.assets_dir, config.default_project), rel, story)
            # The thread records what the player typed; the snapshot is for the agent.
            await note(thread_id, text)
            await publish_message(thread_id, ThreadMessage(
                role="dex", kind="tasks", text=f"Started a chat: {task.title}",
                data={"tasks": [task.to_json(config.assets_dir)]},
            ))
            return {"chatId": task.id, "taskId": task.id, "delivered": "started"}

        members = await chain(entry, body.chatId)
        if members[0].thread_id != thread_id:
            raise HTTPException(404, "that chat is in another thread")
        latest = tasks.merge_live(members[-1])
        await note(thread_id, text)
        # What the agent reads: the player's words, and what is on their screen now
        # (they may have moved on since the chat began). Follow-ups share the package.
        if story:
            save_context(latest.output_dir(config.assets_dir, config.default_project), rel, story)
        text = text + context_block(story, rel, body.context.e2e)
        # Running here: straight into the agent, which stops its turn for it,
        # and the switch applies to what it starts from now on.
        live = tasks.live.get(latest.id)
        if live is not None and live.inbox is not None:
            live.options = {**(live.options or {}), **options}
            await tasks.tasks.set_options(latest.id, live.options)
        if tasks.say(latest.id, text) is not None:
            return {"chatId": body.chatId, "taskId": latest.id, "delivered": "live"}
        # Paused: end that attempt, so the message continues it as the next one.
        if latest.state is TaskState.PAUSED:
            await tasks.cancel(latest.id)
            latest = await tasks.tasks.get(latest.id) or latest
        if not latest.state.terminal:
            # Queued, or starting up: held until it stops, then the next attempt.
            await tasks.queue_message(latest.id, text, options=options)
            return {"chatId": body.chatId, "taskId": latest.id, "delivered": "queued"}
        await tasks.queue_message(latest.id, text, options=options)  # stopped: starts the next attempt at once
        members = await chain(entry, body.chatId)
        return {"chatId": body.chatId, "taskId": members[-1].id, "delivered": "followup"}

    @router.get("/{project}/chats/{chat_id}/events")
    async def chat_events(
        chat_id: str,
        after: int | None = Query(default=None, description="the last seq seen; none or 0 = from the start"),
        limit: int = Query(default=PAGE, ge=1, le=PAGE),
        entry: dict = Depends(access),
    ) -> dict[str, Any]:
        members = await chain(entry, chat_id)
        since = after or 0
        rows = await db.pool.fetch(
            """SELECT seq, task_id, type, data, extract(epoch from ts)::float8 AS ts
                 FROM events WHERE task_id = ANY($1::text[]) AND seq > $2
                 ORDER BY seq LIMIT $3""",
            [t.id for t in members], since, limit + 1,
        )
        more = len(rows) > limit
        rows = rows[:limit]
        events = []
        for r in rows:
            data = r["data"]
            if isinstance(data, str):
                data = json.loads(data)
            events.append(Event(type=r["type"], data=data or {}, task_id=r["task_id"], seq=r["seq"], ts=r["ts"]).to_json())
        last = rows[-1]["seq"] if rows else since
        live = [tasks.merge_live(t) for t in members]
        return {
            "chat": _summary(chat_id, live, await CostStore(db).by_tasks([t.id for t in live])),
            "attempts": [{"taskId": t.id, "attempt": t.attempt, "state": t.state.value} for t in live],
            "events": _merge(events),
            "last": last,
            "more": more,
            "now": time.time(),
        }

    async def act(entry: dict, chat_id: str, run: Any) -> dict[str, Any]:
        members = await chain(entry, chat_id)
        result = await run(members[-1].id)
        if result is None or result is False:
            raise HTTPException(409, "the chat cannot do that in its current state")
        members = await chain(entry, chat_id)
        live = [tasks.merge_live(t) for t in members]
        return {"chat": _summary(chat_id, live, await CostStore(db).by_tasks([t.id for t in live]))}

    @router.post("/{project}/chats/{chat_id}/pause")
    async def pause(chat_id: str, entry: dict = Depends(access)) -> dict[str, Any]:
        out = await act(entry, chat_id, tasks.pause_task)
        await tasks.rebalance()
        return out

    @router.post("/{project}/chats/{chat_id}/resume")
    async def resume(chat_id: str, entry: dict = Depends(access)) -> dict[str, Any]:
        out = await act(entry, chat_id, tasks.resume_in_place)
        await tasks.rebalance()
        return out

    @router.post("/{project}/chats/{chat_id}/cancel")
    async def cancel(chat_id: str, entry: dict = Depends(access)) -> dict[str, Any]:
        return await act(entry, chat_id, tasks.cancel)

    return router
