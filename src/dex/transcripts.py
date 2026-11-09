"""How full a task's context window was, from the agent's own session transcript.

`tasks.context_tokens` is written by the runner on every model call, but only
since it existed: a task that finished before then has none, and its panel
showed turns and cost and no context size. The Claude CLI keeps every session
as `<projects>/<cwd>/<session_id>.jsonl`, each main-agent call with its usage,
so the last of those is the figure the runner would have stored. Read once at
startup, and only for tasks that have no figure; OpenCode and Antigravity
sessions have no such file and stay without one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from . import pricing

log = logging.getLogger(__name__)

#: How much of a transcript's end to read first. A call's record is small, but
#: a tool result between two calls can be large; the whole file is the fallback.
TAIL_BYTES = 1 << 20


def projects_dir() -> Path:
    """Where the Claude CLI keeps its sessions."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "projects"


def index(root: Path) -> dict[str, Path]:
    """Every session transcript under `root`, by session id."""
    found: dict[str, Path] = {}
    try:
        directories = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return found
    for directory in directories:
        try:
            for path in directory.glob("*.jsonl"):
                found.setdefault(path.stem, path)
        except OSError:
            continue
    return found


def _last_call(lines: list[bytes]) -> int:
    for line in reversed(lines):
        try:
            record: Any = json.loads(line)
        except ValueError:
            continue  # the partial first line of a tail, or a torn write
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        # A subagent's calls have a window of their own.
        if record.get("isSidechain"):
            continue
        message = record.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        context = pricing.context_tokens(pricing.tokens(usage)) if isinstance(usage, dict) else 0
        # A synthetic message (an API error the CLI recorded) reports zeros.
        if context > 0:
            return context
    return 0


def last_context_tokens(path: Path) -> int:
    """The main agent's last call in one transcript, in tokens; 0 when none."""
    try:
        with path.open("rb") as transcript:
            size = transcript.seek(0, os.SEEK_END)
            transcript.seek(max(0, size - TAIL_BYTES))
            found = _last_call(transcript.read().splitlines())
            if found or size <= TAIL_BYTES:
                return found
            transcript.seek(0)
            return _last_call(transcript.read().splitlines())
    except OSError:
        return 0


async def backfill(store: Any, root: Path | None = None) -> int:
    """Fill `context_tokens` for every task without one, from its transcript.

    A `TaskStore`. Returns how many tasks got a figure. Never raises: a task
    left without one only shows no context size, as before.
    """
    try:
        missing = await store.sessions_without_context()
        if not missing:
            return 0
        found = await asyncio.to_thread(
            context_for, sorted({session for _, session in missing}), root or projects_dir())
        filled = await store.fill_context_tokens(
            {task: found[session] for task, session in missing if session in found})
        log.info("context size filled in for %d of %d tasks from their transcripts",
                 filled, len(missing))
        return filled
    except Exception:
        log.exception("could not fill in context sizes from transcripts")
        return 0


def context_for(sessions: list[str], root: Path) -> dict[str, int]:
    """The context size for each session that has a transcript with a call in it."""
    paths = index(root)
    sizes: dict[str, int] = {}
    for session in sessions:
        path = paths.get(session)
        if path is not None and (tokens := last_context_tokens(path)):
            sizes[session] = tokens
    return sizes
