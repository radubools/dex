"""Tasks that start, continue and wait for other tasks (dex/deps.py, design/18)."""

from __future__ import annotations

import asyncio
import json

import pytest

from dex import deps as deps_mod
from dex.bus import EventBus
from dex.deps import Refused
from dex.models import TaskState
from dex.queue import TaskManager
from dex.runner import TaskRunner
from dex.store import SettingsStore, ThreadStore


@pytest.fixture
async def manager(config, db):
    bus = EventBus(db)
    await bus.start()
    m = TaskManager(config, bus, db)
    posted: list[tuple[str, str]] = []

    async def post(thread_id, message):
        posted.append((thread_id, message.text))

    m.post = post
    m.posted = posted
    try:
        yield m
    finally:
        await m.graph.stop()
        await bus.stop()


async def threads(db, project="algorithms"):
    store = ThreadStore(db)
    return {t: (await store.create(t, project)).id for t in ("Story — lessons", "Backgrounds and scenery")}


async def finish(m, task, state=TaskState.SUCCEEDED):
    await m.tasks.set_state(task.id, state, finished_at=1.0)


async def test_start_task_lands_in_the_named_thread_and_records_the_dependency(manager, db):
    ids = await threads(db)
    parent = await manager.submit("tell a story", title="Story", thread_id=ids["Story — lessons"])
    child = await manager.graph.spawn(parent, thread="backgrounds", title="A lab prop",
                                      brief="Make a lab prop with game-environment.")
    assert child.thread_id == ids["Backgrounds and scenery"]  # a unique prefix of the title
    assert await manager.graph.store.of(parent.id) == [child.id]
    assert any(t == ids["Backgrounds and scenery"] and "Story" in text for t, text in manager.posted)
    with pytest.raises(Refused, match="no thread"):
        await manager.graph.spawn(parent, thread="Music", title="x", brief="y")
    made = await manager.graph.spawn(parent, thread="Music", title="x", brief="y", create_thread=True)
    assert made.thread_id not in ids.values()


async def test_continue_task_follows_the_chain_until_its_follow_up_is_done(manager, db):
    asset = await manager.submit("make a bowl", title="Loss bowl", slug="dataviz-bowl")
    await finish(manager, asset)
    story = await manager.submit("story", title="GD story")
    out = await manager.graph.follow_up(story, "dataviz-bowl", "Make the rim thinner.")
    # The note started a new attempt of the same package; the dependency is not done.
    st = await manager.graph.store.status(asset.id)
    assert st["task"].attempt == 2 and st["task"].output_slug == "dataviz-bowl"
    assert out["task"].id == st["task"].id
    assert not st["done"]
    await finish(manager, st["task"])
    assert (await manager.graph.store.status(asset.id))["done"]


async def test_a_note_queued_on_a_running_task_keeps_it_not_done(manager):
    asset = await manager.submit("make a bowl", title="Loss bowl")
    await manager.tasks.set_state(asset.id, TaskState.RUNNING)
    story = await manager.submit("story", title="GD story")
    out = await manager.graph.follow_up(story, asset.slug, "Thinner rim.")
    assert out["running"]
    await finish(manager, asset)  # the run ends, but the note is still to be delivered
    assert not (await manager.graph.store.status(asset.id))["done"]



async def test_the_waker_starts_notes_that_missed_their_runs_end(manager, db):
    # Notes queued while a run is under way are delivered as it ends; one that
    # missed that moment (the run's final write landed late, a restart) left
    # its waiters blocked for good. The waker now starts it.
    await threads(db)
    voice = await manager.submit("caption the stories", title="Captions")
    await manager.tasks.set_state(voice.id, TaskState.RUNNING)
    story = await manager.submit("story", title="Story")
    await manager.graph.follow_up(story, voice.slug, "Caption the new story.")
    await manager.graph.park(story, [voice.id])
    await finish(manager, voice)  # ended without delivering the note
    assert await manager.graph.wake_ready() == 0  # it started the note instead of waking
    st = await manager.graph.store.status(voice.id)
    assert st["task"].attempt == 2 and st["task"].state is TaskState.QUEUED and not st["done"]
    assert "Caption the new story." in st["task"].problem
    assert (await manager.tasks.get(story.id)).state is TaskState.BLOCKED
    await finish(manager, st["task"])
    assert await manager.graph.wake_ready() == 1


