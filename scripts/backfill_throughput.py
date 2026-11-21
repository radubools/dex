#!/usr/bin/env python
"""Time the model calls of tasks that ran before dex recorded throughput.

    .venv/bin/python scripts/backfill_throughput.py --dry-run
    .venv/bin/python scripts/backfill_throughput.py

A task's events still say when each model call began, streamed and ended: a
tool's result, or the run starting, is a call's boundary; its text and thinking
deltas (the first row of each block survives compaction), block ends and tool
calls are its tokens; each running `cost` event is its usage. They are fed to
the same meter the runner uses live (`dex.throughput`), so a call's prefill and
decode are measured the same way.

What the events do not keep is each call's tokens: `task_usage` has them per
task, hour and skill only. So an hour's tokens are shared out over its calls,
input by how far each call's context grew past the last one's (what it read
fresh; evenly where a context is missing), output evenly, and only the share
of calls that were timed on a side is counted against that side's seconds (an even share only when at least half were,
`MIN_SHARE`). A cold call (a turn's first) has no prefill and an
unstreamed one no split, as live. The rows are spread over the hour's skills by
their tokens, and marked `inferred`.

Claude's output in `task_usage` is too low before this version: it came from
the per-block messages, which carry what had been written when each block
began, not the message's final count. A Claude task's output is scaled up to
the total its result reported, where that total is the task's own (one result,
in a session it started); otherwise its out side is left untimed.

Only inserts: a task that already has throughput rows (timed live, or by an
earlier run of this) is left alone, so reruns are harmless. Running tasks are
skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dex import throughput  # noqa: E402

DSN = "postgresql://127.0.0.1/dex"

TYPES = ("task_state", "task_message_delivered", "tool", "tool_result", "text_delta",
         "thinking_delta", "block_end", "thinking", "text", "cost", "result")

TASKS = """
    SELECT t.id, t.model, t.state, t.output_tokens, t.resumed_from FROM tasks t
    WHERE EXISTS (SELECT 1 FROM task_usage u WHERE u.task_id = t.id
                  AND u.input_tokens + u.cache_write_tokens + u.output_tokens > 0)
      AND NOT EXISTS (SELECT 1 FROM task_throughput p WHERE p.task_id = t.id)
      AND t.state NOT IN ('running', 'awaiting_input')
    ORDER BY t.created_at"""
EVENTS = """
    SELECT type, data, extract(epoch from ts)::float8 AS ts FROM events
    WHERE task_id = $1 AND type = ANY($2::text[]) ORDER BY seq"""
USAGE = """
    SELECT hour, skill, input_tokens + cache_write_tokens AS in_tokens, output_tokens AS out_tokens
    FROM task_usage WHERE task_id = $1"""
INSERT = """
    INSERT INTO task_throughput (task_id, hour, skill, in_tokens, in_seconds,
                                 out_tokens, out_seconds, inferred)
    VALUES ($1, $2, $3, $4, $5, $6, $7, true)
    ON CONFLICT (task_id, hour, skill) DO NOTHING"""


#: The least share of an hour's calls timed on a side for it to be counted.
MIN_SHARE = 0.5


def hour_of(at: float) -> datetime:
    return datetime.fromtimestamp(at, UTC).replace(minute=0, second=0, microsecond=0)


def calls(events: list[dict], model: str | None = None) -> list[throughput.Call]:
    """A task's model calls, timed from its events by the runner's own meter.

    Claude's running cost comes per block, mid-call; every other agent's at the
    end of its call, which it ends."""
    ends = not str(model or "").startswith("claude")
    meter = throughput.Meter()
    started: dict[str, float] = {}
    turn = None
    for e in events:
        kind, data, at = e["type"], e["data"], e["ts"]
        if kind in ("text_delta", "thinking_delta"):
            # OpenCode's block ids carry their turn's start (`oc-<session>-<t>:<i>`):
            # a new one is a new `opencode run`, which the runner's query marks live.
            stamp = str(data.get("id") or "").rpartition(":")[0].rpartition("-")[2]
            if str(data.get("id") or "").startswith("oc-") and stamp != turn:
                if turn is not None:
                    meter.boundary(at, cold=True)
                turn = stamp
        if kind == "task_state":
            if data.get("state") == "running":
                meter.boundary(at, cold=True)
        elif kind == "task_message_delivered":
            meter.boundary(at, cold=True)
        elif kind == "tool_result":
            meter.boundary(at)
        elif kind in ("text_delta", "thinking_delta"):
            meter.token(at)
            started.setdefault(str(data.get("id")), at)
        elif kind == "block_end":
            first = started.pop(str(data.get("id")), None)
            if first is not None:
                meter.token(at)
                if at - first > throughput.STREAM_GAP:
                    meter.stream()
        elif kind in ("tool", "thinking", "text"):
            meter.token(at)
        elif kind == "cost" and data.get("estimate") is True:
            # One per model call that added tokens (Claude: per block, which
            # finds the same call, as no boundary comes between them).
            meter.usage(at, ends=ends, context=int(data.get("contextTokens") or 0))
    return meter.calls


def out_scale(task: dict, events: list[dict], usage: list[dict]) -> float | None:
    """What to multiply a task's recorded output by: None leaves it untimed."""
    if not str(task["model"] or "").startswith("claude"):
        return 1.0
    recorded = sum(u["out_tokens"] for u in usage)
    results = sum(1 for e in events if e["type"] == "result")
    if task["resumed_from"] or results != 1 or not task["output_tokens"] or not recorded:
        return None
    return max(1.0, task["output_tokens"] / recorded)


