"""Postgres connection pool, schema, and the one-time import of JSON threads."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import asyncpg

log = logging.getLogger("dex.db")

SCHEMA = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        return self._pool

    async def connect(self) -> None:
        try:
            self._pool = await asyncpg.create_pool(
                self.dsn, min_size=1, max_size=10, command_timeout=30
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise RuntimeError(
                f"cannot reach Postgres at {_safe(self.dsn)}: {exc}\n"
                "Start it (Postgres.app, or `brew services start postgresql`), create the\n"
                "database with `createdb dex`, or point DEX_DATABASE_URL somewhere else."
            ) from exc
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA.read_text())
        log.info("connected to %s", _safe(self.dsn))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # asyncpg has no JSON codec by default; every jsonb column goes through these.
    @staticmethod
    def dump(value: Any) -> str:
        return json.dumps(value or {})

    @staticmethod
    def load(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            return json.loads(value) if value else {}
        except (TypeError, ValueError):
            return {}


def _safe(dsn: str) -> str:
    """DSNs can carry a password; never log it."""
    if "@" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("://")
    return f"{scheme}://***@{rest.rpartition('@')[2]}"


async def import_json_threads(db: Database, directory: Path) -> int:
    """Carry threads written by the previous file-backed store into Postgres.

    Runs once: a thread whose id is already in the database is skipped, so this
    is safe to call on every startup. The files are left alone.
    """
    if not directory.exists():
        return 0

    imported = 0
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("skipping unreadable thread file %s", path.name)
            continue

        thread_id = str(payload.get("id") or "")
        if not thread_id:
            continue

        async with db.pool.acquire() as conn, conn.transaction():
            exists = await conn.fetchval("SELECT 1 FROM threads WHERE id = $1", thread_id)
            if exists:
                continue
            await conn.execute(
                """INSERT INTO threads (id, title, created_at, updated_at)
                   VALUES ($1, $2, to_timestamp($3), to_timestamp($4))""",
                thread_id,
                str(payload.get("title", "Imported thread")),
                float(payload.get("createdAt", 0) or 0),
                float(payload.get("updatedAt", 0) or 0),
            )
            snapshots: list[dict[str, Any]] = []
            for message in payload.get("messages", []):
                if message.get("kind") == "tasks":
                    snapshots.extend(
                        t for t in (message.get("data") or {}).get("tasks", []) if isinstance(t, dict)
                    )
                await conn.execute(
                    """INSERT INTO messages (id, thread_id, role, kind, body, data, ts)
                       VALUES ($1, $2, $3, $4, $5, $6::jsonb, to_timestamp($7))
                       ON CONFLICT (id) DO NOTHING""",
                    str(message.get("id")),
                    thread_id,
                    str(message.get("role", "dex")),
                    str(message.get("kind", "text")),
                    str(message.get("text", "")),
                    Database.dump(message.get("data")),
                    float(message.get("ts", 0) or 0),
                )
            # Tasks were not persisted by the old store, so rebuild what the
            # thread remembers of them. Anything that was mid-flight is recorded
            # as `cancelled`: it was never really running, and marking it
            # `paused` would have the queue start work for packages that have
            # long since been superseded.
            for snapshot in snapshots:
                task_id = str(snapshot.get("id") or "")
                slug = str(snapshot.get("slug") or "")
                if not task_id or not slug:
                    continue
                state = str(snapshot.get("state") or "cancelled")
                if state not in {"succeeded", "failed", "cancelled"}:
                    state = "cancelled"
                await conn.execute(
                    """INSERT INTO tasks (id, thread_id, title, slug, problem, state,
                                          created_at, error, attempt)
                       VALUES ($1, $2, $3, $4, $5, $6, to_timestamp($7), $8, 1)
                       ON CONFLICT (id) DO NOTHING""",
                    task_id,
                    thread_id,
                    str(snapshot.get("title") or slug),
                    slug,
                    str(snapshot.get("problem") or snapshot.get("title") or slug),
                    state,
                    float(snapshot.get("createdAt") or 0),
                    snapshot.get("error")
                    or ("Imported from the previous file-backed store." if state == "cancelled" else None),
                )
            imported += 1

    if imported:
        log.info("imported %d thread(s) from %s", imported, directory)
    return imported
