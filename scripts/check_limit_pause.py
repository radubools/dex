"""One task against a real CLI that will refuse the call: the limit is spent.

Run from the repo root while a tunnel to a working Postgres is up:

    DEX_PORT=4417 DEX_DATABASE_URL=postgresql://adrianparadovschi@127.0.0.1:55432/dex_test \
        .venv/bin/python scripts/check_limit_pause.py

What it proves, in order: a refused call ends the run PAUSED (not failed),
keeps the session on the row, and flips the usage hold so the rest of the
fleet is not dispatched into the same refusal. The refusal itself is free —
the CLI spends nothing on a call it will not run.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

BASE = f"http://127.0.0.1:{os.environ.get('DEX_PORT', '4417')}"
TOKEN = os.environ.get("DEX_TOKEN", "")


def call(path: str, method: str = "GET", body: dict | None = None) -> dict:
    request = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "content-type": "application/json",
            **({"x-dex-token": TOKEN} if TOKEN else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


async def wait_for(predicate, timeout: float = 120.0, every: float = 1.0):
    for _ in range(int(timeout / every)):
        value = predicate()
        if value:
            return value
        await asyncio.sleep(every)
    raise TimeoutError("the condition never held")


async def main() -> None:
    task = call("/api/tasks", "POST", {
        "problem": "Say ok and stop. Do not use any tools.",
        "title": "limit-pause check",
        "slug": "limit-pause-check",
    })["task"]
    task_id = task["id"]
    print(f"submitted {task_id}")

    state = await wait_for(lambda: (
        (t := call(f"/api/tasks/{task_id}")["task"])["state"] in ("paused", "failed", "succeeded")
        and t
    ))
    print("final state:", state["state"])
    print("activity:", state.get("activity"))
    print("error:", state.get("error"))
    assert state["state"] == "paused", "the refused call should pause, not fail"
    assert "limit" in (state.get("activity") or ""), "the pause should say why"
    assert not state.get("error")

    # The hold is set by the pause's own hook, a moment behind the state
    # event; polled rather than assumed.
    held = await wait_for(lambda: call("/api/settings")["settings"]["limit_paused"] is True)
    print("limit_paused:", held)
    assert held is True, "the fleet should be held after a refusal"
    print("OK: refused call paused the task and held the fleet")


if __name__ == "__main__":
    asyncio.run(main())
