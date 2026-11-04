"""Routing a chat message (design/26): what a reply can do, and what the API does with it."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace as Row

import pytest

from dex.planner import Plan, PlannedTask
from dex.router import Route, parse_route
from dex.threads import intent_of

from tests.test_api import clean, client  # noqa: F401

design = Row(id="d", kind="project_design", title="ML · design", skill=None)
main = Row(id="m", kind="main", title="Main", skill=None)
music = Row(id="s", kind="chat", title="Music", skill="game-music")
players = Row(id="c1", kind="chat", title="Players", skill="game-characters")
enemies = Row(id="c2", kind="chat", title="Enemies", skill="game-characters")
power = Row(id="p", kind="chat", title="Power", skill="power")
errands = Row(id="e", kind="chat", title="tag my poses", skill=None)
threads = [design, main, music, players, enemies, power, errands]
tasks = [{"id": "t1"}]
declared = {"game-music": "Music", "game-sfx": "Sound effects", "power": "Power"}


def route(here=main, **reply):
    return parse_route(json.dumps(reply), here, threads, tasks, declared)


@pytest.mark.parametrize("thread,intent", [(design, "design"), (main, "task"), (music, "skill"), (power, "skill"), (errands, "task")])
def test_a_thread_is_a_skills_only_if_it_is_tied_to_one(thread, intent):
    assert intent_of(thread.kind, thread.skill) == intent


def test_a_known_task_is_continued_and_an_invented_one_is_not():
    assert route(intent="skill", task_id="t1").task_id == "t1"
    assert route(intent="task", task_id="nope") is None


def test_design_always_goes_to_the_design_thread():
    assert route(intent="design", thread_id="s").thread_id == "d"


def test_a_skill_goes_to_its_thread_whatever_thread_the_reply_named():
    assert route(intent="skill", skill="game-music", thread_id="e").thread_id == "s"
    assert route(intent="skill", skill="power").thread_id == "p"


def test_a_skill_with_several_threads_may_pick_only_among_them():
    assert route(intent="skill", skill="game-characters", thread_id="c2").thread_id == "c2"
    assert route(intent="skill", skill="game-characters", thread_id="s").thread_id == "c1"


def test_a_thread_is_only_ever_made_for_a_skill_that_declares_one():
    assert route(intent="skill", skill="game-sfx") == Route("skill", skill="game-sfx")
    assert route(intent="skill", skill="made-up") is None
    assert route(intent="skill", new_thread="Voices") is None
    assert route(intent="task", new_thread="Voices") is None


def test_general_work_never_lands_in_a_skills_thread():
    assert route(intent="task", thread_id="s") is None
    assert route(intent="task", thread_id="e").thread_id == "e"


def test_work_cant_stay_in_the_design_thread():
    assert route(here=design, intent="task").thread_id == "m"
    assert route(here=design, intent="design") is None


def test_what_fits_stays_and_nonsense_stays():
    assert route(here=music, intent="skill", skill="game-music") is None
    assert parse_route("I think music?", main, threads, tasks) is None


def test_new_work_in_a_tasks_activity_says_so_even_if_it_stays():
    assert route(here=music, intent="skill", skill="game-music", new_task=True) == Route("skill", new_task=True)


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


def test_a_routed_note_says_the_name_the_message_gave_the_thread(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    empty = client.post("/api/threads", json={}).json()["thread"]
    routed_to(lambda threads, tasks: Route("task", thread_id=empty["id"]))
    got = client.post(f"/api/threads/{main_id}/messages", json={"text": "tag my poses"}).json()
    assert got["routed"]["threadTitle"] == "tag my poses"


def test_every_project_has_a_main_thread(client):
    assert thread_of(client, "main")["intent"] == "task"
    assert thread_of(client, "project_design")["intent"] == "design"


def test_a_message_moves_to_its_thread_and_says_so(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    other = client.post("/api/threads", json={"title": "Music"}).json()["thread"]
    routed_to(lambda threads, tasks: Route("skill", "a track", thread_id=other["id"]))
    got = client.post(f"/api/threads/{main_id}/messages", json={"text": "a new track"}).json()
    assert got == {"routed": got["routed"]} and got["routed"]["threadId"] == other["id"]
    here = client.get(f"/api/threads/{main_id}").json()["thread"]["messages"]
    assert [m["kind"] for m in here][-2:] == ["text", "routed"]
    there = lambda: client.get(f"/api/threads/{other['id']}").json()["thread"]["messages"]
    assert there()[0]["text"] == "a new track"
    deadline = time.time() + 5
    while not any(m["kind"] == "plan" for m in there()):
        assert time.time() < deadline, "the plan never came"
        time.sleep(0.02)


def with_skill(client, project, name="game-sfx", title="Sound effects", version="abc123"):
    """A skill that declares a thread, enabled in `project`."""
    config = client.config
    path = config.workspace / "skills" / name / version
    path.mkdir(parents=True)
    (path / "skill.json").write_text(json.dumps({"name": name, "thread": title}))
    (config.project_dir(project) / "skills.json").write_text(json.dumps({"skills": {name: version}}))


def test_a_skills_thread_is_made_once_and_tied(client, routed_to):
    project = client.get("/api/threads").json()["project"]
    with_skill(client, project)
    main_id = thread_of(client, "main")["id"]
    routed_to(lambda threads, tasks: Route("skill", skill="game-sfx"))
    first = client.post(f"/api/threads/{main_id}/messages", json={"text": "a click"}).json()["routed"]
    second = client.post(f"/api/threads/{main_id}/messages", json={"text": "a beep"}).json()["routed"]
    assert first["threadTitle"] == "Sound effects" and second["threadId"] == first["threadId"]
    tied = [t for t in client.get("/api/threads").json()["threads"] if t["skill"] == "game-sfx"]
    assert len(tied) == 1 and tied[0]["intent"] == "skill"


def test_a_plan_sends_each_row_to_its_skills_thread(client):
    project = client.get("/api/threads").json()["project"]
    with_skill(client, project)
    main_id = thread_of(client, "main")["id"]
    got = client.post("/api/chat/confirm", json={"thread_id": main_id, "tasks": [
        {"title": "A", "problem": "Make a click sound", "slug": "sfx-click", "skill": "game-sfx"},
        {"title": "B", "problem": "A general errand", "slug": "errand"},
    ]}).json()["tasks"]
    sfx = next(t for t in client.get("/api/threads").json()["threads"] if t["skill"] == "game-sfx")
    assert got[0]["threadId"] == sfx["id"] and got[1]["threadId"] == main_id
    said = client.get(f"/api/threads/{sfx['id']}").json()["thread"]["messages"]
    assert said[-1]["kind"] == "tasks" and said[-1]["text"].startswith("From Main.")


def test_a_thread_can_be_tied_and_untied_by_hand(client):
    project = client.get("/api/threads").json()["project"]
    with_skill(client, project)
    thread = client.post("/api/threads", json={"title": "Bleeps"}).json()["thread"]
    assert thread["intent"] == "task"
    tied = client.put(f"/api/threads/{thread['id']}/skill", json={"skill": "game-sfx"}).json()["thread"]
    assert tied["skill"] == "game-sfx" and tied["intent"] == "skill"
    assert client.put(f"/api/threads/{thread['id']}/skill", json={"skill": "nope"}).status_code == 409
    assert client.put(f"/api/threads/{thread['id']}/skill", json={"skill": None}).json()["thread"]["skill"] is None


def test_work_for_an_existing_task_goes_to_it(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    task = client.post("/api/chat/confirm", json={"thread_id": main_id, "tasks": [
        {"title": "T", "problem": "Full statement of it", "slug": "t-one"}]}).json()["tasks"][0]
    routed_to(lambda threads, tasks: Route("task", task_id=task["id"]))
    got = client.post(f"/api/threads/{main_id}/messages", json={"text": "fix it"}).json()
    assert got["routed"]["taskId"] == task["id"]
    assert got["message"]["body"] == "fix it"


def test_other_work_posted_in_a_tasks_activity_starts_new_work(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    task = client.post("/api/chat/confirm", json={"thread_id": main_id, "tasks": [
        {"title": "T", "problem": "Full statement of it", "slug": "t-two"}]}).json()["tasks"][0]
    routed_to(lambda threads, tasks: Route("task", new_task=True))
    got = client.post(f"/api/tasks/{task['id']}/messages", json={"text": "something else"}).json()
    assert got == {"message": None, "routed": got["routed"]} and got["routed"]["threadId"] == main_id
    said = client.get(f"/api/threads/{main_id}").json()["thread"]["messages"]
    assert said[-1]["text"] == "something else" or any(m["text"] == "something else" for m in said)
    assert client.get(f"/api/tasks/{task['id']}/messages").json()["messages"] == []


def test_a_routed_post_answers_before_planning_ends(client, routed_to, monkeypatch):
    main_id = thread_of(client, "main")["id"]
    other = client.post("/api/threads", json={"title": "Music"}).json()["thread"]
    routed_to(lambda threads, tasks: Route("skill", thread_id=other["id"]))

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(2)
        raise RuntimeError("too late to matter")
    monkeypatch.setattr("dex.api.plan_from_message", slow)
    started = time.time()
    client.post(f"/api/threads/{main_id}/messages", json={"text": "a new track"})
    assert time.time() - started < 1


def test_new_work_that_stays_in_its_thread_is_planned_there(client, routed_to, monkeypatch):
    main_id = thread_of(client, "main")["id"]
    routed_to(lambda threads, tasks: Route("task", "new work", new_task=True))
    plan_of(monkeypatch, "ingest")
    got = client.post(f"/api/threads/{main_id}/messages", json={"text": "run the pipeline"})
    assert got.status_code == 200 and "routed" not in got.json()


def test_a_message_that_cannot_be_handled_says_so_in_the_thread(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    routed_to(lambda threads, tasks: Route("skill", "a track", skill="nothing-declares-this"))
    got = client.post(f"/api/threads/{main_id}/messages", json={"text": "a new track"})
    assert got.status_code == 500
    last = client.get(f"/api/threads/{main_id}").json()["thread"]["messages"][-1]
    assert last["kind"] == "error" and "nothing-declares-this" in last["text"]


def plan_of(monkeypatch, *slugs, remaining=""):
    async def fake(*_args, **_kwargs):
        return Plan(tasks=[PlannedTask(title=s, problem=f"Do all of {s}.", slug=s) for s in slugs], remaining=remaining)
    monkeypatch.setattr("dex.api.plan_from_message", fake)


def kinds(client, thread_id):
    return [m["kind"] for m in client.get(f"/api/threads/{thread_id}").json()["thread"]["messages"]]


def test_a_plan_of_one_task_starts_without_asking(client, monkeypatch):
    main_id = thread_of(client, "main")["id"]
    plan_of(monkeypatch, "one-thing")
    client.post(f"/api/threads/{main_id}/messages", json={"text": "do one thing"})
    assert kinds(client, main_id)[-2:] == ["plan", "tasks"]
    said = client.get(f"/api/threads/{main_id}").json()["thread"]["messages"]
    assert said[-2]["data"]["autoStarted"] is True


@pytest.mark.parametrize("slugs,remaining", [(("a-a", "b-b"), ""), (("a-a",), "and more after it")])
def test_anything_more_still_waits_for_run(client, monkeypatch, slugs, remaining):
    main_id = thread_of(client, "main")["id"]
    plan_of(monkeypatch, *slugs, remaining=remaining)
    client.post(f"/api/threads/{main_id}/messages", json={"text": "do these"})
    assert "tasks" not in kinds(client, main_id)
    said = client.get(f"/api/threads/{main_id}").json()["thread"]["messages"]
    assert not any(m["data"].get("autoStarted") for m in said)


def test_the_same_task_keeps_the_message(client, routed_to):
    main_id = thread_of(client, "main")["id"]
    task = client.post("/api/chat/confirm", json={"thread_id": main_id, "tasks": [
        {"title": "T", "problem": "Full statement of it", "slug": "t-three"}]}).json()["tasks"][0]
    routed_to(lambda threads, tasks: Route("task", task_id=task["id"]))
    got = client.post(f"/api/tasks/{task['id']}/messages", json={"text": "more of it"}).json()
    assert got["message"]["body"] == "more of it"
