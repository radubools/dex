"""Projects and threads are listed by when the reader last opened them."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.identity import ROLE_OPERATOR, SESSION_COOKIE
from dex.config import Config
from dex.projects import ProjectStore
from dex.store import ThreadStore, ViewStore
from tests.test_identity import signed_in  # noqa: F401  (fixture)


@pytest.fixture
def client(config: Config, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        yield c


async def test_threads_lead_with_the_last_opened(db, config: Config):
    await ProjectStore(db, config.assets_dir).create("Seen", slug="seen")
    threads, views = ThreadStore(db), ViewStore(db)
    a = await threads.create("a", project="seen")
    b = await threads.create("b", project="seen")
    c = await threads.create("c", project="seen")

    await views.record("", "thread", a.id)
    await views.record("", "thread", c.id)
    await views.record("", "thread", a.id)  # again: a is the latest

    # Opened ones first, latest first; then the never-opened b.
    assert [t.title for t in await threads.list("seen", viewer="")] == ["a", "c", "b"]
    # Someone else has opened nothing: newest first.
    assert [t.title for t in await threads.list("seen", viewer="other")] == ["c", "b", "a"]


async def test_a_view_kind_must_be_known(db):
    with pytest.raises(ValueError):
        await ViewStore(db).record("", "task", "x")


def test_opening_reorders_both_lists(client):
    client.post("/api/projects", json={"name": "First"})
    client.post("/api/projects", json={"name": "Second"})
    one = client.post("/api/threads", json={"project": "first", "title": "one"}).json()["thread"]
    client.post("/api/threads", json={"project": "first", "title": "two"})

    assert client.post(f"/api/threads/{one['id']}/viewed").json() == {"ok": True}
    titles = [t["title"] for t in client.get("/api/threads", params={"project": "first"}).json()["threads"]]
    assert titles[0] == "one"
    # Opening a thread opens its project.
    assert client.get("/api/projects").json()["projects"][0]["slug"] == "first"

    client.post("/api/projects/second/viewed")
    assert client.get("/api/projects").json()["projects"][0]["slug"] == "second"


def test_an_unknown_thread_cannot_be_viewed(client):
    assert client.post("/api/threads/nope/viewed").status_code == 404


async def test_each_user_has_their_own_order(signed_in, config, db):  # noqa: F811
    client, sign_in, _ = signed_in
    store = ProjectStore(db, config.assets_dir)
    await store.create("Alpha", slug="alpha")
    await store.create("Beta", slug="beta")
    _, ann = await sign_in("ann@e.com", ROLE_OPERATOR, projects=["alpha", "beta"])
    _, bob = await sign_in("bob@e.com", ROLE_OPERATOR, projects=["alpha", "beta"])

    client.cookies.set(SESSION_COOKIE, ann)
    client.post("/api/projects/alpha/viewed")
    client.cookies.set(SESSION_COOKIE, bob)
    client.post("/api/projects/beta/viewed")

    client.cookies.set(SESSION_COOKIE, ann)
    assert [p["slug"] for p in client.get("/api/projects").json()["projects"]] == ["alpha", "beta"]
    client.cookies.set(SESSION_COOKIE, bob)
    assert [p["slug"] for p in client.get("/api/projects").json()["projects"]] == ["beta", "alpha"]


async def test_a_project_they_cannot_open_cannot_be_viewed(signed_in, config, db):  # noqa: F811
    client, sign_in, _ = signed_in
    await ProjectStore(db, config.assets_dir).create("Hidden", slug="hidden")
    _, secret = await sign_in("eve@e.com", ROLE_OPERATOR, projects=[])
    client.cookies.set(SESSION_COOKIE, secret)
    assert client.post("/api/projects/hidden/viewed").status_code in (403, 404)
