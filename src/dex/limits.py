"""A concurrency gate whose ceiling can change while work is in flight."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable


class DynamicLimiter:
    """Like a semaphore, except the limit is re-read on every acquire.

    Used for planner/chat calls, where the operator can raise or lower the
    ceiling from the UI and expect it to take effect on the next request rather
    than at the next restart. Lowering it never interrupts work already running
    — those finish, and the new ceiling applies to what comes after.
    """

    def __init__(
        self,
        get_limit: Callable[[], Awaitable[int]],
        on_change: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._get_limit = get_limit
        #: Called after each acquire and release, so a scheduler can react to
        #: the change in pressure without waiting for its next poll.
        self._on_change = on_change
        self._active = 0
        self._condition = asyncio.Condition()

    async def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            await self._on_change()
        except Exception:  # a listener must not break the gate
            logging.getLogger("dex.limits").exception("limiter listener failed")

    @property
    def active(self) -> int:
        return self._active

    async def __aenter__(self) -> "DynamicLimiter":
        acquired = False
        async with self._condition:
            while True:
                limit = await self._get_limit()
                if self._active < limit:
                    self._active += 1
                    acquired = True
                    break
                # Re-check on a timeout as well as on release, so a raised
                # ceiling frees waiters even when nothing has finished.
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        if acquired:
            await self._notify()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        async with self._condition:
            self._active -= 1
            self._condition.notify_all()
        await self._notify()