async def test_a_worker_delivers_notes_only_after_the_runs_final_state_is_stored(manager, config):
    task = await manager.submit("make a bowl", title="Bowl")
    runner = TaskRunner(task, config, manager.bus, manager.tasks, manager.settings)
    runner.set_state(TaskState.SUCCEEDED)  # written in the background
    await runner.settled()
    assert (await manager.tasks.get(task.id)).state is TaskState.SUCCEEDED

async def test_cycles_depth_and_fan_out_are_refused(manager, monkeypatch):
    a = await manager.submit("a", title="A")
    ids = await threads(manager.db)
    b = await manager.graph.spawn(a, thread="Story", title="B", brief="b")
    with pytest.raises(Refused, match="cycle"):
        await manager.graph.follow_up(b, a.slug, "change")
    with pytest.raises(Refused, match="itself"):
        await manager.graph.follow_up(a, a.slug, "change")
    # Depth: a chain of MAX_DEPTH tasks may not start another.
    monkeypatch.setattr(deps_mod, "MAX_DEPTH", 2)
    c = await manager.graph.spawn(b, thread="Story", title="C", brief="c")
    with pytest.raises(Refused, match="levels"):
        await manager.graph.spawn(c, thread="Story", title="D", brief="d")
    # Fan-out.
    monkeypatch.setattr(deps_mod, "MAX_DEPS", 2)
    await manager.graph.spawn(a, thread="Story", title="E", brief="e")
    with pytest.raises(Refused, match="limit"):
        await manager.graph.spawn(a, thread="Story", title="F", brief="f")
    assert ids


async def test_a_blocked_task_wakes_first_once_everything_it_waits_on_is_done(manager, db):
    ids = await threads(db)
    old = await manager.submit("older queued work", title="Older")
    parent = await manager.submit("story", title="Story")
    child = await manager.graph.spawn(parent, thread="Backgrounds", title="Prop", brief="prop")
    await manager.tasks.set_state(parent.id, TaskState.RUNNING, started_at=1.0)
    parent.session_id = "sess-1"
    await manager.graph.park(parent, [child.id])
    stored = await manager.tasks.get(parent.id)
    assert stored.state is TaskState.BLOCKED and stored.waiting_on == [child.id]
    assert stored.resumed_from == "sess-1"
    # Still running: nothing wakes, and a blocked task is never claimed.
    assert await manager.graph.wake_ready() == 0
    claimed = await manager.tasks.claim("w")
    assert claimed.id == old.id
    await finish(manager, child)
    await manager.tasks.set_state(old.id, TaskState.QUEUED)  # put the older work back in line
    assert await manager.graph.wake_ready() == 1
    woken = await manager.tasks.get(parent.id)
    assert woken.state is TaskState.QUEUED and woken.waiting_on is None
    assert "Prop" in woken.wake_note and "succeeded" in woken.wake_note
    # Its priority puts it ahead of the older queued task.
    assert (await manager.tasks.claim("w")).id == parent.id
    assert ids


async def test_a_failed_dependency_still_wakes_its_waiter_with_the_error(manager, db):
    await threads(db)
    parent = await manager.submit("story", title="Story")
    child = await manager.graph.spawn(parent, thread="Backgrounds", title="Prop", brief="prop")
    await manager.tasks.set_state(child.id, TaskState.FAILED, error="timed out after 36000s", finished_at=1.0)
    await manager.graph.park(parent, [child.id])
    await manager.graph.wake_ready()
    note = (await manager.tasks.get(parent.id)).wake_note
    assert "failed" in note and "timed out" in note