def rows(calls_: list[throughput.Call], usage: list[dict], scale: float | None = 1.0) -> list[dict]:
    """Throughput rows for a task: its hours' tokens against its calls' time.

    `scale` corrects the recorded output (`out_scale`); None times none of it."""
    by_hour: dict[datetime, list[throughput.Call]] = collections.defaultdict(list)
    read: dict[int, int | None] = {}
    before = 0
    for call in calls_:
        by_hour[hour_of(call.claimed_at)].append(call)
        # What a call read fresh: how far its context grew past the last one's
        # (the last reply and the tool results since), as caches keep the rest.
        read[id(call)] = max(0, call.context - before) if call.context else None
        before = call.context or before
    usage_by_hour: dict[datetime, list[dict]] = collections.defaultdict(list)
    for u in usage:
        usage_by_hour[u["hour"].astimezone(UTC)].append(u)
    out = []
    for hour, found in usage_by_hour.items():
        timed = by_hour.get(hour) or []
        if not timed:
            continue
        n = len(timed)
        prefilled = [c.prefill for c in timed if c.prefill > 0]
        decoded = [c.decode for c in timed if c.decode > 0]
        # An even share is only a fair guess when it covers most calls: a few
        # stray ones would put a big hour's tokens on a moment's time.
        even = lambda k: k / n if k / n >= MIN_SHARE else 0.0  # noqa: E731
        weights = [read[id(c)] for c in timed]
        if all(w is not None for w in weights) and sum(weights) > 0:
            in_share = sum(w for c, w in zip(timed, weights) if c.prefill > 0) / sum(weights)
        else:
            in_share = even(len(prefilled))
        out_share = even(len(decoded))
        in_seconds, out_seconds = sum(prefilled), sum(decoded)
        total_in = sum(u["in_tokens"] for u in found)
        total_out = sum(u["out_tokens"] for u in found)
        for u in found:
            # Each skill's part of the hour, by its tokens: the hour's rate.
            fin = u["in_tokens"] / total_in if total_in else 0.0
            fout = u["out_tokens"] / total_out if total_out else 0.0
            row = {
                "hour": hour, "skill": u["skill"],
                "in_tokens": round(u["in_tokens"] * in_share), "in_seconds": in_seconds * fin,
                "out_tokens": round(u["out_tokens"] * out_share * (scale or 0)),
                "out_seconds": out_seconds * fout,
            }
            if row["in_tokens"] <= 0 or row["in_seconds"] <= 0:
                row["in_tokens"], row["in_seconds"] = 0, 0.0
            if row["out_tokens"] <= 0 or row["out_seconds"] <= 0:
                row["out_tokens"], row["out_seconds"] = 0, 0.0
            if row["in_tokens"] or row["out_tokens"]:
                out.append(row)
    return out


def kind_of(model: str | None) -> str:
    """How the summary groups models: oc:local/x and claude-opus-5 stay apart."""
    return model or "unknown"


async def main(dry: bool, limit: int | None, dsn: str = DSN) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        if not await conn.fetchval("SELECT to_regclass('task_throughput') IS NOT NULL"):
            if not dry:
                raise SystemExit("no task_throughput table yet: start dex on this version first")
            # Before the server has made the table, nothing has throughput yet.
            tasks = await conn.fetch(TASKS.replace(
                "AND NOT EXISTS (SELECT 1 FROM task_throughput p WHERE p.task_id = t.id)", ""))
        else:
            tasks = await conn.fetch(TASKS)
        if limit:
            tasks = tasks[:limit]
        sums: dict[str, list[float]] = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
        written = skipped = 0
        for task in tasks:
            events = [
                {"type": e["type"], "ts": e["ts"],
                 "data": e["data"] if isinstance(e["data"], dict) else json.loads(e["data"])}
                for e in await conn.fetch(EVENTS, task["id"], list(TYPES))
            ]
            usage = [dict(u) for u in await conn.fetch(USAGE, task["id"])]
            found = rows(calls(events, task["model"]), usage, out_scale(task, events, usage))
            if not found:
                skipped += 1
                continue
            s = sums[kind_of(task["model"])]
            s[0] += 1
            for r in found:
                s[1] += r["in_tokens"]; s[2] += r["in_seconds"]
                s[3] += r["out_tokens"]; s[4] += r["out_seconds"]
            written += len(found)
            if not dry:
                await conn.executemany(INSERT, [
                    (task["id"], r["hour"], r["skill"], r["in_tokens"], r["in_seconds"],
                     r["out_tokens"], r["out_seconds"]) for r in found
                ])
        verb = "would insert" if dry else "inserted"
        print(f"{len(tasks)} tasks without throughput; {verb} {written} task_throughput rows "
              f"for {len(tasks) - skipped}; {skipped} had no timed call")
        print(f"{'model':40} {'tasks':>6} {'in t/s':>9} {'out t/s':>9}")
        for model, (n, ti, si, to, so) in sorted(sums.items(), key=lambda kv: -kv[1][0]):
            fmt = lambda t, sec: f"{t / sec:9.1f}" if sec else f"{'-':>9}"  # noqa: E731
            print(f"{model:40} {int(n):6} {fmt(ti, si)} {fmt(to, so)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="say what would be written, write nothing")
    parser.add_argument("--limit", type=int, help="only the first N tasks (oldest first)")
    parser.add_argument("--dsn", default=DSN, help=f"the database (default {DSN})")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, args.limit, args.dsn))
