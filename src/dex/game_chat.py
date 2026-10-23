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
- `POST …/threads/<thread>/messages {text, chatId?, context?}`: without
  `chatId` starts a chat; with one it goes to that chat at once: straight
  into the running agent (it interrupts its turn), or, if the chat has
  stopped, as the next attempt that resumes the same session.
- `GET  …/chats/<chat>/events?after=<seq>`: the chat's events after `seq`
  (none or 0: all of them), with streamed deltas merged, for polling.
- `POST …/chats/<chat>/pause | resume | cancel`.
"""

from __future__ import annotations

import hmac
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .config import Config
from .models import Event, Task, TaskState
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


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    #: The chat to continue (its first task's id, as listed); none starts a new one.
    chatId: str | None = None
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


def chat_brief(entry: dict[str, Any], message: ChatMessage) -> str:
    """The task brief for a new chat: who is talking, where the game is, then the message."""
    game = entry.get("game") or entry.get("project")
    root = Path(entry.get("root") or ".").expanduser()
    dests = [(k, _inside(root, v)) for k, v in sorted(message.context.destinations.items())]
    dest_lines = "\n".join(f"  - {k}: `{v}`" for k, v in dests if v) or "  - (the game gave none; use `public/assets/<kind>/<name>/`)"
    view = message.context.view.strip()
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

1. Build assets as packages in your task directory with this project's
   skills, and check them as the project brief says (validate, look at the
   previews).
2. Install them into the game yourself:
   `catalog.export(pkg, game_root=Path("{root}"))`, or
   `story.export_story(pkg, Path("{root}"))` for a story. That writes
   `public/assets/<kind>/<name>/` and the game's `public/assets/index.json`.
3. If the game needs code to use the new asset (a new model in a scene, a new
   sound on an event, a new lesson in a menu), change the game's code under
   `{root}`, keeping to its `AGENTS.md`, and run its tests
   (`npx vitest run` in that folder) before you finish.
4. Do not commit, push or publish anything, and do not touch other games or
   dex itself. The player reloads the game to see the change.

## Where the player is

{view or "(the game did not say)"}

## The player's message

{message.text.strip()}
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


def _summary(chat_id: str, members: list[Task]) -> dict[str, Any]:
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
        "costUsd": round(sum(t.cost_usd or 0 for t in members), 4),
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
        if first is None or first.project != entry["project"] or not first.is_chat:
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
        found = [t for t in await tasks.tasks.list(thread_id=thread_id, limit=10_000) if t.is_chat]
        chats = [_summary(cid, m) for cid, m in _chains(found).items()]
        chats.sort(key=lambda c: c["updatedAt"], reverse=True)
        return {"project": entry["project"], "threadId": thread_id, "chats": chats}

    @router.post("/{project}/threads/{thread_id}/messages")
    async def send(thread_id: str, body: ChatMessage, entry: dict = Depends(access)) -> dict[str, Any]:
        await chats_thread(entry, thread_id)
        text = body.text.strip()
        if not body.chatId:
            title = (body.title or text.splitlines()[0])[:80]
            task = await tasks.submit(chat_brief(entry, body), title=title, slug=f"chat-{title}"[:60],
                                      thread_id=thread_id, project=entry["project"], scope="chat")
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
        # Running here: straight into the agent, which stops its turn for it.
        if tasks.say(latest.id, text) is not None:
            return {"chatId": body.chatId, "taskId": latest.id, "delivered": "live"}
        # Paused: end that attempt, so the message continues it as the next one.
        if latest.state is TaskState.PAUSED:
            await tasks.cancel(latest.id)
            latest = await tasks.tasks.get(latest.id) or latest
        if not latest.state.terminal:
            # Queued, or starting up: held until it stops, then the next attempt.
            await tasks.queue_message(latest.id, text)
            return {"chatId": body.chatId, "taskId": latest.id, "delivered": "queued"}
        await tasks.queue_message(latest.id, text)  # stopped: starts the next attempt at once
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
            "chat": _summary(chat_id, live),
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
        return {"chat": _summary(chat_id, [tasks.merge_live(t) for t in members])}

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
