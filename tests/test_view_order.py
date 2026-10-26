"""Projects and threads are listed by when they were last opened (`last_viewed_at`)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.config import Config
from dex.identity import ROLE_OPERATOR, SESSION_COOKIE
from dex.projects import ProjectStore
from dex.store import ThreadStore
from tests.test_identity import signed_in  # noqa: F401  (fixture)


@pytest.fixture
def client(config: Config, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        yield c


async def test_threads_lead_with_the_last_opened(db, config: Config):
    await ProjectStore(db, config.assets_dir).create("Seen", slug="seen")
    threads = ThreadStore(db)
    a = await threads.create("a", project="seen")
    await threads.create("b", project="seen")
    c = await threads.create("c", project="seen")

    assert await threads.viewed(a.id) == "seen"
    await threads.viewed(c.id)
    await threads.viewed(a.id)  # again: a is the latest

    # Opened ones first, latest first; then the never-opened b.
    assert [t.title for t in await threads.list("seen", by_viewed=True)] == ["a", "c", "b"]
    stored = await db.pool.fetchval("SELECT last_viewed_at FROM threads WHERE id = $1", a.id)
    assert stored is not None


async def test_opening_a_thread_opens_its_project(db, config: Config):
    projects = ProjectStore(db, config.assets_dir)
    await projects.create("Older", slug="older")
    await projects.create("Newer", slug="newer")
    thread = await ThreadStore(db).create("t", project="older")

    await ThreadStore(db).viewed(thread.id)
    assert [p.slug for p in await projects.list(by_viewed=True)][0] == "older"
    # Unsorted, the list stays alphabetical for everything else that reads it.
    names = [p.name for p in await projects.list()]
    assert names == sorted(names)


async def test_an_unknown_thread_is_not_viewed(db):
    assert await ThreadStore(db).viewed("nope") is None


def test_opening_reorders_both_lists(client):
    client.post("/api/projects", json={"name": "First"})
    client.post("/api/projects", json={"name": "Second"})
    one = client.post("/api/threads", json={"project": "first", "title": "one"}).json()["thread"]
    client.post("/api/threads", json={"project": "first", "title": "two"})

    assert client.post(f"/api/threads/{one['id']}/viewed").json() == {"ok": True}
    titles = [t["title"] for t in client.get("/api/threads", params={"project": "first"}).json()["threads"]]
    assert titles[0] == "one"
    assert client.get("/api/projects").json()["projects"][0]["slug"] == "first"

    client.post("/api/projects/second/viewed")
    assert client.get("/api/projects").json()["projects"][0]["slug"] == "second"


def test_an_unknown_thread_cannot_be_viewed(client):
    assert client.post("/api/threads/nope/viewed").status_code == 404


async def test_a_project_they_cannot_open_cannot_be_viewed(signed_in, config, db):  # noqa: F811
    client, sign_in, _ = signed_in
    await ProjectStore(db, config.assets_dir).create("Hidden", slug="hidden")
    _, secret = await sign_in("eve@e.com", ROLE_OPERATOR, projects=[])
    client.cookies.set(SESSION_COOKIE, secret)
    assert client.post("/api/projects/hidden/viewed").status_code in (403, 404)
    assert await db.pool.fetchval("SELECT last_viewed_at FROM projects WHERE slug = 'hidden'") is None
