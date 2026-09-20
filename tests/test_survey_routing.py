"""The choice between planning a message and surveying it first.

The behaviour these pin down is the one that had to be ubiquitous: a message
arriving with material attached is surveyed before it is planned, in every
project, with no guide able to turn it off.
"""

from __future__ import annotations

import json

import pytest

from tests.test_api import client, clean  # noqa: F401  (fixtures)


def _thread(c, project: str = "algorithms") -> str:
    return c.post("/api/threads", json={"project": project}).json()["thread"]["id"]


def _upload(c, name: str, body: bytes, project: str = "algorithms"):
    return c.post(
        f"/api/uploads?project={project}",
        files={"files": (name, body, "application/octet-stream")},
    ).json()["files"][0]["name"]


def test_a_plain_message_is_planned_straight_away(client):  # noqa: F811
    thread = _thread(client)
    body = client.post(
        f"/api/threads/{thread}/messages", json={"text": "two sum and lru cache"}
    ).json()
    assert "plan" in body
    assert "survey" not in body


def test_a_message_with_an_attachment_starts_a_survey_instead(client):  # noqa: F811
    thread = _thread(client)
    name = _upload(client, "book.md", b"# One\n\ntext\n\n# Two\n\nmore\n")

    body = client.post(
        f"/api/threads/{thread}/messages",
        json={"text": "split this up", "uploads": [name]},
    ).json()

    assert body.get("survey") == {"queued": True}
    assert "plan" not in body
    task = body["task"]
    assert task["scope"] == "survey"


def test_a_message_with_a_url_starts_a_survey(client):  # noqa: F811
    thread = _thread(client)
    body = client.post(
        f"/api/threads/{thread}/messages",
        json={"text": "index https://example.com/docs for me"},
    ).json()
    assert body.get("survey") == {"queued": True}


def test_the_survey_task_carries_what_it_needs(client):  # noqa: F811
    """Message, attachments, URLs and the guide, packed into `problem`."""
    thread = _thread(client)
    name = _upload(client, "notes.md", b"# A\n")

    body = client.post(
        f"/api/threads/{thread}/messages",
        json={"text": "use this with https://example.com/x", "uploads": [name]},
    ).json()

    packed = json.loads(client.get(f"/api/tasks/{body['task']['id']}").json()["task"]["problem"])
    assert packed["attachments"] == [name]
    assert packed["urls"] == ["https://example.com/x"]
    assert "use this" in packed["message"]


def test_the_survey_is_attributed_to_the_thread_and_project(client):  # noqa: F811
    thread = _thread(client)
    name = _upload(client, "notes.md", b"# A\n")
    task = client.post(
        f"/api/threads/{thread}/messages",
        json={"text": "plan this", "uploads": [name]},
    ).json()["task"]
    assert task["threadId"] == thread
    assert task["project"] == "algorithms"


async def test_a_design_thread_is_never_surveyed(client, db):  # noqa: F811
    """Design turns read the guide, not the operator's source material.

    A design thread is made by `migrate`, not by `POST /api/threads` — that
    route only ever produces chat threads — so this reaches the store the same
    way the real one is created.
    """
    from dex.store import ThreadStore

    thread = await ThreadStore(db).create(
        "Design", project="algorithms", kind="project_design"
    )
    name = _upload(client, "notes.md", b"# A\n")

    body = client.post(
        f"/api/threads/{thread.id}/messages",
        json={"text": "change the guide", "uploads": [name]},
    ).json()
    assert "design" in body
    assert "survey" not in body


# ------------------------------------------------- the anchor reaches the task


ANCHOR = {
    "source": "book.pdf", "label": "pp. 12–48 · Chapter 3",
    "page": 12, "endPage": 48, "heading": "Chapter 3",
}


def test_a_confirmed_task_keeps_its_anchor(client):  # noqa: F811
    """The survey's anchor has to survive the plan and the confirm round trip.

    It came from the survey, went out to the browser inside a plan, and comes
    back in the confirm body; without it persisting here the Files tab has no
    way to say which part of the material a task was cut out of.
    """
    created = client.post("/api/chat/confirm", json={
        "tasks": [{
            "problem": "Translate chapter 3, pages 12 to 48 of book.pdf.",
            "title": "Chapter 3", "slug": "chapter-3", "anchor": ANCHOR,
        }],
    }).json()["tasks"]

    assert created[0]["anchor"] == ANCHOR
    # And again from the database rather than from what was just built.
    assert client.get(f"/api/tasks/{created[0]['id']}").json()["task"]["anchor"] == ANCHOR


