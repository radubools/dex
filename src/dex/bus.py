"""Event fan-out, with Postgres as the record and the sequence authority.

Every event is written before it is delivered, so `seq` means the same thing to
a client reconnecting after a restart as it does to one that never dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from .db import Database
from .models import Event

log = logging.getLogger("dex.bus")

#: Most events replayed to a client on connect.
REPLAY_LIMIT = 3000


class EventBus:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._pending: asyncio.Queue[Event] = asyncio.Queue(maxsize=10_000)
        self._writer: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._writer = asyncio.create_task(self._drain(), name="dex-event-writer")

    async def stop(self) -> None:
        if self._writer is None:
            return
        await self._pending.join()
        self._writer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._writer
        self._writer = None

    def publish(self, event: Event) -> Event:
        """Queue an event. Returns immediately; `seq` is assigned by the writer."""
        try:
            self._pending.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("event queue full; dropping %s", event.type)
        return event

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
                for queue in self._subscribers:
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        pass  # a stalled client must not back-pressure the agent
            except Exception:
                log.exception("could not record event %s", event.type)
            finally:
                self._pending.task_done()

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