async def test_the_graph_shows_active_chains_their_dependencies_and_isolated_tasks(manager, db):
    await threads(db)
    parent = await manager.submit("story", title="Story")
    child = await manager.graph.spawn(parent, thread="Backgrounds", title="Prop", brief="prop")
    alone = await manager.submit("unrelated", title="Alone")
    done = await manager.submit("old", title="Old and done")
    await finish(manager, done)
    g = await manager.graph.store.graph("algorithms")
    by_title = {n["title"]: n for n in g["nodes"]}
    assert set(by_title) == {"Story", "Prop", "Alone"}
    assert g["edges"] == [{"from": parent.id, "to": child.id, "kind": "started"}]
    assert by_title["Prop"]["threadTitle"] == "Backgrounds and scenery"
    # A follow-up attempt replaces its chain's node and keeps the edge.
    await finish(manager, child)
    await manager.graph.follow_up(parent, child.slug, "bigger")
    g = await manager.graph.store.graph("algorithms")
    prop = next(n for n in g["nodes"] if n["title"] == "Prop")
    assert prop["attempt"] == 2 and g["edges"][0]["to"] == prop["id"]
    assert alone


async def test_find_tasks_matches_words_and_returns_one_entry_per_package(manager):
    a = await manager.submit("brief about a loss bowl surface", title="Loss bowl", slug="dataviz-bowl")
    await finish(manager, a)
    await manager.queue_message(a.id, "thinner")  # attempt 2 of the same package
    await manager.submit("a builder", title="Player builder", slug="player-builder")
    hits = await manager.graph.find("algorithms", "bowl")
    assert [h["package"] for h in hits] == ["dataviz-bowl"]
    assert hits[0]["attempt"] == 2
    assert {h["package"] for h in await manager.graph.find("algorithms", "surface builder")} == {
        "dataviz-bowl", "player-builder"}


# ---- the agent's tools ------------------------------------------------------


def tools_of(runner):
    return {t.name: t.handler for t in runner._task_tools()}


async def test_wait_for_tasks_parks_a_task_and_only_checks_back_in_a_conversation(manager, db, config):
    await threads(db)
    parent = await manager.submit("story", title="Story")
    runner = TaskRunner(parent, config, manager.bus, manager.tasks, SettingsStore(db))
    runner.graph = manager.graph
    tools = tools_of(runner)
    started = json.loads((await tools["start_task"]({"thread": "Backgrounds", "title": "Prop", "brief": "make it", "slug": "background-prop"}))
                         ["content"][0]["text"])["started"]
    out = json.loads((await tools["wait_for_tasks"]({}))["content"][0]["text"])
    assert out["parked"] and runner._park_on == [started["id"]]
    # Already done: no parking, the statuses come straight back.
    await finish(manager, await manager.tasks.get(started["id"]))
    runner._park_on = None
    out = json.loads((await tools["wait_for_tasks"]({"tasks": [started["slug"]]}))["content"][0]["text"])
    assert out["done"] and runner._park_on is None
    # A design turn is a conversation: it parks too, and is told to say what it waits for.
    design = await manager.submit("talk", title="Design", scope="design")
    chat = TaskRunner(design, config, manager.bus, manager.tasks, SettingsStore(db))
    chat.graph = manager.graph
    other = await manager.graph.spawn(design, thread="Backgrounds", title="Prop 2", brief="b")
    out = json.loads((await tools_of(chat)["wait_for_tasks"]({"tasks": [other.id]}))["content"][0]["text"])
    assert out["parked"] and "Tell the person" in out["note"] and chat._park_on == [other.id]


async def test_waiting_on_a_task_that_waits_for_this_one_is_refused(manager, db, config):
    await threads(db)
    a = await manager.submit("a", title="A")
    b = await manager.graph.spawn(a, thread="Story", title="B", brief="b")
    runner = TaskRunner(b, config, manager.bus, manager.tasks, SettingsStore(db))
    runner.graph = manager.graph
    text = (await tools_of(runner)["wait_for_tasks"]({"tasks": [a.slug]}))["content"][0]["text"]
    assert text.startswith("Refused") and runner._park_on is None


