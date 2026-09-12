"""Holding work before a Claude limit runs out, rather than after.

Hitting a limit mid-run fails the task with whatever it had already spent, and
the failure reads as the agent's fault. The CLI reports how much of each window
is gone while a run is in flight; this is what dex does with that.
"""

from __future__ import annotations

import time

import pytest

from dex.bus import EventBus
from dex.store import SettingsStore
from dex.usage import PAUSE_AT, UsageWatcher, closest_limit


@pytest.fixture
async def watcher(db):
    bus = EventBus(db)
    await bus.start()
    try:
        yield UsageWatcher(SettingsStore(db), bus)
    finally:
        await bus.stop()


def reading(used: float, resets_in: float = 3600, status: str = "allowed") -> dict:
    return {"utilization": used, "status": status, "resetsAt": time.time() + resets_in}


def test_the_nearest_window_is_the_one_that_matters():
    # Five-hour and seven-day limits are spent at different rates; work has to
    # stop for whichever runs out first.
    window, used = closest_limit({"five_hour": reading(0.42), "seven_day": reading(0.97)})
    assert (window, used) == ("seven_day", 0.97)


def test_a_window_that_has_reset_says_nothing_about_the_new_one():
    # Its utilization described a window that no longer exists.
    window, used = closest_limit({"five_hour": reading(0.99, resets_in=-1)})
    assert (window, used) == (None, 0.0)


def test_a_rejected_window_counts_as_spent_whatever_number_came_with_it():
    window, used = closest_limit({"five_hour": reading(0.1, status="rejected")})
    assert (window, used) == ("five_hour", 1.0)


def test_a_reading_with_nothing_usable_in_it_is_ignored():
    # A status dex does not recognise, a reading that is not a reading, and no
    # readings at all. A status-only reading is *not* ignored — see `spent`.
    assert closest_limit({"five_hour": {"status": "something-new"}}) == (None, 0.0)
    assert closest_limit({"five_hour": "nonsense"}) == (None, 0.0)
    assert closest_limit({}) == (None, 0.0)


def test_a_status_only_reading_still_names_its_window():
    # The account this was built against sends no percentage, so dropping
    # status-only readings would mean never knowing anything.
    assert closest_limit({"five_hour": {"status": "allowed"}}) == ("five_hour", 0.0)


async def test_work_is_held_at_the_threshold_and_released_below_it(watcher):
    await watcher.settings.record_limit(
        {"rateLimitType": "five_hour", "utilization": PAUSE_AT, "status": "allowed_warning",
         "resetsAt": time.time() + 3600})
    assert await watcher.check() is True
    assert await watcher.settings.limit_paused() is True

    # The window rolls over, or usage is recalculated lower.
    await watcher.settings.record_limit(
        {"rateLimitType": "five_hour", "utilization": 0.4, "status": "allowed",
         "resetsAt": time.time() + 3600})
    assert await watcher.check() is False
    assert await watcher.settings.limit_paused() is False


async def test_below_the_threshold_nothing_is_held(watcher):
    await watcher.settings.record_limit(
        {"rateLimitType": "five_hour", "utilization": 0.94, "status": "allowed",
         "resetsAt": time.time() + 3600})
    assert await watcher.check() is False


async def test_an_override_survives_the_next_check(watcher):
    """A toggle the watcher undoes five minutes later is not a control."""
    await watcher.settings.record_limit(
        {"rateLimitType": "five_hour", "utilization": 0.99, "status": "allowed_warning",
         "resetsAt": time.time() + 3600})
    await watcher.settings.set(
        SettingsStore.LIMIT_OVERRIDE, {"paused": False, "until": time.time() + 3600})

    assert await watcher.check() is False  # still 99%, but the operator said go


async def test_an_override_lapses_with_the_window_it_was_made_in(watcher):
    """Otherwise one click disables the guard for good."""
    await watcher.settings.record_limit(
        {"rateLimitType": "five_hour", "utilization": 0.99, "status": "allowed_warning",
         "resetsAt": time.time() + 3600})
    await watcher.settings.set(
        SettingsStore.LIMIT_OVERRIDE, {"paused": False, "until": time.time() - 1})

    assert await watcher.check() is True
    assert await watcher.settings.get(SettingsStore.LIMIT_OVERRIDE) is None


async def test_both_flags_hold_work_independently(db):
    """Work runs only when the operator has not paused *and* limit remains."""
    settings = SettingsStore(db)

    assert await settings.work_held() is False

    await settings.set(SettingsStore.PAUSED, True)
    assert await settings.work_held() is True

    await settings.set(SettingsStore.PAUSED, False)
    await settings.set(SettingsStore.LIMIT_PAUSED, True)
    # Releasing the operator's pause must not release dex's own.
    assert await settings.work_held() is True

    await settings.set(SettingsStore.LIMIT_PAUSED, False)
    assert await settings.work_held() is False


