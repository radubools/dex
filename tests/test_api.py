"""HTTP surface against Postgres: auth, threads, tasks, resume, rerun, assets."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.config import Config
from dex.models import TaskState
from dex.planner import Plan, PlannedTask


@pytest.fixture
def client(config: Config, dsn: str, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)

    async def fake_plan(message, cfg, existing, model=None, project=None, guide=""):
        if "vague" in message:
            return Plan(needs_clarification="Which problem did you mean?")
        return Plan(
            tasks=[
                PlannedTask(title="Two Sum", problem="Two Sum full statement", slug="two-sum"),
                PlannedTask(title="LRU Cache", problem="LRU full statement", slug="lru-cache"),
            ],
            notes="split into two independent problems",
        )

    monkeypatch.setattr("dex.api.plan_from_message", fake_plan)
    guarded = Config(**{**config.__dict__, "token": "secret"})
    try:
        with TestClient(create_app(guarded), headers={"x-dex-token": "secret"}) as c:
            c.config = guarded  # type: ignore[attr-defined]
            c.post("/api/_truncate") if False else None
            yield c
    except RuntimeError as exc:  # no database
        pytest.skip(str(exc))


@pytest.fixture(autouse=True)
def clean(db):
    """Every API test starts from an empty database."""
    yield


def wait_for_terminal(client, task_id: str, timeout: float = 5.0) -> dict:
    """Poll until the task stops running; the runner finishes asynchronously."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()["task"]
        if task["state"] in {"succeeded", "failed", "cancelled"}:
            return task
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} never finished (last state {task['state']})")