# ---- a run that waits ends blocked, and comes back with the news -------------


class Client:
    """A scripted agent: each query runs `on_query(prompt)`; it writes nothing itself."""

    prompts: list[str] = []
    on_query = None

    def __init__(self, options):
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        Client.prompts.append(prompt)
        if Client.on_query:
            Client.on_query(prompt)
        await asyncio.sleep(0.05)  # a turn takes a moment: the run's state writes land in order

    async def receive_response(self):
        return
        yield  # pragma: no cover


async def test_a_run_that_waits_ends_blocked_not_failed_and_resumes_with_the_news(manager, db, config, monkeypatch):
    await threads(db)
    monkeypatch.setattr("dex.runner.ClaudeSDKClient", Client, raising=False)
    parent = await manager.submit("tell a story", title="Story", slug="story-x")
    child = await manager.graph.spawn(parent, thread="Backgrounds", title="Prop", brief="prop")
    runner = TaskRunner(parent, config, manager.bus, manager.tasks, SettingsStore(db))
    runner.graph = manager.graph
    parent.session_id = "sess-1"
    Client.prompts = []
    Client.on_query = lambda prompt: setattr(runner, "_park_on", [child.id])  # it called wait_for_tasks
    await runner.run()
    stored = await manager.tasks.get(parent.id)
    # Ended its turn having written nothing: blocked, not the empty-package failure.
    assert stored.state is TaskState.BLOCKED, stored.error
    assert stored.waiting_on == [child.id] and stored.error is None
    await finish(manager, child)
    assert await manager.graph.wake_ready() == 1
    woken = await manager.tasks.get(parent.id)
    assert woken.resumed_from == "sess-1"
    # The resumed session is told how the tasks went, not handed the brief again.
    Client.prompts = []

    def wrote(prompt):
        (config.assets_dir / "algorithms" / "story-x").mkdir(parents=True, exist_ok=True)
        (config.assets_dir / "algorithms" / "story-x" / "story.json").write_text("{}")

    Client.on_query = wrote
    again = TaskRunner(woken, config, manager.bus, manager.tasks, SettingsStore(db))
    again.graph = manager.graph
    await again.run()
    assert Client.prompts[0].startswith("## The tasks you waited for are done")
    assert "Prop" in Client.prompts[0]
    assert (await manager.tasks.get(parent.id)).wake_note is None


@pytest.mark.parametrize("scope", ["package", "chat"])
async def test_a_run_that_started_work_is_blocked_until_it_is_done_even_without_waiting(
        manager, db, config, monkeypatch, scope):
    """Not done before what it asked for is: a story, or a chat, that started or continued a
    task and ended its turn is blocked (not succeeded), and finishes on the next run."""
    await threads(db)
    monkeypatch.setattr("dex.runner.ClaudeSDKClient", Client, raising=False)
    parent = await manager.submit("ask for a change", title="Asker", slug=f"asker-{scope}", scope=scope)
    runner = TaskRunner(parent, config, manager.bus, manager.tasks, SettingsStore(db))
    runner.graph = manager.graph
    holder = {}

    def ask(prompt):
        (config.assets_dir / "algorithms" / parent.slug).mkdir(parents=True, exist_ok=True)
        (config.assets_dir / "algorithms" / parent.slug / "notes.md").write_text("asked")
        holder.setdefault("go", True)

    Client.prompts, Client.on_query = [], ask
    # What start_task does from inside the turn.
    child = await manager.graph.spawn(parent, thread="Backgrounds", title="Prop", brief="prop")
    if scope == "chat":
        monkeypatch.setattr(TaskRunner, "_converse", _one_turn)
    await runner.run()
    stored = await manager.tasks.get(parent.id)
    assert stored.state is TaskState.BLOCKED and stored.waiting_on == [child.id]
    # Once the child is done it is woken, resumed, and this time it finishes.
    await finish(manager, child)
    assert await manager.graph.wake_ready() == 1
    again = TaskRunner(await manager.tasks.get(parent.id), config, manager.bus, manager.tasks, SettingsStore(db))
    again.graph = manager.graph
    await again.run()
    for _ in range(50):  # the runner persists its final state in the background
        if (await manager.tasks.get(parent.id)).state.terminal:
            break
        await asyncio.sleep(0.02)
    final = await manager.tasks.get(parent.id)
    assert final.state is TaskState.SUCCEEDED, final.error
    assert "The tasks you waited for are done" in Client.prompts[-1]


