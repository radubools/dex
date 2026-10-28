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


# ------------------------------------------------------------- whose windows


def test_a_window_named_for_one_model_holds_only_it():
    from dex.usage import counts_for

    # Full Opus time does not stop Fable work: Fable calls draw nothing from
    # Opus's window.
    assert counts_for("seven_day_opus", "claude-fable-5-1") is False
    assert counts_for("seven_day_sonnet", "claude-opus-4-6") is False


def test_the_windows_every_model_draws_on_hold_every_model():
    from dex.usage import counts_for

    # The CLI gates them on all models, Fable included: on this account a
    # Fable call comes back "You've hit your weekly limit" once the shared
    # weekly window is spent, however much of Fable's own is left (27 Oct,
    # fifty-five tasks failed that way).
    assert counts_for("seven_day", "claude-fable-5-1") is True
    assert counts_for("five_hour", "claude-fable-5-1") is True
    assert counts_for("seven_day", "claude-opus-4-6") is True
    # A window dex has never heard of (a session, a new plan's naming) counts:
    # refusing an unknown limit would run work into a rejection.
    assert counts_for("session", "claude-fable-5-1") is True


def test_fables_own_window_holds_fable_alone():
    from dex.usage import counts_for

    assert counts_for("seven_day_overage_included", "claude-fable-5-1") is True
    assert counts_for("seven_day_overage_included", "claude-opus-4-6") is False


def test_the_closest_limit_for_a_fable_run_still_sees_the_shared_weekly():
    # What the CLI actually enforced on 27 Oct: shared weekly spent, Fable's
    # own at 17%. The hold has to come from the shared window, or the queue
    # keeps handing Fable work to be rejected.
    snapshot = {
        "five_hour": reading(0.58),
        "seven_day": reading(1.0, status="rejected"),
        "seven_day_overage_included": reading(0.17),
    }
    window, used = closest_limit(snapshot, model="claude-fable-5-1")
    assert (window, used) == ("seven_day", 1.0)


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


def test_the_closest_limit_for_a_fable_run_counts_every_window_it_draws_on():
    # The CLI's names: seven_day is the weekly limit on all models,
    # seven_day_overage_included the Fable limit.
    snapshot = {
        "five_hour": reading(0.38),
        "seven_day": reading(0.97),
        "seven_day_overage_included": reading(0.09),
    }
    # The shared weekly is the binding one: the CLI rejects a Fable call
    # once it is spent, whatever Fable's own window still has (27 Oct).
    assert closest_limit(snapshot, model="claude-fable-5-1") == ("seven_day", 0.97)
    spent_fable = {**snapshot, "seven_day": reading(0.5),
                   "seven_day_overage_included": reading(0.96)}
    assert closest_limit(spent_fable, model="claude-fable-5-1") == (
        "seven_day_overage_included", 0.96)


def test_other_models_go_by_the_all_models_limits_and_their_own():
    snapshot = {
        "five_hour": reading(0.38),
        "seven_day": reading(0.5),
        "seven_day_overage_included": reading(0.99),
        "seven_day_sonnet": reading(0.98),
        "seven_day_opus": reading(0.6),
    }
    # Neither Fable's limit nor Sonnet's holds Opus work.
    assert closest_limit(snapshot, model="claude-opus-5-5") == ("seven_day_opus", 0.6)
    assert closest_limit(snapshot, model="claude-sonnet-5-5") == ("seven_day_sonnet", 0.98)
    # A model with no window of its own: the all-models limits only.
    assert closest_limit(snapshot, model="claude-haiku-5-5") == ("seven_day", 0.5)
    # No model known: every window counts, as before.
    assert closest_limit(snapshot) == ("seven_day_overage_included", 0.99)


async def test_the_watcher_follows_the_default_model(db):
    bus = EventBus(db)
    await bus.start()
    settings = SettingsStore(db)
    try:
        model = {"now": "claude-fable-5-1"}

        async def current() -> str:
            return model["now"]

        w = UsageWatcher(settings, bus, model=current)
        await settings.set(SettingsStore.LIMIT_SNAPSHOT, {
            "seven_day": reading(0.5),
            "seven_day_overage_included": reading(0.96),
        })
        # Each model is held by its own windows: Fable by its own nearly-spent
        # weekly, which Opus does not draw on at all.
        assert await w.check() is True
        model["now"] = "claude-opus-5-5"
        assert await w.check() is False

        # The windows every model draws on hold every model, Fable included:
        # the CLI rejects a Fable call once the shared weekly is spent, so a
        # warning there holds Fable work the same as Opus's.
        await settings.set(SettingsStore.LIMIT_SNAPSHOT, {
            "seven_day": reading(0.97, status="allowed_warning"),
            "seven_day_overage_included": reading(0.09),
        })
        assert await w.check() is True
    finally:
        await bus.stop()
