#!/usr/bin/env python
"""Rebuild `task_usage` for tasks that finished before it existed.

Run once per dex, after the server has created the table:

    .venv/bin/python scripts/backfill_usage.py --dry-run
    .venv/bin/python scripts/backfill_usage.py

A task's row in the rollup comes from the best source left for it:

1. its session transcript: real per-message tokens and every tool call. Claude
   Code keeps these for about 30 days, and a follow-up resumes the session, so
   a message belongs to the newest task that existed when it was written;
2. its `cost` and `tool` events: when the money went and on which skill, with
   the tokens of the task's final result, where it has one, spread over that.

What a task really cost is not simply its `cost_usd`. A resumed session reports
everything before it too, so attempts count only what they added; a run that
died before its result has a running estimate that counted each message once
per content block, about three times. Every row is marked `inferred`. A task
that already has rows is left alone, so reruns are harmless.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import glob
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dex import pricing, skills  # noqa: E402

FIELDS = pricing.TOKEN_FIELDS
Row = dict  # {"at": datetime, "cost": float, "skill": str | None, **tokens}

#: How many times a running estimate counted each message (601 entries for 217
#: messages, 425 for 137, 139 for 45 in sampled transcripts).
ESTIMATE_REPEATS = 3


def hour_of(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def transcripts() -> dict[str, list[str]]:
    """Session id -> its transcript and its subagents' transcripts."""
    base = os.path.expanduser("~/.claude/projects")
    found: dict[str, list[str]] = collections.defaultdict(list)
    for path in glob.glob(f"{base}/*/*.jsonl"):
        found[Path(path).stem].append(path)
    for path in glob.glob(f"{base}/*/*/subagents/*.jsonl"):
        found[Path(path).parents[1].name].append(path)
    return found


def read_session(paths: list[str], attribution: skills.Attribution) -> list[dict]:
    """A session's messages, once each, oldest first, with the skill in use."""
    messages: dict[str, dict] = {}
    for path in paths:
        current = None
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                message = entry.get("message") or {}
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        hits = attribution.find(block.get("name", ""), block.get("input"))
                        if hits and current not in hits:
                            current = hits[0]
                usage, key = message.get("usage"), message.get("id")
                if not usage or not key or not entry.get("timestamp"):
                    continue
                messages[key] = {
                    "id": key,
                    "at": datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")),
                    "model": message.get("model"),
                    "usage": usage,
                    # A message's own tool calls are what it was spent on.
                    "skill": current,
                }
    return sorted(messages.values(), key=lambda m: m["at"])


def from_transcript(task, messages: list[dict]) -> list[Row]:
    rows = []
    for m in messages:
        rows.append({
            "at": m["at"], "skill": m["skill"],
            "cost": pricing.estimate(m["model"] or task["model"], m["usage"]),
            **pricing.tokens(m["usage"]),
        })
    return rows


def from_events(task, events: list, attribution: skills.Attribution) -> list[Row]:
    rows, last, current = [], 0.0, None
    for event in events:
        if event["type"] == "tool":
            hits = attribution.find(event["name"] or "", {
                "file_path": event["file_path"], "command": event["command"], "path": event["path"],
            })
            if hits and current not in hits:
                current = hits[0]
        elif event["estimate"] == "true":
            spent = float(event["cost"] or 0)
            added = spent if spent < last else spent - last
            last = spent
            if added > 0:
                rows.append({"at": event["ts"], "cost": added, "skill": current,
                             **dict.fromkeys(FIELDS, 0)})
    if not rows:
        rows = [{"at": task["finished_at"] or task["created_at"], "cost": 1.0, "skill": current,
                 **dict.fromkeys(FIELDS, 0)}]
    known = sum(task[f] or 0 for f in FIELDS)
    if known:
        whole = sum(r["cost"] for r in rows)
        for row in rows:
            for f in FIELDS:
                row[f] = (task[f] or 0) * row["cost"] / whole
    return rows


def real_costs(tasks: list) -> dict[str, float]:
    """Each task's own share of what it reported, oldest first per session."""
    costs, reached = {}, collections.Counter()
    for task in sorted(tasks, key=lambda t: t["created_at"]):
        cost = task["cost_usd"] or 0.0
        if task["cost_is_estimate"]:
            costs[task["id"]] = cost / ESTIMATE_REPEATS
        else:
            key = task["session_id"] or task["id"]
            costs[task["id"]] = max(0.0, cost - reached[key])
            reached[key] = max(reached[key], cost)
    return costs