async def _one_turn(self, client):
    """A chat turn with no player messages: the opening prompt's answer, then the run ends."""
    async for message in client.receive_response():
        self._observe(message)


async def test_end_to_end_tests_off_passes_down_to_started_and_continued_tasks(manager, db):
    await threads(db)
    chat = await manager.submit("talk", title="Chat", scope="chat", options={"e2e": False})
    started = await manager.graph.spawn(chat, thread="Backgrounds", title="Prop", brief="prop")
    assert started.options == {"e2e": False} and not started.e2e
    asset = await manager.submit("bowl", title="Bowl", slug="dataviz-bowl")
    await finish(manager, asset)
    out = await manager.graph.follow_up(chat, "dataviz-bowl", "thinner rim")
    assert out["task"].attempt == 2 and out["task"].options == {"e2e": False}
    assert "End-to-end tests are off" in out["task"].problem
    # And down again: what the started task starts runs the same way.
    grandchild = await manager.graph.spawn(started, thread="Story", title="Note", brief="n")
    assert not grandchild.e2e
    # A task started normally runs its tests.
    assert (await manager.submit("x", title="Plain")).e2e


async def test_a_task_with_tests_off_is_told_and_gets_the_env_flag(manager, db, config, monkeypatch):
    seen = {}

    class Recording(Client):
        def __init__(self, options):
            super().__init__(options)
            seen["env"] = getattr(options, "env", None)

    monkeypatch.setattr("dex.runner.ClaudeSDKClient", Recording, raising=False)
    task = await manager.submit("make a story", title="Story", slug="story-off", options={"e2e": False})
    runner = TaskRunner(task, config, manager.bus, manager.tasks, SettingsStore(db))
    runner.graph = manager.graph
    Client.prompts, Client.on_query = [], None
    await runner.run()
    assert "End-to-end tests are off for this task" in Client.prompts[0]
    assert seen["env"]["DEX_SKIP_E2E"] == "1"


async def test_start_task_refuses_a_thread_that_type_of_package_is_not_made_in(manager, db):
    ids = await threads(db)
    for n in range(2):  # dataviz- packages live in the backgrounds thread here
        await manager.submit("d", title=f"D{n}", slug=f"dataviz-thing-{n}", thread_id=ids["Backgrounds and scenery"])
    chat = await manager.submit("talk", title="Chat", scope="chat")
    with pytest.raises(Refused, match="made in the thread “Backgrounds and scenery”"):
        await manager.graph.spawn(chat, thread="Story", title="Bowl", brief="b", slug="dataviz-bowl")
    ok = await manager.graph.spawn(chat, thread="Backgrounds", title="Bowl", brief="b", slug="dataviz-bowl")
    assert ok.thread_id == ids["Backgrounds and scenery"]
    # A type with no home yet may go anywhere.
    await manager.graph.spawn(chat, thread="Story", title="New kind", brief="b", slug="gizmo-one")


