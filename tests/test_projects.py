"""Projects: their directory, their guide, their threads, their tasks."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.config import Config
from dex.migrate import adopt_existing_project, ensure_design_thread
from dex.planner import Plan, PlannedTask
from dex.projects import ProjectStore, slugify
from dex.store import ThreadStore


def test_slugs_are_directory_safe_and_unique():
    assert slugify("Data Structures & Algorithms!") == "data-structures-algorithms"
    assert slugify("Algorithms", {"algorithms"}) == "algorithms-2"
    assert slugify("   ") == "project"


async def test_creating_a_project_makes_its_directory_and_guide(db, config: Config):
    projects = ProjectStore(db, config.assets_dir)
    project = await projects.create("System Design", "Distributed systems drills")

    assert project.slug == "system-design"
    assert projects.directory("system-design").is_dir()
    guide = projects.guide_path("system-design")
    assert guide.is_file()
    # The starter guide is something to edit, not a blank page.
    assert "System Design" in guide.read_text()
    assert "Distributed systems drills" in guide.read_text()


async def test_an_existing_guide_is_never_overwritten(db, config: Config):
    projects = ProjectStore(db, config.assets_dir)
    await projects.create("Kept", slug="kept")
    projects.write_guide("kept", "# Mine\n")
    await projects.create("Kept", slug="kept")  # created again
    assert projects.read_guide("kept") == "# Mine\n"


async def test_writing_a_guide_is_atomic(db, config: Config):
    projects = ProjectStore(db, config.assets_dir)
    await projects.create("Atomic", slug="atomic")
    projects.write_guide("atomic", "# One\n")
    projects.write_guide("atomic", "# Two\n")
    assert projects.read_guide("atomic") == "# Two\n"
    # No scratch file left behind.
    assert not list(projects.directory("atomic").glob("*.tmp"))


async def test_adoption_claims_pre_project_work(db, config: Config):
    """dex had one implicit project before it had the concept."""
    threads = ThreadStore(db)
    orphan = await threads.create("older conversation")
    await db.pool.execute("UPDATE threads SET project = NULL WHERE id = $1", orphan.id)

    await adopt_existing_project(db, config)

    projects = ProjectStore(db, config.assets_dir)
    assert await projects.get("algorithms") is not None
    restored = await threads.get(orphan.id)
    assert restored.project == "algorithms"


async def test_every_project_gets_exactly_one_design_thread(db, config: Config):
    projects = ProjectStore(db, config.assets_dir)
    await projects.create("Design Me", slug="design-me")

    first = await ensure_design_thread(db, projects, "design-me")
    second = await ensure_design_thread(db, projects, "design-me")
    assert first == second

    thread = await ThreadStore(db).get(first)
    assert thread.kind == "project_design"
    assert thread.project == "design-me"
    # It opens with an explanation rather than an empty page.
    assert thread.messages and "AGENTS.md" in thread.messages[0].text


async def test_design_threads_lead_the_list(db, config: Config):
    projects = ProjectStore(db, config.assets_dir)
    await projects.create("Ordered", slug="ordered")
    threads = ThreadStore(db)
    await threads.create("a chat", project="ordered")
    design = await ensure_design_thread(db, projects, "ordered")

    listed = await threads.list("ordered")
    assert listed[0].id == design
    assert listed[0].kind == "project_design"


async def test_threads_are_scoped_to_their_project(db, config: Config):
    projects = ProjectStore(db, config.assets_dir)
    await projects.create("One", slug="one")
    await projects.create("Two", slug="two")
    threads = ThreadStore(db)
    await threads.create("in one", project="one")
    await threads.create("in two", project="two")

    assert [t.title for t in await threads.list("one")] == ["in one"]
    assert [t.title for t in await threads.list("two")] == ["in two"]


@pytest.fixture
def client(config: Config, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)

    async def fake_plan(message, cfg, existing, model=None, project=None, guide=""):
        return Plan(tasks=[PlannedTask(title="Two Sum", problem="full", slug="two-sum")])

    monkeypatch.setattr("dex.api.plan_from_message", fake_plan)
    with TestClient(create_app(config)) as c:
        yield c


def test_the_api_lists_and_creates_projects(client):
    listed = client.get("/api/projects").json()
    assert listed["default"] == "algorithms"
    assert "algorithms" in [p["slug"] for p in listed["projects"]]

    created = client.post("/api/projects", json={"name": "Graph Theory"}).json()["project"]
    assert created["slug"] == "graph-theory"

    # Creating one gives it a design thread straight away.
    threads = client.get("/api/threads", params={"project": "graph-theory"}).json()["threads"]
    assert [t["kind"] for t in threads] == ["project_design"]


def test_the_default_project_cannot_be_deleted(client):
    assert client.delete("/api/projects/algorithms").status_code == 409


def test_the_guide_round_trips_through_the_api(client):
    client.post("/api/projects", json={"name": "Guided"})
    assert "Guided" in client.get("/api/projects/guided/guide").json()["text"]

    client.put("/api/projects/guided/guide", json={"text": "# Replaced\n"})
    assert client.get("/api/projects/guided/guide").json()["text"] == "# Replaced\n"


def test_an_unknown_project_has_no_guide(client):
    assert client.get("/api/projects/nope/guide").status_code == 404


def test_tasks_inherit_the_project_of_their_thread(client):
    client.post("/api/projects", json={"name": "Scoped"})
    thread = client.post("/api/threads", json={"project": "scoped"}).json()["thread"]
    assert thread["project"] == "scoped"

    plan = client.post(f"/api/threads/{thread['id']}/messages", json={"text": "two sum"}).json()
    created = client.post(
        "/api/chat/confirm", json={"tasks": plan["plan"]["tasks"], "thread_id": thread["id"]}
    ).json()["tasks"]
    assert created[0]["project"] == "scoped"
    # And it writes inside that project's directory.
    assert "/scoped/" in created[0]["outputDir"]


def test_a_design_thread_redrafts_the_guide_instead_of_planning(client, monkeypatch):
    """A design thread answers by changing the guide, not by proposing tasks."""
    from dex.designer import DesignReply

    async def fake_draft(**kwargs):
        assert "keep solutions short" in kwargs["message"]
        return DesignReply(
            summary="Added a **brevity** rule.", guide="# Guide\n\nKeep it short.\n", changed=True
        )

    monkeypatch.setattr("dex.designer.draft", fake_draft)
    client.post("/api/projects", json={"name": "Drafted"})
    design = client.get("/api/threads", params={"project": "drafted"}).json()["threads"][0]

    body = client.post(
        f"/api/threads/{design['id']}/messages", json={"text": "keep solutions short"}
    ).json()

    assert body["design"]["changed"] is True
    assert "brevity" in body["message"]["text"]
    # The file on disk is what tasks read, so that is what must have changed.
    assert client.get("/api/projects/drafted/guide").json()["text"] == "# Guide\n\nKeep it short.\n"
    # And no plan was produced.
    assert "plan" not in body


def test_a_design_turn_that_changes_nothing_leaves_the_file_alone(client, monkeypatch):
    from dex.designer import DesignReply

    async def fake_draft(**kwargs):
        return DesignReply(summary="Which language?", guide=kwargs["guide"], changed=False)

    monkeypatch.setattr("dex.designer.draft", fake_draft)
    client.post("/api/projects", json={"name": "Asking"})
    before = client.get("/api/projects/asking/guide").json()["text"]
    design = client.get("/api/threads", params={"project": "asking"}).json()["threads"][0]

    body = client.post(f"/api/threads/{design['id']}/messages", json={"text": "hello"}).json()
    assert body["design"]["changed"] is False
    assert client.get("/api/projects/asking/guide").json()["text"] == before


def test_the_feed_is_scoped_to_the_project(client, config):
    client.post("/api/projects", json={"name": "Feeder"})
    package = config.project_dir("feeder") / "topic"
    package.mkdir(parents=True)
    (package / "explanation.md").write_text("# Topic\n")

    feed = client.get("/api/feed", params={"project": "feeder"}).json()
    assert feed["project"] == "feeder"
    assert [t["slug"] for t in feed["topics"]] == ["topic"]
    # Paths carry the project, so they can be handed to the asset endpoints.
    assert feed["topics"][0]["explanation"] == "feeder/topic/explanation.md"
    assert client.get("/api/assets", params={"path": "feeder/topic/explanation.md"}).status_code == 200

    # Another project does not see it.
    assert client.get("/api/feed", params={"project": "algorithms"}).json()["topics"] == []


async def test_an_explicit_slug_is_taken_at_its_word(db, config: Config):
    """Adoption names an existing directory; it must not be renamed around it."""
    projects = ProjectStore(db, config.assets_dir)
    (config.assets_dir / "algorithms").mkdir(parents=True, exist_ok=True)

    project = await projects.create("Algorithms", slug="algorithms")
    assert project.slug == "algorithms"

    # Creating it again is idempotent rather than making a second one.
    again = await projects.create("Algorithms", slug="algorithms")
    assert again.slug == "algorithms"
    assert len([p for p in await projects.list() if p.slug == "algorithms"]) == 1


async def test_a_derived_slug_still_avoids_collisions(db, config: Config):
    projects = ProjectStore(db, config.assets_dir)
    await projects.create("Algorithms", slug="algorithms")
    second = await projects.create("Algorithms")
    assert second.slug == "algorithms-2"
