#!/usr/bin/env python
"""Count the lines every past task changed, for `task_changes` (design/25).

Run once per dex, after the server has created the table:

    .venv/bin/python scripts/backfill_changes.py --dry-run
    .venv/bin/python scripts/backfill_changes.py

Nothing is estimated. A task's Write and Edit calls are in its `diff` events,
each with the lines it added or modified; one the tool then refused or failed is
left out. A file in a package that no diff touched came from a script, so it is
counted whole, for the package's first task. Every row is marked `inferred`, and
a task that already has a row is left alone, so reruns are harmless.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import os
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dex.changes import file_lines, is_code, is_work  # noqa: E402
from dex.artifacts import is_artifact  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = await asyncpg.connect(os.environ.get("DEX_DATABASE_URL", "postgresql://127.0.0.1/dex"))

    tasks = await db.fetch(
        """SELECT id, project, coalesce(output_slug, slug) AS package, created_at FROM tasks
           WHERE state <> 'running' AND id NOT IN (SELECT task_id FROM task_changes) ORDER BY seq"""
    )
    diffs = await db.fetch(
        """SELECT d.task_id, d.data->>'path' AS path, sum((d.data->>'additions')::int)::bigint AS lines
           FROM events d
           LEFT JOIN events r ON r.task_id = d.task_id AND r.type = 'tool_result'
                             AND r.data->>'id' = d.data->>'id' AND r.data->>'ok' = 'false'
           WHERE d.type = 'diff' AND d.task_id IS NOT NULL AND r.seq IS NULL
           GROUP BY 1, 2"""
    )
    code: dict[str, int] = collections.Counter()
    assets: dict[str, int] = collections.Counter()
    seen: set[str] = set()
    for row in diffs:
        seen.add(row["path"])
        if is_work(row["path"]):
            (code if is_code(row["path"]) else assets)[row["task_id"]] += row["lines"]

    # What a script wrote: the first task of a package gets what no diff explains.
    first: dict[tuple[str, str], str] = {}
    for task in tasks:
        first.setdefault((task["project"], task["package"]), task["id"])
    scripted = 0
    for (project, package), task_id in first.items():
        directory = ROOT / "assets" / str(project) / package
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            relative = path.relative_to(directory)
            if not path.is_file() or not is_artifact(relative) or not is_work(relative):
                continue
            if str(path.relative_to(ROOT)) in seen:
                continue
            lines = file_lines(path)
            scripted += lines
            (code if is_code(path) else assets)[task_id] += lines

    print(f"{len(tasks)} tasks: {sum(code.values()):,} lines of code, {sum(assets.values()):,} of assets "
          f"({scripted:,} from files no diff touched)")
    if args.dry_run:
        await db.close()
        return
    await db.executemany(
        """INSERT INTO task_changes (task_id, code_lines, asset_lines, inferred)
           VALUES ($1, $2, $3, true) ON CONFLICT (task_id) DO NOTHING""",
        [(t["id"], code[t["id"]], assets[t["id"]]) for t in tasks],
    )
    await db.close()
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
