"""Threads, messages, and tasks in Postgres."""

from __future__ import annotations

import pytest

from dex.models import Task, TaskState
from dex.store import TaskStore, ThreadStore
from dex.threads import ThreadMessage


async def test_thread_round_trips(db):
    threads = ThreadStore(db)
    thread = await threads.create()
    await threads.append(thread.id, ThreadMessage(role="user", text="two sum please"))

    restored = await threads.get(thread.id)
    assert restored is not None
    assert restored.messages[0].text == "two sum please"
    # The first thing the operator says names the thread.
    assert restored.title == "two sum please"


async def test_messages_keep_their_order_and_payload(db):
    threads = ThreadStore(db)
    thread = await threads.create()
    await threads.append(thread.id, ThreadMessage(role="user", text="first"))
    await threads.append(
        thread.id,
        ThreadMessage(role="dex", kind="plan", text="split", data={"tasks": [{"slug": "two-sum"}]}),
    )

    restored = await threads.get(thread.id)
    assert [m.text for m in restored.messages] == ["first", "split"]
    assert restored.messages[1].data["tasks"][0]["slug"] == "two-sum"


async def test_threads_list_newest_first_with_counts(db):
    threads = ThreadStore(db)
    older = await threads.create("older")
    newer = await threads.create("newer")
    await threads.append(older.id, ThreadMessage(role="user", text="bump"))

    listed = await threads.list()
    assert [t.id for t in listed] == [older.id, newer.id]
    assert listed[0].message_count == 1


async def test_hiding_a_thread_keeps_everything_it_holds(db):
    """Hiding replaced deleting after a mis-tap cost a month of conversation.

    Deleting cascaded the messages away and set the tasks' thread_id to NULL,
    which is unrecoverable. Hiding has to leave every row where it is.
    """
    threads = ThreadStore(db)
    tasks = TaskStore(db)
    thread = await threads.create()
    await threads.append(thread.id, ThreadMessage(role="user", text="x"))
    task = Task(problem="p", title="Two Sum", slug="two-sum")
    task.thread_id = thread.id
    await tasks.create(task)

    assert await threads.hide(thread.id) is True

    # Gone from the list, but nothing is destroyed.
    assert [t.id for t in await threads.list()] == []
    assert await threads.get(thread.id) is not None
    assert await db.pool.fetchval("SELECT count(*) FROM messages") == 1
    assert (await tasks.get(task.id)).thread_id == thread.id

    assert [t.id for t in await threads.list(include_hidden=True)] == [thread.id]
    assert await threads.unhide(thread.id) is True
    assert [t.id for t in await threads.list()] == [thread.id]


async def test_tasks_survive_and_report_their_lineage(db):
    tasks = TaskStore(db)
    first = Task(problem="p", title="Two Sum", slug="two-sum")
    await tasks.create(first)

    second = Task(problem="p", title="Two Sum", slug="two-sum-2")
    second.parent_id = first.id
    second.attempt = 2
    second.output_slug = "two-sum"
    await tasks.create(second)

    restored = await tasks.get(second.id)
    assert restored.parent_id == first.id
    assert restored.attempt == 2
    # A resumed attempt writes into its parent's package.
    assert restored.output_slug == "two-sum"
    assert await tasks.taken_slugs() == {"two-sum", "two-sum-2"}


async def test_claim_hands_each_task_to_one_worker(db):
    tasks = TaskStore(db)
    for slug in ("a", "b"):
        await tasks.create(Task(problem="p", title=slug, slug=slug))

    first = await tasks.claim("worker-1")
    second = await tasks.claim("worker-2")
    third = await tasks.claim("worker-3")

    assert {first.slug, second.slug} == {"a", "b"}
    assert first.state is TaskState.RUNNING
    assert third is None  # nothing left queued


async def test_orphans_are_released_when_the_worker_is_gone(db):
    tasks = TaskStore(db)
    await tasks.create(Task(problem="p", title="a", slug="a"))
    claimed = await tasks.claim("otherhost:999999")

    # A fresh claim on another host is not yet stale, so nothing is released.
    assert await tasks.release_orphans(90, "thishost") == []

    # A claim from a process on this host that no longer exists is released at
    # once — that is the state a killed server leaves behind.
    await db.pool.execute(
        "UPDATE tasks SET claimed_by = $2 WHERE id = $1", claimed.id, "thishost:999999"
    )
    assert await tasks.release_orphans(90, "thishost") == [claimed.id]

    recovered = await tasks.get(claimed.id)
    assert recovered.state is TaskState.PAUSED
    # Not "resumable": nobody has to press anything. It goes again by itself,
    # which is the whole reason the two states became one.
    assert recovered.state.waiting
    assert not recovered.state.terminal
    assert "server stopped" in recovered.error


async def test_a_stale_heartbeat_is_released_even_from_another_host(db):
    tasks = TaskStore(db)
    await tasks.create(Task(problem="p", title="a", slug="a"))
    claimed = await tasks.claim("elsewhere:1")
    await db.pool.execute(
        "UPDATE tasks SET claimed_at = now() - interval '10 minutes' WHERE id = $1", claimed.id
    )
    assert await tasks.release_orphans(90, "thishost") == [claimed.id]


async def test_heartbeat_keeps_a_task_claimed(db):
    tasks = TaskStore(db)
    await tasks.create(Task(problem="p", title="a", slug="a"))
    claimed = await tasks.claim("thishost:1")
    await db.pool.execute(
        "UPDATE tasks SET claimed_at = now() - interval '10 minutes' WHERE id = $1", claimed.id
    )
    await tasks.heartbeat(claimed.id)
    # Fresh again, and the pid check does not apply to a live process.
    assert await tasks.release_orphans(90, "otherhost") == []
