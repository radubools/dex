"""Shared fixtures. The database-backed tests run against a real Postgres.

Set `DEX_TEST_DATABASE_URL` to point them elsewhere; they are skipped entirely
when no server is reachable, so the pure-logic tests still run anywhere.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from dex.config import Config
from dex.db import Database
from dex.models import TaskState

TEST_DSN = os.environ.get("DEX_TEST_DATABASE_URL", "postgresql://127.0.0.1/dex_test")
DEFAULT_PROJECT = "algorithms"


@pytest.fixture(scope="session")
def dsn() -> str:
    return TEST_DSN


@pytest.fixture(scope="session", autouse=True)
def _one_session_at_a_time(dsn: str):
    """Serialise pytest sessions that share one test database.

    Every test empties each table, and the queue's workers claim whatever
    queued row they find without caring which session created it. Two sessions
    pointed at the same database therefore corrupt each other: one runs the
    other's task, and rows vanish from under a test mid-assertion. It surfaces
    as unrelated scheduling tests timing out at random, which is expensive to
    chase and easy to misread as a product bug -- so wait for the other session
    rather than racing it.

    Synchronous and session-scoped on purpose: it must outlive every event loop
    pytest-asyncio creates, and taking the lock needs no loop of its own.
    """
    key = hashlib.sha256(dsn.encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"dex-tests-{key}.lock"
    handle = path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"\nanother pytest session is using {dsn}; waiting for it...", flush=True)
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


@pytest.fixture
async def db(dsn: str):
    """A connected database with every table emptied."""
    database = Database(dsn)
    try:
        await database.connect()
    except RuntimeError as exc:
        pytest.skip(f"no test database: {exc}")
    # DELETE rather than TRUNCATE: TRUNCATE needs an ACCESS EXCLUSIVE lock, so
    # it blocks behind any connection a previous test has not finished closing,
    # which showed up as occasional multi-second stalls and timeouts.
    await database.pool.execute(
        "DELETE FROM events; DELETE FROM task_messages; DELETE FROM tasks; "
        "DELETE FROM threads; DELETE FROM settings; DELETE FROM topic_reviews; "
        # Identity too, or users accumulate across tests and anything counting
        # them -- "is this the last admin?" -- sees the previous test's people.
        "DELETE FROM sessions; DELETE FROM user_projects; DELETE FROM users; "
        "DELETE FROM oauth_states; "
        "DELETE FROM projects;"
    )
    # Every task belongs to a project, and startup guarantees the default one
    # exists. Seed it here so tests start from the same footing.
    await database.pool.execute(
        "INSERT INTO projects (slug, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        DEFAULT_PROJECT, DEFAULT_PROJECT.title(),
    )
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture(autouse=True)
def _no_startup_grace(monkeypatch):
    """Skip the restart settling period.

    Production holds queued work for half a minute after a restart. A test
    suite that waited that long for every manager it builds would take hours,
    so it is zero here and exercised deliberately by the tests that are about
    it.
    """
    monkeypatch.setattr("dex.queue.TaskManager.startup_grace_s", 0.0, raising=False)


@pytest.fixture
def config(tmp_path: Path, dsn: str) -> Config:
    cfg = Config(
        workspace=tmp_path,
        assets_dir=tmp_path / "assets",
        state_dir=tmp_path / ".dex",
        database_url=dsn,
        token="",
    )
    cfg.ensure_dirs()
    return cfg


class InstantRunner:
    """A run that succeeds immediately, so the API can be exercised offline."""

    def __init__(self, task, config, bus, store, settings=None):
        self.task, self.config, self.bus, self.store = task, config, bus, store
        self.settings = settings

    async def run(self) -> None:
        self.task.output_dir(self.config.assets_dir).mkdir(parents=True, exist_ok=True)
        self.task.state = TaskState.SUCCEEDED
        await self.store.set_state(self.task.id, TaskState.SUCCEEDED)