def test_a_task_with_no_anchor_has_none(client):  # noqa: F811
    """Most tasks have no material behind them; the field stays empty."""
    created = client.post("/api/chat/confirm", json={
        "tasks": [{"problem": "Two Sum in full.", "title": "Two Sum", "slug": "two-sum"}],
    }).json()["tasks"]
    assert created[0]["anchor"] is None


def test_a_url_anchor_is_kept_whole(client):  # noqa: F811
    """A link has no page; what identifies it is the URL itself."""
    anchor = {"source": "https://docs.example.com/guide",
              "url": "https://docs.example.com/guide", "label": "Guide"}
    created = client.post("/api/chat/confirm", json={
        "tasks": [{"problem": "Summarise the guide page.", "title": "Guide",
                   "slug": "guide", "anchor": anchor}],
    }).json()["tasks"]
    assert created[0]["anchor"] == anchor


# ---------------------------------------------- questions live in a task


def test_a_planner_question_becomes_a_pre_planning_task(client, monkeypatch):  # noqa: F811
    """The planner may not ask the chat; it hands the question to pre-planning.

    Planning is one stateless call. It cannot follow up, and an answer typed
    underneath it reaches a *different* call that remembers none of it — which
    is how a link surveyed into twenty parts came back as a single task. A
    survey task can ask, because it parks with an activity pane and carries the
    answer forward itself.
    """
    from dex.planner import Plan

    async def asks(message, cfg, existing, model=None, project=None, guide="", survey=""):
        return Plan(needs_clarification="Which target language?",
                    options=["Romanian", "English"])

    monkeypatch.setattr("dex.api.plan_from_message", asks)
    thread = _thread(client)
    # The project's guide, where a real install keeps it. Written here because
    # the assertion below is the point: a question asked from pre-planning is
    # asked by something that has read it, which is what a chat question never
    # had.
    guide = client.config.assets_dir / "algorithms" / "AGENTS.md"  # type: ignore[attr-defined]
    guide.parent.mkdir(parents=True, exist_ok=True)
    guide.write_text("# Algorithms\n\nOne package per problem.\n")

    body = client.post(
        f"/api/threads/{thread}/messages", json={"text": "translate the Priest novel"},
    ).json()

    assert body.get("survey") == {"queued": True}
    task = body["task"]
    assert task["scope"] == "survey"
    assert task["title"].startswith("Ask:")

    packed = json.loads(client.get(f"/api/tasks/{task['id']}").json()["task"]["problem"])
    assert "translate the Priest novel" in packed["message"]
    assert "Which target language?" in packed["message"]
    # The planner's own suggestions seed what the survey offers as buttons.
    assert "Romanian" in packed["message"]
    # And the guide goes with it, which is the whole point of asking from there.
    assert "One package per problem." in packed["guide"]


def test_the_question_is_not_posted_to_the_chat(client, monkeypatch):  # noqa: F811
    """Only the plan comes back to the chat; the asking happens in the task."""
    from dex.planner import Plan

    async def asks(message, cfg, existing, model=None, project=None, guide="", survey=""):
        return Plan(needs_clarification="Which target language?")

    monkeypatch.setattr("dex.api.plan_from_message", asks)
    thread = _thread(client)
    client.post(f"/api/threads/{thread}/messages", json={"text": "translate it"})

    messages = client.get(f"/api/threads/{thread}").json()["thread"]["messages"]
    assert not any(
        (m.get("data") or {}).get("needsClarification") for m in messages
    ), "the question should be in the task, not the thread"


def test_a_message_carrying_material_is_still_surveyed_not_escalated(client):  # noqa: F811
    """Material wins: it is surveyed on its own account, without a question."""
    thread = _thread(client)
    name = _upload(client, "notes.md", b"# A\n")
    body = client.post(
        f"/api/threads/{thread}/messages",
        json={"text": "split this", "uploads": [name]},
    ).json()
    assert body["task"]["title"].startswith("Survey:")