async def test_a_chat_may_not_make_packages_itself(tmp_path):
    from dex.permissions import PermissionPolicy, auto_deny

    task_dir, game = tmp_path / "chat-x", tmp_path / "game"
    policy = PermissionPolicy(workspace=tmp_path, task_dir=task_dir, project_dir=tmp_path,
                              extra_writable=(game,), escalate=auto_deny,
                              delegate_assets=True, asset_dirs=(game / "public" / "assets",))
    refuse = policy._refuse_reason
    assert "own task" in refuse("Write", {"file_path": str(task_dir / "story.json")})
    assert "own task" in refuse("Write", {"file_path": str(task_dir / "pkg" / "tree.glb")})
    assert "installed assets" in refuse("Edit", {"file_path": str(game / "public/assets/story/x/story.json")})
    assert refuse("Bash", {"command": "cp -r ~/dex/assets/ml/story-x /x/public/assets/story/"}) is not None
    assert refuse("Bash", {"command": "python -c 'story.export_story(p, root)'"}) is not None
    # The story catalog is a package like any other: its own task makes it.
    assert "own task" in refuse("Write", {"file_path": str(task_dir / "catalog.json")})
    assert refuse("Bash", {"command": "python -c 'sc.write_catalog(p, cat)'"}) is not None
    # What a chat may do: notes, the game's code, the game's own pull.
    assert refuse("Write", {"file_path": str(task_dir / "notes.md")}) is None
    assert refuse("Edit", {"file_path": str(game / "src" / "main.js")}) is None
    assert refuse("Bash", {"command": "python /g/tools/pull_dex_assets.py /g/model-workshop --only story-x"}) is None
    # Other tasks are not chats: nothing changes for them.
    plain = PermissionPolicy(workspace=tmp_path, task_dir=task_dir, escalate=auto_deny)
    assert plain._refuse_reason("Write", {"file_path": str(task_dir / "story.json")}) is None


async def test_prioritize_moves_a_task_and_everything_under_it_to_the_front(manager, db):
    await threads(db)
    old = await manager.submit("older queued work", title="Older")
    parent = await manager.submit("story", title="Story")
    child = await manager.graph.spawn(parent, thread="Backgrounds", title="Prop", brief="prop")
    grand = await manager.graph.spawn(child, thread="Backgrounds", title="Prop texture", brief="texture")
    # A finished dependency continued by a note: its newest attempt is the one under the story.
    bowl = await manager.submit("make a bowl", title="Bowl", slug="dataviz-bowl")
    await finish(manager, bowl)
    again = (await manager.graph.follow_up(parent, "dataviz-bowl", "Thinner rim."))["task"]
    done = await manager.graph.spawn(parent, thread="Backgrounds", title="Done prop", brief="done")
    await finish(manager, done)
    await manager.tasks.set_state(parent.id, TaskState.BLOCKED, waiting_on=json.dumps([child.id]))
    unrelated = await manager.submit("other work", title="Other")

    raised = await manager.graph.prioritize(parent.id)
    assert {t.id for t in raised} == {parent.id, child.id, grand.id, again.id}
    assert (await manager.tasks.get(done.id)).priority == 0  # finished: nothing to move
    assert (await manager.tasks.get(unrelated.id)).priority == 0
    assert all(t.to_json(manager.config.assets_dir)["canPrioritize"] is False for t in raised)
    # The next free slots take what is under the story, before older work.
    first = {(await manager.tasks.claim("w")).id for _ in range(3)}
    assert first == {child.id, grand.id, again.id}
    assert (await manager.tasks.claim("w")).id == old.id
    # Woken, the story keeps its place ahead of other woken work.
    for t in (child, grand, again):
        await finish(manager, t)
    assert await manager.graph.wake_ready() == 1
    assert (await manager.tasks.get(parent.id)).priority == deps_mod.PRIORITIZED
    # Prioritizing again changes nothing.
    assert [t.id for t in await manager.graph.prioritize(parent.id)] == []


async def test_a_free_slot_takes_a_prioritized_paused_task_before_queued_work(manager, db):
    """Paused work used to wait for the next rebalance, which queued work beat to every freed slot."""
    queued = await manager.submit("queued work", title="Queued")
    paused = await manager.submit("captions", title="Captions")
    await manager.tasks.set_state(paused.id, TaskState.PAUSED, started_at=1.0, session_id="sess-9", finished_at=2.0)
    await manager.tasks.prioritize([paused.id], deps_mod.PRIORITIZED)
    claimed = await manager.tasks.claim("w")
    assert claimed.id == paused.id
    assert claimed.resumed_from == "sess-9" and claimed.finished_at is None
    assert (await manager.tasks.claim("w")).id == queued.id
