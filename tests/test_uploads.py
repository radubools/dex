"""Source material a task reads and writes: `datasets/<project>/`.

Separate from `assets/<project>/`, which is what tasks produce. A source
dropped in the assets tree would show up in the Library, the feed and the asset
backup as though dex had generated it.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from dex import uploads
from dex.api import create_app


@pytest.fixture
def client(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        c.config = config  # type: ignore[attr-defined]
        yield c


# ------------------------------------------------------- naming and safety

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("report.pdf", "report.pdf"),
        ("my report.pdf", "my-report.pdf"),
        # The whole point: a name is not a path.
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32", "system32"),
        ("/absolute/path.md", "path.md"),
        # Nothing that hides the file from a listing, or survives into a shell.
        (".hidden", "hidden"),
        # The slash makes this a path too, so only the last component is kept.
        ("$(rm -rf /).md", "_.md"),
        ("$(rm -rf).md", "_rm--rf_.md"),
        ("....", "attachment"),
        ("", "attachment"),
    ],
)
def test_a_filename_cannot_escape_its_directory(raw, expected):
    assert uploads.safe_name(raw) == expected


def test_a_name_stays_inside_the_project_whatever_it_claims(tmp_path):
    project = tmp_path / "music"
    stored = uploads.store(project, "../../escape.txt", b"x")

    assert stored.path.parent == project
    assert not (tmp_path / "escape.txt").exists()


def test_a_name_pointing_out_of_the_project_resolves_to_nothing(tmp_path):
    """The names come back from a browser, so each is re-checked."""
    project = tmp_path / "music"
    uploads.store(project, "real.md", b"x")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "secret.md").write_text("not yours")

    found = uploads.resolve(project, ["../other/secret.md", "../../etc/passwd", "real.md"])

    assert [u.name for u in found] == ["real.md"]


def test_a_name_that_no_longer_exists_is_skipped_rather_than_fatal(tmp_path):
    """A message naming a deleted file should still send."""
    assert uploads.resolve(tmp_path, ["gone.md"]) == []


def test_two_files_of_the_same_name_both_survive(tmp_path):
    first = uploads.store(tmp_path, "notes.md", b"one")
    second = uploads.store(tmp_path, "notes.md", b"two")

    assert first.path != second.path
    assert first.path.read_bytes() == b"one"
    assert second.path.read_bytes() == b"two"


# ------------------------------------------------------------ the brief

def test_the_brief_names_the_files_and_says_they_may_be_read(tmp_path):
    uploads.store(tmp_path, "spec.pdf", b"x" * 10)
    attached = uploads.resolve(tmp_path, ["spec.pdf"])

    brief = uploads.with_sources("Translate the spec.", attached, tmp_path)

    assert brief.startswith("Translate the spec.")
    assert "## Sources" in brief
    assert "spec.pdf" in brief
    # Absolute, because the agent's working directory is its own package.
    assert str(tmp_path) in brief
    assert "read from and write to" in brief


def test_a_brief_without_attachments_is_left_alone():
    assert uploads.with_sources("Two Sum", []) == "Two Sum"


def test_naming_the_same_file_twice_lists_it_once(tmp_path):
    uploads.store(tmp_path, "a.md", b"a")

    assert len(uploads.resolve(tmp_path, ["a.md", "a.md"])) == 1


# --------------------------------------------------------------- the API

def upload(client, *files, project: str | None = None) -> dict:
    url = "/api/uploads" + (f"?project={project}" if project else "")
    return client.post(
        url,
        files=[("files", (name, io.BytesIO(body), "application/octet-stream"))
               for name, body in files],
    ).json()


def test_several_files_upload_together(client):
    body = upload(client, ("a.md", b"first"), ("b.md", b"second"))

    assert len(body["files"]) == 2
    assert {f["name"] for f in body["files"]} == {"a.md", "b.md"}


def test_an_upload_lands_in_the_project_data_directory(client):
    body = upload(client, ("source.pdf", b"%PDF-1.4"))
    config = client.config  # type: ignore[attr-defined]
    stored = config.project_datasets(body["project"]) / "source.pdf"

    assert stored.read_bytes() == b"%PDF-1.4"
    assert stored.parent == config.datasets_dir / body["project"]
    # Never in the assets tree, which is what tasks produce.
    assert config.assets_dir not in stored.parents


def test_a_task_submitted_with_attachments_is_told_where_they_are(client):
    upload(client, ("notes.md", b"the source"))

    task = client.post(
        "/api/tasks", json={"problem": "Use the notes", "uploads": ["notes.md"]}
    ).json()["task"]

    brief = client.get(f"/api/tasks/{task['id']}").json()["task"]["problem"]
    assert "## Sources" in brief
    assert "notes.md" in brief


def test_every_task_of_a_confirmed_plan_gets_the_attachments(client):
    """They were attached to the request, not to whichever task came first."""
    thread = client.post("/api/threads", json={}).json()["thread"]
    upload(client, ("brief.pdf", b"x"))

    created = client.post(
        "/api/chat/confirm",
        json={
            "thread_id": thread["id"],
            "uploads": ["brief.pdf"],
            "tasks": [
                {"problem": "One", "title": "One", "slug": "one"},
                {"problem": "Two", "title": "Two", "slug": "two"},
            ],
        },
    ).json()["tasks"]

    assert len(created) == 2
    for task in created:
        full = client.get(f"/api/tasks/{task['id']}").json()["task"]["problem"]
        assert "brief.pdf" in full, f"{task['slug']} was not told about the source"


def test_an_empty_upload_is_refused(client):
    assert client.post(
        "/api/uploads",
        files=[("files", ("empty.md", io.BytesIO(b""), "text/markdown"))],
    ).status_code == 400


# ------------------------------------------- what a task is allowed to touch

def test_every_task_may_write_its_project_data_and_no_other(config, db):
    """The guides promise read and write here, so the policy has to allow it.

    A task that had to stop for approval before writing an index beside its
    sources would stop on its first real step.
    """
    from dex.bus import EventBus
    from dex.models import Task
    from dex.permissions import PermissionPolicy
    from dex.runner import TaskRunner
    from dex.store import SettingsStore, TaskStore

    task = Task(problem="p", title="t", slug="song")
    task.project = "music"
    runner = TaskRunner(task, config, EventBus(db), TaskStore(db), SettingsStore(db))
    policy = PermissionPolicy(
        workspace=config.workspace,
        task_dir=runner.task_dir,
        extra_writable=(config.project_datasets("music"),),
        escalate=None,  # type: ignore[arg-type]
    )

    def writable(path) -> bool:
        return policy._within(str(path), policy.task_dir) or any(
            policy._within(str(path), root) for root in policy.extra_writable
        )

    assert writable(config.project_datasets("music") / "corpus.db")
    assert writable(config.project_datasets("music") / "nested" / "index.json")
    # Its own package, as before.
    assert writable(runner.task_dir / "solutions.py")
    # Another project's data is not its business.
    assert not writable(config.project_datasets("yoga") / "poses.db")
    assert not writable(config.workspace / "src" / "dex" / "api.py")


def test_the_brief_tells_the_task_where_its_data_directory_is(config):
    from dex.prompts import generation_prompt

    brief = generation_prompt(
        problem="x",
        task_dir=config.assets_dir / "music" / "song",
        python=config.workspace / "py",
        manim_available=True,
        workspace=config.workspace,
        datasets_dir=config.project_datasets("music"),
    )

    assert "data directory is yours to read and write" in brief
    assert str(config.project_datasets("music")) in brief
