"""A provider's quota refusal (a 429) holds the fleet the way Claude's limit does.

On 15 Nov Antigravity's Google quota ran out and 134 tasks failed in two
minutes: dex read "RESOURCE_EXHAUSTED (code 429): Individual quota reached" as
the work failing, so the queue kept dispatching into the same refusal.
"""

from __future__ import annotations

import time

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from dex.bus import EventBus
from dex.models import TaskState
from dex.store import SettingsStore
from dex.usage import UsageWatcher, closest_limit, counts_for, is_usage_limit_error, resets_in

from test_limit_pause import package_task, run_with

QUOTA = ("Individual quota reached. Please upgrade your subscription to increase "
         "your limits. Resets in 2h5m39s.")
AGY_ERROR = ('AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): ' + QUOTA
             + '","status":"RESOURCE_EXHAUSTED","error_code":429}')
GEMINI = "agy:gemini-4-carbon-medium"


class QuotaClient:
    """An Antigravity run whose provider refuses it for a spent quota."""

    def __init__(self, options, notice=None):
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        return None

    async def receive_response(self):
        yield SystemMessage(subtype="init", data={"session_id": "agy-conv"})
        yield ResultMessage(subtype="result", duration_ms=600, duration_api_ms=600,
                            is_error=True, num_turns=3, session_id="agy-conv", result=QUOTA)

    receive_messages = receive_response


def test_a_quota_refusal_is_a_limit():
    assert is_usage_limit_error(QUOTA)
    assert is_usage_limit_error(AGY_ERROR)
    assert not is_usage_limit_error("The Antigravity CLI exited (3)")


def test_the_reset_is_read_from_the_message():
    assert resets_in(QUOTA) == 2 * 3600 + 5 * 60 + 39
    assert resets_in("Resets in 45m") == 45 * 60
    assert resets_in("Resets in 1d2h") == 26 * 3600
    assert resets_in("no reset named") is None


def test_a_providers_window_holds_its_own_models_only():
    assert counts_for("antigravity", GEMINI)
    assert not counts_for("antigravity", "claude-opus-5-5")
    assert not counts_for("antigravity", "oc:kimi")
    assert not counts_for("seven_day", GEMINI)          # no Claude window holds it
    assert counts_for("opencode", "oc:kimi")
    assert counts_for("antigravity", None)               # no model known: all count


async def test_a_quota_refusal_pauses_and_holds_until_the_named_reset(config, db, monkeypatch):
    monkeypatch.setattr("dex.runner.antigravity.AgyClient", QuotaClient)
    task, _, recheck = await run_with(config, db, package_task(model=GEMINI), QuotaClient, monkeypatch)

    assert task.state is TaskState.PAUSED and task.error is None
    assert "Antigravity refuses work" in (task.activity or "")
    assert recheck == [True]
    snapshot = await SettingsStore(db).limit_snapshot()
    held = snapshot["antigravity"]
    assert held["status"] == "rejected"
    assert abs(held["resetsAt"] - (time.time() + resets_in(QUOTA))) < 60
    # Claude's windows are left alone: Claude work is not what was refused.
    assert "seven_day" not in snapshot or snapshot["seven_day"].get("status") != "rejected"


@pytest.mark.parametrize("default, held", [(GEMINI, True), ("claude-opus-5-5", False), ("oc:qwen3.8-flash", False)])
async def test_the_fleet_is_held_while_the_default_model_is_refused(db, default, held):
    bus = EventBus(db)
    await bus.start()
    settings = SettingsStore(db)
    try:
        async def current() -> str:
            return default

        await settings.set(SettingsStore.LIMIT_SNAPSHOT, {
            "antigravity": {"status": "rejected", "resetsAt": time.time() + 600, "seenAt": time.time()},
        })
        assert await UsageWatcher(settings, bus, model=current).check() is held
    finally:
        await bus.stop()


def test_the_hold_lifts_once_the_reset_has_passed():
    snapshot = {"antigravity": {"status": "rejected", "resetsAt": time.time() - 1}}
    assert closest_limit(snapshot, model=GEMINI) == (None, 0.0)