def rollup(task, rows: list[Row], cost: float) -> dict[tuple, dict]:
    """Fold a task's rows into its hours and skills, adding up to `cost`."""
    named = next((r["skill"] for r in rows if r["skill"]), "")
    if task["scope"] == "power":
        named = "power"
    whole = sum(r["cost"] for r in rows)
    factor = cost / whole if whole > 0 else 0.0
    folded: dict[tuple, dict] = {}
    for row in rows:
        skill = "power" if task["scope"] == "power" else row["skill"] or named
        into = folded.setdefault((hour_of(row["at"]), skill), collections.Counter())
        into["cost"] += row["cost"] * factor
        for f in FIELDS:
            into[f] += row[f]
    return folded


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workspace = Path(os.environ.get("DEX_WORKSPACE", Path.home() / "dex"))
    attribution = skills.attribution(workspace)
    db = await asyncpg.connect(os.environ.get("DEX_DATABASE_URL", "postgresql://127.0.0.1/dex"))

    tasks = await db.fetch(
        """SELECT * FROM tasks t WHERE cost_usd > 0 AND state <> 'running'
           AND NOT EXISTS (SELECT 1 FROM task_usage u WHERE u.task_id = t.id)
           ORDER BY created_at"""
    )
    real = real_costs(tasks)
    sessions = transcripts()
    by_session = collections.defaultdict(list)
    for task in tasks:
        if task["session_id"] in sessions:
            by_session[task["session_id"]].append(task)

    # Oldest task first, so a message copied into a fork stays with whoever
    # wrote it.
    assigned: dict[str, list[dict]] = collections.defaultdict(list)
    taken: set[str] = set()
    for session, members in sorted(by_session.items(), key=lambda kv: kv[1][0]["created_at"]):
        for message in read_session(sessions[session], attribution):
            if message["id"] in taken:
                continue
            owners = [t for t in members if t["created_at"] <= message["at"]]
            if owners:
                taken.add(message["id"])
                assigned[owners[-1]["id"]].append(message)

    events = collections.defaultdict(list)
    for event in await db.fetch(
        """SELECT task_id, type, ts, data->>'name' AS name,
                  data->'input'->>'file_path' AS file_path,
                  data->'input'->>'command' AS command,
                  data->'input'->>'path' AS path,
                  data->>'costUsd' AS cost, data->>'estimate' AS estimate
           FROM events WHERE type IN ('cost', 'tool') AND task_id IS NOT NULL ORDER BY seq"""
    ):
        events[event["task_id"]].append(event)

    # A session's messages price its whole cost at once, so its one final total
    # scales them all, whichever attempt they fell in.
    priced = collections.Counter()
    finals = collections.Counter()
    rows_of = {}
    for task in tasks:
        if task["id"] in assigned:
            rows_of[task["id"]] = from_transcript(task, assigned[task["id"]])
            priced[task["session_id"]] += sum(r["cost"] for r in rows_of[task["id"]])
            if not task["cost_is_estimate"]:
                finals[task["session_id"]] = max(finals[task["session_id"]], task["cost_usd"])

    records, sources, spend = [], collections.Counter(), collections.Counter()
    for task in tasks:
        rows = rows_of.get(task["id"])
        if rows:
            source = "transcript"
            whole = sum(r["cost"] for r in rows)
            session = task["session_id"]
            scale = finals[session] / priced[session] if finals[session] and priced[session] else 1.0
            cost = whole * scale
        else:
            rows, source = from_events(task, events.get(task["id"], []), attribution), "events"
            cost = real[task["id"]]
        sources[source] += 1
        spend[source] += cost
        for (hour, skill), v in rollup(task, rows, cost).items():
            records.append((task["id"], hour, skill, v["cost"], *(round(v[f]) for f in FIELDS), True))

    print(f"{len(tasks)} tasks, {len(records)} rows")
    print("reported:", round(sum(t["cost_usd"] for t in tasks)), "real:", round(sum(spend.values())))
    print("tasks by source:", dict(sources))
    print("spend by source:", {k: round(v) for k, v in spend.items()})
    if not args.dry_run:
        await db.executemany(
            """INSERT INTO task_usage (task_id, hour, skill, cost_usd, input_tokens,
                   cache_read_tokens, cache_write_tokens, output_tokens, thinking_tokens, inferred)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT (task_id, hour, skill) DO NOTHING""",
            records,
        )
        print("written")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
