"""Routing a chat message (design/26): what a reply can do, and what the API does with it."""

from __future__ import annotations

import json
from types import SimpleNamespace as Row

import pytest

from dex.router import Route, parse_route
from dex.threads import intent_of

from tests.test_api import clean, client  # noqa: F401

design = Row(id="d", kind="project_design", title="ML · design")
main = Row(id="m", kind="main", title="Main")
music = Row(id="s", kind="chat", title="Music")
power = Row(id="p", kind="chat", title="Power")
threads = [design, main, music, power]
tasks = [{"id": "t1"}]


def route(here=main, **reply):
    return parse_route(json.dumps(reply), here, threads, tasks)


@pytest.mark.parametrize("thread,intent", [(design, "design"), (main, "task"), (music, "skill"), (power, "task")])
def test_a_thread_decides_its_intent(thread, intent):
    assert intent_of(thread.kind, thread.title) == intent


def test_a_known_task_is_continued_and_an_invented_one_is_not():
    assert route(intent="skill", task_id="t1").task_id == "t1"
    assert route(intent="task", task_id="nope") is None


def test_design_always_goes_to_the_design_thread():
    assert route(intent="design", thread_id="s").thread_id == "d"


def test_a_skill_thread_is_only_for_skill_work():
    assert route(intent="task", thread_id="s") is None
    assert route(intent="skill", thread_id="s").thread_id == "s"


def test_a_new_skill_thread_is_made_only_if_none_has_that_name():
    assert route(intent="skill", new_thread="Voices").new_thread == "Voices"
    assert route(intent="skill", new_thread="music").thread_id == "s"
    assert route(intent="task", new_thread="Voices") is None


def test_work_cant_stay_in_the_design_thread():
    assert route(here=design, intent="task").thread_id == "m"
    assert route(here=design, intent="design") is None


def test_what_fits_stays_and_nonsense_stays():
    assert route(here=music, intent="skill", thread_id="s") is None
    assert parse_route("I think music?", main, threads, tasks) is None


def test_new_work_in_a_tasks_activity_says_so_even_if_it_stays():
    assert route(here=music, intent="skill", thread_id="s", new_task=True) == Route("skill", new_task=True)


@pytest.fixture
def routed_to(client, monkeypatch):
    """Make the router answer `route(listed threads) -> Route`."""
    def answer(pick):
        async def fake(text, config, *, here, threads, tasks, **_):
            return pick(threads, tasks)
        monkeypatch.setattr("dex.api.route_message", fake)
    return answer


def thread_of(client, kind):
    listed = client.get("/api/threads").json()["threads"]
    return next(t for t in listed if t["kind"] == kind)


def test_every_project_has_a_main_thread(client):
    assert thread_of(client, "main")["intent"] == "task"
    assert thread_of(client, "project_design")["intent"] == "design"


def test_a_message_moves_to_its_thread_and_says_so(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    other = client.post("/api/threads", json={"title": "Music"}).json()["thread"]
    routed_to(lambda threads, tasks: Route("skill", "a track", thread_id=other["id"]))
    got = client.post(f"/api/threads/{main_id}/messages", json={"text": "a new track"}).json()
    assert got["routed"]["threadId"] == other["id"]
    here = client.get(f"/api/threads/{main_id}").json()["thread"]["messages"]
    assert [m["kind"] for m in here][-2:] == ["text", "routed"]
    there = client.get(f"/api/threads/{other['id']}").json()["thread"]["messages"]
    assert there[0]["text"] == "a new track"
    assert any(m["kind"] == "plan" for m in there)


def test_a_new_skill_thread_is_created(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    routed_to(lambda threads, tasks: Route("skill", new_thread="Voices"))
    got = client.post(f"/api/threads/{main_id}/messages", json={"text": "narrate it"}).json()
    assert got["routed"]["threadTitle"] == "Voices"
    assert thread_of(client, "chat")["title"] == "Voices"


def test_work_for_an_existing_task_goes_to_it(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    task = client.post("/api/chat/confirm", json={"thread_id": main_id, "tasks": [
        {"title": "T", "problem": "Full statement of it", "slug": "t-one"}]}).json()["tasks"][0]
    routed_to(lambda threads, tasks: Route("task", task_id=task["id"]))
    got = client.post(f"/api/threads/{main_id}/messages", json={"text": "fix it"}).json()
    assert got["routed"]["taskId"] == task["id"]
    assert client.get(f"/api/tasks/{task['id']}/messages").json()["messages"][0]["body"] == "fix it"


def test_other_work_posted_in_a_tasks_activity_starts_new_work(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    task = client.post("/api/chat/confirm", json={"thread_id": main_id, "tasks": [
        {"title": "T", "problem": "Full statement of it", "slug": "t-two"}]}).json()["tasks"][0]
    routed_to(lambda threads, tasks: Route("task", new_task=True))
    got = client.post(f"/api/tasks/{task['id']}/messages", json={"text": "something else"}).json()
    assert got == {"message": None, "routed": got["routed"]} and got["routed"]["threadId"] == main_id
    assert client.get(f"/api/tasks/{task['id']}/messages").json()["messages"] == []


def test_the_same_task_keeps_the_message(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    task = client.post("/api/chat/confirm", json={"thread_id": main_id, "tasks": [
        {"title": "T", "problem": "Full statement of it", "slug": "t-three"}]}).json()["tasks"][0]
    routed_to(lambda threads, tasks: Route("task", task_id=task["id"]))
    got = client.post(f"/api/tasks/{task['id']}/messages", json={"text": "more of it"}).json()
    assert got["message"]["body"] == "more of it"
