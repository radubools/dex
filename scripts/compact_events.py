"""Compact the event history of finished tasks, as dex now does as it goes (bus.py).

    .venv/bin/python scripts/compact_events.py --dry-run     # what it would do
    .venv/bin/python scripts/compact_events.py               # do it, a task at a time

For every task that has ended (succeeded, failed, cancelled):
- each streamed block (its text_delta or thinking_delta rows, by block id) becomes
  its first row, holding the whole text; the rest are deleted. The first keeps its
  seq, so a replay shows the block in its place, as the stream did;
- its asset events come down to the last per path (the UI keeps only whether a
  path is there).

A task at a time, each in one transaction, with a pause between, so dex and its
running tasks keep the database. Running tasks are never touched. The table's
disk space is reused by later rows; shrinking the file would need VACUUM FULL,
which locks the table, so it is not done here.
"""

from __future__ import annotations

import argparse
import asyncio
import time

import asyncpg

DSN = "postgresql://127.0.0.1/dex"

GROUPS = """
    SELECT type, data->>'id' AS bid, min(seq) AS first, count(*) AS n,
           string_agg(data->>'delta', '' ORDER BY seq) AS text
    FROM events WHERE task_id = $1 AND type IN ('text_delta', 'thinking_delta')
    GROUP BY 1, 2 HAVING count(*) > 1"""
OLD_ASSETS = """
    SELECT count(*) FROM events e WHERE e.task_id = $1 AND e.type = 'asset'
      AND EXISTS (SELECT 1 FROM events n WHERE n.task_id = e.task_id AND n.type = 'asset'
                  AND n.data->>'path' = e.data->>'path' AND n.seq > e.seq)"""


async def compact(conn: asyncpg.Connection, task_id: str, dry: bool) -> tuple[int, int]:
    groups = await conn.fetch(GROUPS, task_id)
    deltas = sum(g["n"] - 1 for g in groups)
    assets = await conn.fetchval(OLD_ASSETS, task_id)
    if dry or not (deltas or assets):
        return deltas, assets
    async with conn.transaction():
        for g in groups:
            await conn.execute(
                "UPDATE events SET data = jsonb_set(data, '{delta}', to_jsonb($2::text)) WHERE seq = $1",
                g["first"], g["text"] or "")
            await conn.execute(
                "DELETE FROM events WHERE task_id = $1 AND type = $2 AND data->>'id' = $3 AND seq > $4",
                task_id, g["type"], g["bid"], g["first"])
        await conn.execute(OLD_ASSETS.replace("SELECT count(*) FROM events e", "DELETE FROM events e"), task_id)
    return deltas, assets


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pause", type=float, default=0.05, help="seconds between tasks")
    ap.add_argument("--limit", type=int, default=0, help="at most this many tasks (0: all)")
    a = ap.parse_args()
    conn = await asyncpg.connect(DSN)
    tasks = await conn.fetch(
        """SELECT t.id FROM tasks t
           WHERE t.state IN ('succeeded', 'failed', 'cancelled')
             AND EXISTS (SELECT 1 FROM events e WHERE e.task_id = t.id
                         AND e.type IN ('text_delta', 'thinking_delta', 'asset'))
           ORDER BY t.finished_at NULLS FIRST""")
    if a.limit:
        tasks = tasks[:a.limit]
    total_d = total_a = done = 0
    t0 = time.time()
    for row in tasks:
        d, s = await compact(conn, row["id"], a.dry_run)
        total_d += d
        total_a += s
        done += 1
        if done % 200 == 0:
            print(f"{done}/{len(tasks)} tasks: {total_d:,} delta rows and {total_a:,} asset rows "
                  f"{'would go' if a.dry_run else 'removed'} ({time.time() - t0:.0f} s)", flush=True)
        if not a.dry_run and a.pause:
            await asyncio.sleep(a.pause)
    print(f"done: {done} finished tasks, {total_d:,} delta rows and {total_a:,} asset rows "
          f"{'would be removed' if a.dry_run else 'removed'} in {time.time() - t0:.0f} s", flush=True)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
