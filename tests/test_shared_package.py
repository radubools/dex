"""Several tasks can be pointed at one package, and normally are not.

A batch translating one document was planned as eleven tasks, each with its own
slug — and `slugify` turned that into eleven directories, `…-ro-2` through
`…-ro-11`, none of which was the package the briefs all referred to. The tasks
then waited for a file that was being written somewhere else entirely.

`package` is the way to say so on purpose. It stays the exception: independent
tasks are what lets one of them fail without taking the others with it, so the
planner is told to reach for this only when the work genuinely cannot be cut up.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.planner import parse_plan


@pytest.fixture
def client(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        yield c


def plan_of(*entries: str, existing: list[str] | None = None):
    return parse_plan('{"tasks": [' + ", ".join(entries) + "]}", existing=existing or [])


# ------------------------------------------------------------------ planning


def test_siblings_keep_their_own_slugs_and_share_a_directory():
    plan = plan_of(
        '{"title": "§1", "problem": "a", "slug": "ro-1", "package": "history-ro"}',
        '{"title": "§2", "problem": "b", "slug": "ro-2", "package": "history-ro"}',
    )

    assert [t.slug for t in plan.tasks] == ["ro-1", "ro-2"]
    assert [t.package for t in plan.tasks] == ["history-ro", "history-ro"]


def test_a_shared_package_need_not_exist_yet():
    """The whole point of it: `updates` cannot name a package nobody built."""
    plan = plan_of(
        '{"title": "§1", "problem": "a", "slug": "ro-1", "package": "nothing-here-yet"}',
        existing=["something-else"],
    )

    assert plan.tasks[0].package == "nothing-here-yet"


def test_a_shared_package_is_normalised_not_trusted():
    plan = plan_of('{"title": "§1", "problem": "a", "slug": "ro-1", "package": "../../etc"}')

    assert "/" not in plan.tasks[0].package
    assert ".." not in plan.tasks[0].package


def test_no_package_named_means_a_directory_of_its_own():
    plan = plan_of('{"title": "Two Sum", "problem": "a", "slug": "two-sum"}')

    assert plan.tasks[0].package == ""


def test_a_sweep_has_no_package_to_share():
    """It edits packages that are already there; it writes into none of them."""
    plan = plan_of(
        '{"title": "Retitle", "problem": "a", "slug": "retitle", '
        '"scope": "project", "package": "history-ro"}'
    )

    assert plan.tasks[0].package == ""
    assert plan.tasks[0].scope == "project"


# ----------------------------------------------------------------- confining


def test_a_shared_package_becomes_the_output_directory(client, config):
    created = client.post(
        "/api/tasks",
        json={"problem": "Translate §1", "slug": "ro-1", "package": "history-ro"},
    ).json()["task"]

    assert created["slug"] == "ro-1"
    assert created["outputDir"].endswith("/history-ro")


def test_updates_outranks_a_shared_package(client, config):
    """One names a package known to be there; the other only hopes so."""
    (config.assets_dir / config.default_project / "two-sum").mkdir(parents=True, exist_ok=True)

    created = client.post(
        "/api/tasks",
        json={
            "problem": "Redo it",
            "slug": "two-sum-again",
            "updates": "two-sum",
            "package": "somewhere-else",
        },
    ).json()["task"]

    assert created["outputDir"].endswith("/two-sum")


def test_a_plan_of_siblings_lands_them_all_in_one_directory(client, config):
    thread = client.post("/api/threads", json={"title": "History"}).json()["thread"]
    body = {
        "thread_id": thread["id"],
        "tasks": [
            {"problem": "Translate §1", "slug": "ro-1", "package": "history-ro"},
            {"problem": "Translate §2", "slug": "ro-2", "package": "history-ro"},
        ],
    }

    created = client.post("/api/chat/confirm", json=body).json()["tasks"]

    assert [t["slug"] for t in created] == ["ro-1", "ro-2"]
    assert {t["outputDir"].rsplit("/", 1)[-1] for t in created} == {"history-ro"}