def test_token_is_required(client):
    assert client.get("/api/health", headers={"x-dex-token": "wrong"}).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_health_reports_the_database(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert "dex_test" in body["database"]


def test_submit_and_list_a_task(client):
    created = client.post("/api/tasks", json={"problem": "Two Sum", "title": "Two Sum"}).json()["task"]
    assert created["slug"] == "two-sum"
    listed = client.get("/api/tasks").json()["tasks"]
    assert created["id"] in [t["id"] for t in listed]


def test_tasks_survive_a_new_app_instance(client, config):
    """The whole point: a restart must not lose the work."""
    created = client.post("/api/tasks", json={"problem": "Two Sum"}).json()["task"]

    guarded = Config(**{**config.__dict__, "token": "secret"})
    with TestClient(create_app(guarded), headers={"x-dex-token": "secret"}) as second:
        again = second.get(f"/api/tasks/{created['id']}").json()["task"]
        assert again["slug"] == created["slug"]


def test_thread_messages_survive_a_new_app_instance(client, config):
    thread = client.post("/api/threads", json={}).json()["thread"]
    client.post(f"/api/threads/{thread['id']}/messages", json={"text": "two sum and lru"})

    guarded = Config(**{**config.__dict__, "token": "secret"})
    with TestClient(create_app(guarded), headers={"x-dex-token": "secret"}) as second:
        restored = second.get(f"/api/threads/{thread['id']}").json()["thread"]
        assert [m["role"] for m in restored["messages"]] == ["user", "dex"]
        assert restored["messages"][1]["kind"] == "plan"


def test_confirming_a_plan_links_tasks_to_the_thread(client):
    thread = client.post("/api/threads", json={}).json()["thread"]
    plan = client.post(
        f"/api/threads/{thread['id']}/messages", json={"text": "two sum and lru"}
    ).json()["plan"]

    created = client.post(
        "/api/chat/confirm", json={"tasks": plan["tasks"], "thread_id": thread["id"]}
    ).json()["tasks"]
    assert len(created) == 2

    fetched = client.get(f"/api/threads/{thread['id']}").json()
    assert fetched["thread"]["taskIds"] == [t["id"] for t in created]
    assert len(fetched["tasks"]) == 2


def test_a_vague_message_comes_back_as_a_question(client):
    thread = client.post("/api/threads", json={}).json()["thread"]
    body = client.post(f"/api/threads/{thread['id']}/messages", json={"text": "something vague"}).json()
    assert body["plan"]["tasks"] == []
    assert "Which problem" in body["message"]["text"]


def test_resume_is_refused_for_a_task_that_did_not_stop_short(client):
    created = client.post("/api/tasks", json={"problem": "Two Sum"}).json()["task"]
    assert wait_for_terminal(client, created["id"])["state"] == "succeeded"
    # A task that finished has nothing to resume; rerun is the option instead.
    assert client.post(f"/api/tasks/{created['id']}/resume").status_code == 409


class FailingRunner:
    """A run that stops short the way a killed server leaves one."""

    def __init__(self, task, config, bus, store, settings=None):
        self.task, self.store = task, store

    async def run(self) -> None:
        self.task.session_id = "session-abc"
        await self.store.set_session(self.task.id, "session-abc")
        self.task.state = TaskState.FAILED
        await self.store.set_state(
            self.task.id, TaskState.FAILED, error="agent reported an error"
        )


def test_resume_in_place_puts_the_same_task_back_rather_than_forking(client, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", FailingRunner)
    thread = client.post("/api/threads", json={}).json()["thread"]
    created = client.post(
        "/api/tasks", json={"problem": "Two Sum", "thread_id": thread["id"]}
    ).json()["task"]
    assert wait_for_terminal(client, created["id"])["state"] == "failed"

    body = client.post(f"/api/tasks/{created['id']}/resume?in_place=true").json()

    assert body["task"]["id"] == created["id"]
    assert body["task"]["state"] == "queued"
    # The forking route would have left the thread holding two chips for one
    # piece of work.
    assert client.get(f"/api/threads/{thread['id']}").json()["thread"]["taskIds"] == [created["id"]]


def test_resume_without_in_place_still_forks_a_continuation(client, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", FailingRunner)
    thread = client.post("/api/threads", json={}).json()["thread"]
    created = client.post(
        "/api/tasks", json={"problem": "Two Sum", "thread_id": thread["id"]}
    ).json()["task"]
    wait_for_terminal(client, created["id"])

    body = client.post(f"/api/tasks/{created['id']}/resume").json()

    assert body["task"]["id"] != created["id"]
    assert body["task"]["parentId"] == created["id"]


def test_rerun_creates_a_second_attempt_in_the_same_thread(client):
    thread = client.post("/api/threads", json={}).json()["thread"]
    created = client.post(
        "/api/tasks", json={"problem": "Two Sum", "thread_id": thread["id"]}
    ).json()["task"]
    wait_for_terminal(client, created["id"])

    again = client.post(f"/api/tasks/{created['id']}/rerun")
    assert again.status_code == 200
    body = again.json()["task"]
    assert body["attempt"] == 2
    assert body["parentId"] == created["id"]
    # The rerun is announced in the thread, so the chat shows it.
    messages = client.get(f"/api/threads/{thread['id']}").json()["thread"]["messages"]
    assert any("Re-running" in m["text"] for m in messages)


def test_unknown_task_and_thread_are_404(client):
    assert client.get("/api/tasks/nope").status_code == 404
    assert client.post("/api/threads/nope/messages", json={"text": "hi"}).status_code == 404


def test_assets_are_confined_to_the_root(client):
    task_dir = client.config.assets_dir / "two-sum"  # type: ignore[attr-defined]
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "solutions.py").write_text("def solve(): ...\n")
    (task_dir / "brute.gif").write_bytes(b"GIF89a\x00")

    assert "def solve" in client.get("/api/assets", params={"path": "two-sum/solutions.py"}).json()["content"]
    raw = client.get("/api/assets/raw", params={"path": "two-sum/brute.gif"})
    assert raw.status_code == 200 and raw.content.startswith(b"GIF89a")

    for path in ["../../pyproject.toml", "/etc/hosts", "two-sum/../../../etc/hosts"]:
        assert client.get("/api/assets", params={"path": path}).status_code in (403, 404)


def test_a_parked_task_reports_what_it_is_waiting_on(client):
    """Rows carry durable state; what the agent is parked on is in the process."""
    import asyncio

    created = client.post("/api/tasks", json={"problem": "Two Sum"}).json()["task"]
    wait_for_terminal(client, created["id"])

    manager = client.app.state.tasks
    stored = client.portal.call(manager.tasks.get, created["id"])  # type: ignore[attr-defined]
    stored.pending = {"question-1": asyncio.Future()}
    manager.live[created["id"]] = stored

    body = client.get(f"/api/tasks/{created['id']}").json()["task"]
    assert body["pending"] == ["question-1"]
    manager.live.pop(created["id"], None)


@pytest.fixture
def ui_client(config, db, monkeypatch):
    """An app whose workspace actually has a built UI to serve."""
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    dist = config.workspace / "web" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>dex</title>")
    (dist / "assets").mkdir(exist_ok=True)
    (dist / "assets" / "real.js").write_text("// built")
    with TestClient(create_app(config)) as c:
        yield c


def test_a_deep_ui_path_serves_the_app_shell(ui_client):
    """The UI keeps its place in the URL, so a reload asks for a client route.

    Without a fallback `StaticFiles` answers 404 and the reload fails, which is
    exactly the case putting state in the URL is meant to support.
    """
    for path in ("/yoga", "/yoga/threads/abc123"):
        response = ui_client.get(path)
        assert response.status_code == 200, path
        assert "<!doctype html" in response.text.lower(), path


def test_a_missing_asset_is_still_a_404(ui_client):
    """Only extensionless routes fall back; a broken build must stay obvious."""
    assert ui_client.get("/assets/real.js").status_code == 200
    for path in ("/assets/nope.js", "/assets/nope.css"):
        assert ui_client.get(path).status_code == 404, path


def test_the_api_is_not_shadowed_by_the_fallback(ui_client):
    assert ui_client.get("/api/health").status_code == 200
    assert ui_client.get("/api/definitely-not-a-route").status_code == 404


async def test_removing_a_thread_hides_it_rather_than_destroying_it(client):
    """A mis-tap used to cascade a thread's messages away and orphan its tasks."""
    thread = client.post("/api/threads", json={}).json()["thread"]
    client.post(f"/api/threads/{thread['id']}/messages", json={"text": "something vague"})

    assert client.delete(f"/api/threads/{thread['id']}").json() == {"ok": True}

    # Out of the list, but still readable, with its messages intact.
    listed = [t["id"] for t in client.get("/api/threads").json()["threads"]]
    assert thread["id"] not in listed
    detail = client.get(f"/api/threads/{thread['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["thread"]["messages"]) > 0

    assert client.post(f"/api/threads/{thread['id']}/unhide").json() == {"ok": True}
    assert thread["id"] in [t["id"] for t in client.get("/api/threads").json()["threads"]]


def test_packages_carry_the_tags_from_their_manifests(client):
    project = client.config.assets_dir / "algorithms"
    (project / "two-sum").mkdir(parents=True, exist_ok=True)
    (project / "two-sum" / "manifest.json").write_text(
        '{"tags": ["medium", "arrays", "hash-map"]}', encoding="utf-8"
    )
    (project / "reverse-string").mkdir(parents=True, exist_ok=True)
    (project / "reverse-string" / "manifest.json").write_text(
        '{"tags": ["easy", "strings"]}', encoding="utf-8"
    )

    body = client.get("/api/packages?project=algorithms").json()

    tags = {p["slug"]: p["tags"] for p in body["packages"]}
    assert tags["two-sum"] == ["medium", "arrays", "hash-map"]
    assert tags["reverse-string"] == ["easy", "strings"]
    # Counted across the project, commonest first, so the pills that actually
    # divide the library lead.
    assert {t["name"]: t["count"] for t in body["tags"]}["arrays"] == 1
    assert [t["name"] for t in body["tags"]][0] in {"arrays", "easy", "hash-map", "medium", "strings"}


def test_a_package_without_a_usable_manifest_still_lists(client):
    project = client.config.assets_dir / "algorithms"
    (project / "no-manifest").mkdir(parents=True, exist_ok=True)
    (project / "no-manifest" / "solutions.py").write_text("x = 1", encoding="utf-8")
    (project / "bad-manifest").mkdir(parents=True, exist_ok=True)
    (project / "bad-manifest" / "manifest.json").write_text("{ not json", encoding="utf-8")
    (project / "odd-tags").mkdir(parents=True, exist_ok=True)
    (project / "odd-tags" / "manifest.json").write_text(
        '{"tags": {"difficulty": "easy"}}', encoding="utf-8"
    )

    listed = {p["slug"]: p for p in client.get("/api/packages?project=algorithms").json()["packages"]}

    # A package written before tags existed, or one whose manifest is broken,
    # drops out of the filters rather than out of the library.
    assert listed["no-manifest"]["tags"] == []
    assert listed["bad-manifest"]["tags"] == []
    assert listed["odd-tags"]["tags"] == []


def test_a_started_task_records_the_plan_row_it_came_from(client):
    """The slug a task gets is not always the slug the plan asked for.

    `slugify` resolves a collision by appending a suffix, so a plan row for
    `two-sum` can produce `two-sum-2`. Without the planned slug recorded, the
    plan card looked its own row up, found nothing, and offered to run work
    that was already queued.
    """
    thread = client.post("/api/threads", json={}).json()["thread"]
    # Take the slug first, so the plan's choice collides with it.
    client.post("/api/tasks", json={"problem": "Something else", "slug": "two-sum"})

    body = client.post(
        "/api/chat/confirm",
        json={"thread_id": thread["id"],
              "tasks": [{"problem": "Two Sum full statement", "title": "Two Sum", "slug": "two-sum"}]},
    ).json()

    task = body["tasks"][0]
    assert task["slug"] != "two-sum"  # it collided and was suffixed
    assert task["plannedSlug"] == "two-sum"  # but the row it came from is known

    # And it survives a reload, because it is stored on the thread message.
    messages = client.get(f"/api/threads/{thread['id']}").json()["thread"]["messages"]
    started = [m for m in messages if m["kind"] == "tasks"][-1]
    assert started["data"]["tasks"][0]["plannedSlug"] == "two-sum"


def test_a_thread_returns_every_one_of_its_tasks(client):
    """The plan cards judge their rows against these.

    The store's default limit is 200; a thread of 546 delivered its oldest 200,
    so a plan card looked up rows whose tasks the browser had never been sent,
    read them as never started, and offered to run queued work again.
    """
    thread = client.post("/api/threads", json={}).json()["thread"]
    for n in range(205):
        client.post("/api/tasks", json={"problem": f"Problem {n}", "thread_id": thread["id"]})

    body = client.get(f"/api/threads/{thread['id']}").json()

    assert len(body["tasks"]) == 205


def test_health_reports_the_live_limit_not_a_startup_guess(client):
    """It used to report the config value — 3, while 6 workers were running.

    The limit is a setting now, so a number fixed when the process started is
    wrong the moment anyone changes it.
    """
    from dex.config import MAX_WORKERS

    client.put("/api/settings", json={"task_concurrency": 5})

    health = client.get("/api/health").json()

    assert health["concurrency"] == 5
    assert health["maxConcurrency"] == MAX_WORKERS
