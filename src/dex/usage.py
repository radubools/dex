"""Holds generation work before a Claude limit runs out, rather than after.

Hitting a limit mid-run is expensive: the task fails with whatever it had spent
already, and the failure looks like the agent's fault. The CLI reports how much
of each limit window is gone, so dex can stop just short of the edge and start
again by itself once the window rolls over.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from .bus import EventBus
from .models import Event
from .store import SettingsStore

log = logging.getLogger("dex.usage")

#: Where to stop. Not 1.0: a run already in flight keeps spending, so the last
#: few percent are what it needs to finish rather than fail part-way.
PAUSE_AT = 0.95

#: How often to re-read the snapshot. The readings themselves arrive whenever a
#: task is running; this decides when to act on them, and — the part that
#: matters with nothing running — when a window that has reset lets work go
#: again without anyone asking.
CHECK_INTERVAL_S = 300

#: The cheapest model there is. A probe asks nothing and reads one word back;
#: what it is really after is the rate-limit reading the CLI attaches to any
#: call, so the model only has to be able to answer at all.
PROBE_MODEL = "claude-haiku-4-5"

#: A probe is a real API call and costs real money — about three cents, which
#: is why it is not simply run on every tick. Beyond this age a reading is old
#: enough to be worth paying for, but only when the conditions in
#: `_should_probe` also hold.
STALE_AFTER_S = 240


def spent(reading: dict[str, Any]) -> float | None:
    """How much of one window is gone, from whatever the CLI reported.

    `utilization` is optional and some plans never send it — the live account
    this was built against reports only a status. So the status is the fallback
    rather than an extra: `allowed_warning` is the CLI saying "approaching the
    limit", which is the same thing the threshold exists to catch, and it is
    treated as having reached it.
    """
    used = reading.get("utilization")
    if isinstance(used, (int, float)):
        return float(used)
    status = reading.get("status")
    if status == "rejected":
        return 1.0
    if status == "allowed_warning":
        return PAUSE_AT
    if status == "allowed":
        return 0.0
    return None


def closest_limit(snapshot: dict[str, Any], now: float | None = None) -> tuple[str | None, float]:
    """The window nearest its limit, and how much of it is gone.

    A reading is only about the window it was taken in: once `resetsAt` has
    passed that window has rolled over and its utilization says nothing about
    the new one, so it is dropped rather than believed.
    """
    moment = time.time() if now is None else now
    nearest, worst = None, 0.0
    for window, reading in snapshot.items():
        if not isinstance(reading, dict):
            continue
        resets_at = reading.get("resetsAt")
        if isinstance(resets_at, (int, float)) and moment >= resets_at:
            continue
        # A window the CLI has rejected is spent, whatever number came with it.
        used = 1.0 if reading.get("status") == "rejected" else spent(reading)
        if used is None:
            continue
        if used >= worst:
            nearest, worst = window, float(used)
    return nearest, worst


class UsageWatcher:
    """Sets and clears the limit pause from what the CLI last reported."""

    def __init__(
        self,
        settings: SettingsStore,
        bus: EventBus,
        on_change: Any = None,
        pending: Any = None,
    ) -> None:
        self.settings = settings
        self.bus = bus
        #: Returns (running, waiting), so a probe is only paid for when the
        #: answer could change what happens next.
        self.pending = pending
        #: Called after the flag changes, so the queue acts on it now rather
        #: than at its next poll.
        self.on_change = on_change
        self._runtime: asyncio.Task[None] | None = None

    async def start(self) -> None:
        # Once at startup: a server that comes back up inside a spent window
        # should not spend the first five minutes handing out work it cannot
        # finish.
        with contextlib.suppress(Exception):
            await self.check()
        self._runtime = asyncio.create_task(self._loop(), name="dex-usage-watch")

    async def stop(self) -> None:
        if self._runtime:
            self._runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runtime
            self._runtime = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A watcher that dies on one bad read stops protecting anything.
                log.exception("usage check failed")

    async def _should_probe(self, snapshot: dict[str, Any]) -> bool:
        """Whether to spend a few cents finding out where the limits stand.

        Only when the answer could change what happens next, which is a narrow
        case. While tasks run, readings arrive free with every call and a probe
        would be paying for what is already coming. With nothing queued, the
        answer changes nothing — dex would hold or release an empty queue.

        So: there is work waiting, nothing is running to report on its own, and
        the last reading is old enough that it may no longer be true.
        """
        if self.pending is None:
            return False
        try:
            running, waiting = await self.pending()
        except Exception:
            log.exception("could not tell whether a probe is worth it")
            return False
        if running or not waiting:
            return False
        newest = max(
            (r.get("seenAt", 0) for r in snapshot.values() if isinstance(r, dict)),
            default=0,
        )
        return time.time() - newest > STALE_AFTER_S

    async def probe(self) -> dict[str, Any] | None:
        """Ask Claude for one word, and keep the reading that comes back with it.

        There is no way to query usage on its own — no endpoint, no CLI
        command. The rate-limit reading rides along with an ordinary call, so
        the smallest possible call on the cheapest model is the way to get one.
        """
        from claude_agent_sdk import ClaudeAgentOptions, RateLimitEvent, ResultMessage, query

        options = ClaudeAgentOptions(
            model=PROBE_MODEL,
            allowed_tools=[],
            max_turns=1,
            permission_mode="dontAsk",
            setting_sources=[],
            system_prompt="Reply with the single word: ok",
        )
        reading, cost = None, None
        try:
            async for message in query(prompt="ok", options=options):
                if isinstance(message, RateLimitEvent):
                    reading = dict(message.rate_limit_info.raw)
                elif isinstance(message, ResultMessage):
                    cost = message.total_cost_usd
        except asyncio.CancelledError:
            raise
        except Exception:
            # A probe is an optimisation. Failing one leaves the last reading
            # in place, which is exactly where dex would have been anyway.
            log.exception("usage probe failed")
            return None

        if reading is None:
            log.info("usage probe returned no rate-limit reading")
            return None
        await self.settings.record_limit(reading)
        await self.settings.set(
            SettingsStore.LIMIT_PROBE,
            {"at": time.time(), "costUsd": cost, "model": PROBE_MODEL},
        )
        log.info(
            "usage probe: %s at %s%% (cost $%.4f)",
            reading.get("rateLimitType"),
            round((reading.get("utilization") or 0) * 100),
            cost or 0.0,
        )
        return reading

    async def check(self) -> bool:
        """Re-decide whether work is held by the limit. Returns the flag."""
        snapshot = await self.settings.limit_snapshot()
        if await self._should_probe(snapshot):
            if await self.probe() is not None:
                snapshot = await self.settings.limit_snapshot()
        window, used = closest_limit(snapshot)
        wanted = used >= PAUSE_AT

        override = await self.settings.get(SettingsStore.LIMIT_OVERRIDE)
        if isinstance(override, dict):
            until = override.get("until")
            # An override lasts as long as the window it was made in. Beyond
            # that it is a decision about a limit that no longer exists, and
            # holding it would mean one click disables the guard for good.
            live = not isinstance(until, (int, float)) or time.time() < until
            if live:
                wanted = bool(override.get("paused"))
            else:
                await self.settings.set(SettingsStore.LIMIT_OVERRIDE, None)

        was = await self.settings.limit_paused()
        if wanted != was:
            await self.settings.set(SettingsStore.LIMIT_PAUSED, wanted)
            log.info(
                "%s generation work: %s at %.0f%% of its limit",
                "holding" if wanted else "releasing", window or "usage", used * 100,
            )
            self.bus.publish(
                Event(
                    type="settings",
                    data={"settings": {
                        "limit_paused": wanted,
                        "limit_window": window,
                        "limit_utilization": used,
                    }},
                )
            )
            if self.on_change is not None:
                await self.on_change()
        return wanted
