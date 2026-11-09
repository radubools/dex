"""A task's context size from its session transcript (`dex.transcripts`).

Tasks from before `tasks.context_tokens` existed showed turns and no context
size; the startup backfill gives them their last main-agent call's figure.
"""

from __future__ import annotations

import json

from dex import transcripts
from dex.models import Task, TaskState
from dex.store import TaskStore


def call(input_tokens=0, cache_read=0, cache_write=0, output=0, **record):
    return {
        "type": "assistant",
        "message": {"usage": {
            "input_tokens": input_tokens, "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write, "output_tokens": output,
        }},
        **record,
    }


def write(path, *records, raw=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw + b"".join(json.dumps(r).encode() + b"\n" for r in records))
    return path


def test_the_last_main_agent_call_is_the_context_size(tmp_path):
    path = write(
        tmp_path / "p" / "s.jsonl",
        call(input_tokens=10, cache_read=500),
        {"type": "user", "message": {"content": "a tool result"}},
        call(input_tokens=2, cache_read=99_790, cache_write=710, output=166),
        # A subagent's call has a window of its own.
        call(input_tokens=50_000, isSidechain=True),
        # A synthetic message the CLI records for an API error reports zeros.
        call(),
        raw=b'{"type": "assist',  # a torn line
    )
    assert transcripts.last_context_tokens(path) == 100_668


def test_a_call_before_a_large_tail_is_still_found(tmp_path, monkeypatch):
    monkeypatch.setattr(transcripts, "TAIL_BYTES", 64)
    path = write(tmp_path / "p" / "s.jsonl", call(input_tokens=1001),
                 {"type": "user", "message": {"content": "x" * 500}})
    assert transcripts.last_context_tokens(path) == 1001


def test_no_transcript_or_no_call_is_zero(tmp_path):
    assert transcripts.last_context_tokens(tmp_path / "missing.jsonl") == 0
    assert transcripts.last_context_tokens(write(tmp_path / "p" / "e.jsonl", {"type": "user"})) == 0
    assert transcripts.context_for(["s"], tmp_path / "nowhere") == {}


async def test_the_backfill_fills_only_tasks_without_a_figure(db, tmp_path):
    store = TaskStore(db)
    root = tmp_path / "projects"
    tasks = {}
    for name in ("old", "measured", "running", "no-transcript"):
        task = Task(problem="p", title=name, slug=name)
        await store.create(task)
        await store.set_session(task.id, f"session-{name}")
        write(root / "-some-cwd" / f"session-{name}.jsonl", call(input_tokens=1000))
        tasks[name] = task
    (root / "-some-cwd" / "session-no-transcript.jsonl").unlink()
    await store.set_state(tasks["old"].id, TaskState.SUCCEEDED)
    await store.set_state(tasks["measured"].id, TaskState.SUCCEEDED)
    await store.set_context_tokens(tasks["measured"].id, 42)
    # A running task writes its own on its next call.
    await store.set_state(tasks["running"].id, TaskState.RUNNING)
    await store.set_state(tasks["no-transcript"].id, TaskState.FAILED)

    assert await transcripts.backfill(store, root) == 1

    sizes = {name: (await store.get(t.id)).context_tokens for name, t in tasks.items()}
    assert sizes == {"old": 1000, "measured": 42, "running": None, "no-transcript": None}
    # Once filled, there is nothing left to do.
    assert await transcripts.backfill(store, root) == 0
