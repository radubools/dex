"""Event fan-out, with Postgres as the record and the sequence authority.

Every event is written before it is delivered, so `seq` means the same thing to
a client reconnecting after a restart as it does to one that never dropped.

Streamed text is kept small in two steps (design/03):
- Deltas are grouped as they are published: a block's text and thinking deltas
  go out together, every DELTA_GROUP of them or every DELTA_FLUSH_S, whichever
  comes first. Any other event of the same task flushes them first, so the order
  a client sees never changes. Live viewers still see text stream, in slightly
  larger pieces.
- A finished block is one row: when its `block_end` is written, the block's
  first delta takes the whole text and the rest are deleted. The first keeps its
  `seq`, so a replay gets the block in its place, and the UI, which joins a
  block's deltas by id, shows it the same. A task that ends compacts any block
  left open, and its `asset` events down to the last per path (the UI keeps only
  whether a path is there).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from .db import Database
from .models import Event

log = logging.getLogger("dex.bus")

#: Most events replayed to a client on connect.
REPLAY_LIMIT = 3000
#: Deltas of one block written as one event: this many, or after this long.
DELTA_GROUP = 8
DELTA_FLUSH_S = 0.25
DELTA_TYPES = ("text_delta", "thinking_delta")
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


class EventBus:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._pending: asyncio.Queue[Event] = asyncio.Queue(maxsize=10_000)
        self._writer: asyncio.Task[None] | None = None
        self._flusher: asyncio.Task[None] | None = None
        #: Deltas waiting to go out, per (task, type, block id): [first event, parts, since].
        self._held: dict[tuple[Any, str, Any], list[Any]] = {}
        #: Written deltas of blocks still open, per (task, block id): first seq,
        #: the other seqs, and the text so far (compacted at the block's end).
        self._open: dict[tuple[Any, Any], dict[str, Any]] = {}

    async def start(self) -> None:
        self._writer = asyncio.create_task(self._drain(), name="dex-event-writer")
        self._flusher = asyncio.create_task(self._flush_loop(), name="dex-delta-flusher")

    async def stop(self) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flusher
            self._flusher = None
        self._flush(None)
        if self._writer is None:
            return
        await self._pending.join()
        self._writer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._writer
        self._writer = None

    def publish(self, event: Event) -> Event:
        """Queue an event. Returns immediately; `seq` is assigned by the writer.

        A text or thinking delta is held and grouped with the next ones of its
        block (DELTA_GROUP, DELTA_FLUSH_S); any other event of its task sends
        what is held first, so nothing is reordered."""
        if event.type in DELTA_TYPES and event.data.get("id") is not None:
            key = (event.task_id, event.type, event.data["id"])
            held = self._held.get(key)
            if held is None:
                self._held[key] = [event, [str(event.data.get("delta") or "")], time.monotonic()]
            else:
                held[1].append(str(event.data.get("delta") or ""))
                if len(held[1]) >= DELTA_GROUP:
                    self._send(self._held.pop(key))
            return event
        self._flush(event.task_id)
        self._put(event)
        return event

    def _put(self, event: Event) -> None:
        try:
            self._pending.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("event queue full; dropping %s", event.type)

    def _send(self, held: list[Any]) -> None:
        first, parts, _since = held
        self._put(Event(type=first.type, data={**first.data, "delta": "".join(parts)}, task_id=first.task_id,
                        ts=first.ts, project=first.project))

    def _flush(self, task_id: Any, older_than: float | None = None) -> None:
        """Send held deltas: one task's (None: everyone's), or only those held long enough."""
        now = time.monotonic()
        for key in [k for k, h in self._held.items()
                    if (task_id is None or k[0] == task_id) and (older_than is None or now - h[2] >= older_than)]:
            self._send(self._held.pop(key))

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(DELTA_FLUSH_S / 2)
            self._flush(None, older_than=DELTA_FLUSH_S)

    async def _drain(self) -> None:
        """One writer, so rows land in the order they were published."""
        while True:
            event = await self._pending.get()
            try:
                seq = await self.db.pool.fetchval(
                    """INSERT INTO events (task_id, thread_id, type, data, ts, project)
                       VALUES ($1, $2, $3, $4::jsonb, to_timestamp($5), $6)
                       RETURNING seq""",
                    event.task_id,
                    event.data.get("threadId"),
                    event.type,
                    Database.dump(event.data),
                    event.ts,
                    event.project,
                )
                event.seq = int(seq)
                await self._compact_after(event)
                for queue in self._subscribers:
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        pass  # a stalled client must not back-pressure the agent
            except Exception:
                log.exception("could not record event %s", event.type)
            finally:
                self._pending.task_done()

    async def _compact_after(self, event: Event) -> None:
        """Keep finished streams small: a block's deltas become its first row once
        it ends, and a task that ends leaves one asset event per path. Never fails
        the write: the record is complete either way, only larger."""
        try:
            if event.type in DELTA_TYPES:
                key = (event.task_id, event.data.get("id"))
                block = self._open.setdefault(key, {"first": event.seq, "rest": [], "parts": []})
                if block["first"] != event.seq:
                    block["rest"].append(event.seq)
                block["parts"].append(str(event.data.get("delta") or ""))
            elif event.type == "block_end":
                await self._compact_block((event.task_id, event.data.get("id")))
            elif event.type == "task_state" and event.data.get("state") in TERMINAL_STATES and event.task_id:
                for key in [k for k in self._open if k[0] == event.task_id]:
                    await self._compact_block(key)
                await self.db.pool.execute(
                    """DELETE FROM events e WHERE e.task_id = $1 AND e.type = 'asset'
                         AND EXISTS (SELECT 1 FROM events n WHERE n.task_id = e.task_id AND n.type = 'asset'
                                     AND n.data->>'path' = e.data->>'path' AND n.seq > e.seq)""",
                    event.task_id)
        except Exception:
            log.exception("could not compact events after %s", event.type)

    async def _compact_block(self, key: tuple[Any, Any]) -> None:
        block = self._open.pop(key, None)
        if not block or not block["rest"]:
            return
        async with self.db.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE events SET data = jsonb_set(data, '{delta}', to_jsonb($2::text)) WHERE seq = $1",
                block["first"], "".join(block["parts"]))
            await conn.execute("DELETE FROM events WHERE seq = ANY($1::bigint[])", block["rest"])

    async def history(
        self,
        task_id: str | None = None,
        after_seq: int = 0,
        limit: int = REPLAY_LIMIT,
        projects: list[str] | None = None,
    ) -> list[Event]:
        """Events after `after_seq`, newest-biased.

        When more than `limit` events qualify, the most recent ones are
        returned rather than the oldest. Taking the oldest would leave a client
        permanently behind: it would replay ancient history, set its cursor
        there, and then discard every live event as "already seen".
        """
        scoped = projects is not None
        if task_id:
            rows = await self.db.pool.fetch(
                """SELECT * FROM (
                       SELECT seq, task_id, type, data, project,
                              extract(epoch from ts)::float8 AS ts
                       FROM events
                       WHERE task_id = $1 AND seq > $2
                         AND (NOT $4::bool OR project = ANY($5::text[]))
                       ORDER BY seq DESC LIMIT $3
                   ) recent ORDER BY seq""",
                task_id, after_seq, limit, scoped, list(projects or []),
            )
        else:
            rows = await self.db.pool.fetch(
                """SELECT * FROM (
                       SELECT seq, task_id, type, data, project,
                              extract(epoch from ts)::float8 AS ts
                       FROM events
                       WHERE seq > $1
                         AND (NOT $3::bool OR project = ANY($4::text[]))
                       ORDER BY seq DESC LIMIT $2
                   ) recent ORDER BY seq""",
                after_seq, limit, scoped, list(projects or []),
            )
        return [
            Event(
                type=row["type"],
                data=Database.load(row["data"]),
                task_id=row["task_id"],
                seq=row["seq"],
                ts=row["ts"],
                project=row["project"],
            )
            for row in rows
        ]

    async def subscribe(
        self,
        task_id: str | None = None,
        after_seq: int = 0,
        projects: list[str] | None = None,
    ) -> AsyncIterator[Event]:
        """Replay what was missed from Postgres, then stream live events.

        `projects` restricts the stream to those projects. None means no
        restriction, which is what an admin and the service credential get; a
        list is applied to both the replay and the live fan-out, because a
        subscriber who may not see a project must not receive its events by
        either route.

        An event with no project reaches nobody who is restricted. Those are
        dex's own (settings changes, thread-level chatter on an unassigned
        thread), and leaking them to a scoped user would be a small hole with no
        upside.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        allowed = set(projects) if projects is not None else None
        try:
            replayed = await self.history(task_id, after_seq, projects=projects)
            last_seq = replayed[-1].seq if replayed else after_seq
            for event in replayed:
                yield event
            while True:
                event = await queue.get()
                if event.seq <= last_seq:
                    continue  # already delivered during replay
                if task_id is not None and event.task_id != task_id:
                    continue
                if allowed is not None and event.project not in allowed:
                    continue
                yield event
        finally:
            self._subscribers.discard(queue)