async def test_the_queue_stops_claiming_when_the_limit_flag_is_set(config, db):
    """The flag has to reach capacity, or it is a note nobody reads."""
    from dex.queue import TaskManager

    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)
    try:
        assert await manager.task_capacity() > 0

        await manager.settings.set(SettingsStore.LIMIT_PAUSED, True)
        manager._paused_cache = None  # the read is cached for a second
        assert await manager.task_capacity() == 0

        await manager.settings.set(SettingsStore.LIMIT_PAUSED, False)
        manager._paused_cache = None
        assert await manager.task_capacity() > 0
    finally:
        await manager.stop()
        await bus.stop()


@pytest.mark.parametrize(
    "reading, expected",
    [
        # A number, when the plan sends one.
        ({"utilization": 0.42, "status": "allowed"}, 0.42),
        # And when it does not — the account this was built against never does.
        ({"status": "allowed"}, 0.0),
        ({"status": "allowed_warning"}, PAUSE_AT),
        ({"status": "rejected"}, 1.0),
        # A number the CLI did send wins over the status it came with.
        ({"utilization": 0.3, "status": "allowed_warning"}, 0.3),
        # Nothing usable at all.
        ({}, None),
        ({"status": "something-new"}, None),
    ],
)
def test_a_reading_is_read_from_whatever_the_cli_sent(reading, expected):
    from dex.usage import spent

    assert spent(reading) == expected


async def test_a_warning_with_no_percentage_still_holds_work(watcher):
    """The live account reports only a status, so this is the common path."""
    await watcher.settings.record_limit(
        {"rateLimitType": "five_hour", "status": "allowed_warning",
         "resetsAt": time.time() + 3600})

    assert await watcher.check() is True

    await watcher.settings.record_limit(
        {"rateLimitType": "five_hour", "status": "allowed", "resetsAt": time.time() + 3600})
    assert await watcher.check() is False


# ---------------------------------------------------------------- probing

async def test_one_reading_records_every_window_it_carries(watcher):
    """A reading carries `unifiedWindows`; using only one throws the rest away."""
    await watcher.settings.record_limit({
        "status": "allowed_warning", "rateLimitType": "five_hour", "utilization": 0.98,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.98, "resetsAt": time.time() + 600},
            "seven_day": {"utilization": 0.44, "resetsAt": time.time() + 86400},
        },
    })

    snapshot = await watcher.settings.limit_snapshot()
    assert snapshot["five_hour"]["utilization"] == 0.98
    assert snapshot["seven_day"]["utilization"] == 0.44
    # The status belongs to the window it was reported for, not to all of them.
    assert snapshot["five_hour"]["status"] == "allowed_warning"
    assert snapshot["seven_day"]["status"] is None
    assert closest_limit(snapshot) == ("five_hour", 0.98)


async def test_a_reading_without_unified_windows_still_records(watcher):
    await watcher.settings.record_limit(
        {"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.1,
         "resetsAt": time.time() + 600})
    assert (await watcher.settings.limit_snapshot())["five_hour"]["utilization"] == 0.1


@pytest.mark.parametrize(
    "running, waiting, age, expected, why",
    [
        (0, 5, 9999, True, "idle with work waiting and a stale reading"),
        (3, 5, 9999, False, "a running task reports for free"),
        (0, 0, 9999, False, "nothing waiting, so the answer changes nothing"),
        (0, 5, 0, False, "the reading is fresh enough"),
    ],
)
async def test_a_probe_is_only_paid_for_when_it_could_change_something(
    db, running, waiting, age, expected, why
):
    """Each probe is a real call costing real money — about three cents."""
    from dex.bus import EventBus
    from dex.usage import UsageWatcher

    bus = EventBus(db)
    watcher = UsageWatcher(SettingsStore(db), bus, pending=lambda: _counts(running, waiting))
    snapshot = {"five_hour": {"utilization": 0.5, "seenAt": time.time() - age}}

    assert await watcher._should_probe(snapshot) is expected, why


async def _counts(running: int, waiting: int) -> tuple[int, int]:
    return running, waiting


async def test_without_a_way_to_ask_no_probe_is_attempted(db):
    from dex.bus import EventBus
    from dex.usage import UsageWatcher

    watcher = UsageWatcher(SettingsStore(db), EventBus(db))
    assert await watcher._should_probe({}) is False
