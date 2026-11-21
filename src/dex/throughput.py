"""How fast a model reads and writes: input and output tokens per second.

Every agent dex runs (Claude, Codex, OpenCode on local, Colab or Go models,
Antigravity) hands the runner the same three things, so one meter times them
all:

- a **boundary**: the model has been given new input and is working on it (the
  turn's prompt, a tool's result, a delivered note);
- a **token**: the model produced something (a text or thinking delta, a tool
  call);
- a **usage**: a model call's tokens, as they are counted.

A model call runs from its boundary to its last token. Its prefill is the
boundary to the first token, its decode the first token to the last, so:

- in t/s  = uncached input tokens (input + cache write) / prefill seconds;
- out t/s = output tokens (thinking included) / decode seconds.

Cache reads are not counted in: the model does not process them again.

Some transports hand over whole parts rather than streaming tokens (OpenCode on
a Go model, Antigravity): a part arrives once the model has written it, so no
token marks where the decode began. A call none of whose blocks streamed (its
first delta and its end apart by more than `STREAM_GAP`) has no in t/s, and its
out t/s is over the whole call, boundary to last token: what the model took to
answer, reading included.

A turn's first call also waits for the agent's own process to start (the CLI,
`opencode run`), which is not the model reading, so its prefill is left out
("cold"). A call with no token seen is untimed and left out altogether, as is a
side with no time to divide by: a figure is only ever tokens over the time they
took.

Usage does not always arrive while its call is current. Claude reports each
message's usage as the message comes, before its tool runs; OpenCode reports a
step's usage after its tools have run and returned. So a boundary closes the
current call, and a usage that names no call it has seen claims the oldest
closed call nobody claimed yet, or else the current one, unless the current
one has already written: then the usage is its, and the closed calls still
waiting never got one (OpenCode compacting its session reports none; the local
model relay starts a new call at each request, which is what closes it). A usage that comes at
its call's end (OpenCode's, Codex's and Antigravity's usage-only messages,
Claude's `message_delta`) also ends the call, if it is the current one: a step
that called no tool is followed by the next with no boundary in between.

The dashboard averages over buckets as total tokens over total seconds (never
an average of rates), which is why rows keep the four sums rather than rates.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: How long the task panel's live figure looks back, in seconds.
WINDOW_S = 60.0

#: A block whose end comes this long after its first delta was streamed; a
#: whole part arrives with its start, delta and end at once.
STREAM_GAP = 0.05


@dataclass(slots=True)
class Call:
    """One model call's timing, and what of it has been recorded so far."""

    start: float
    cold: bool = False
    first: float | None = None
    last: float | None = None
    claimed: bool = False
    #: When its usage first came: the hour it is filed under.
    claimed_at: float | None = None
    streamed: bool = False
    #: The context window after the call, where known (the backfill weighs by it).
    context: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    #: What has been written for this call, so each usage adds only its change.
    recorded: dict[str, float] = field(default_factory=lambda: dict.fromkeys(KEYS, 0))

    @property
    def prefill(self) -> float:
        if self.cold or self.first is None or not self.streamed:
            return 0.0
        return max(0.0, self.first - self.start)

    @property
    def decode(self) -> float:
        if self.first is None or self.last is None:
            return 0.0
        if not self.streamed:
            # The whole answer, reading included; a cold one also waited for
            # the agent's process, which says nothing about the model.
            return 0.0 if self.cold else max(0.0, self.last - self.start)
        return max(0.0, self.last - self.first)

    def totals(self) -> dict[str, float]:
        """What this call amounts to now: tokens only where they have time."""
        prefill, decode = self.prefill, self.decode
        return {
            "in_tokens": self.in_tokens if prefill > 0 else 0,
            "in_seconds": prefill if self.in_tokens else 0.0,
            "out_tokens": self.out_tokens if decode > 0 else 0,
            "out_seconds": decode if self.out_tokens else 0.0,
        }


KEYS = ("in_tokens", "in_seconds", "out_tokens", "out_seconds")


@dataclass(slots=True)
class _Lane:
    """One stream of calls: the main agent, or one subagent."""

    current: Call
    closed: deque[Call] = field(default_factory=deque)


class Meter:
    """Times a run's model calls. Fed by the runner live, or by the backfill
    from stored events; times are seconds since the epoch, given by the caller."""

    def __init__(self) -> None:
        self._lanes: dict[Any, _Lane] = {}
        self._by_id: dict[str, Call] = {}
        #: Every call a usage claimed, in order (the backfill reads them).
        self.calls: list[Call] = []

    def _lane(self, lane: Any, at: float) -> _Lane:
        found = self._lanes.get(lane)
        if found is None:
            found = self._lanes[lane] = _Lane(current=Call(start=at, cold=True))
        return found

    def boundary(self, at: float, lane: Any = None, *, cold: bool = False) -> None:
        """The model was given new input at `at`. `cold`: a turn's start, which
        also waits for the agent's process."""
        state = self._lanes.get(lane)
        if state is None:
            self._lanes[lane] = _Lane(current=Call(start=at, cold=cold))
            return
        call = state.current
        if cold:
            # A new turn: no usage of the last one is still to come, and a
            # call that never got one (it failed) must not take the next's.
            state.closed.clear()
        if call.first is None:
            # Nothing produced yet: the model starts once all of its input is in
            # (the last of several tool results), so the call starts later.
            call.start = at
            call.cold = call.cold or cold
            return
        if not call.claimed and not cold:
            state.closed.append(call)
        state.current = Call(start=at, cold=cold)

    def token(self, at: float, lane: Any = None) -> None:
        """The model produced something at `at`."""
        call = self._lane(lane, at).current
        if call.first is None:
            call.first = at
        call.last = at if call.last is None else max(call.last, at)

    def stream(self, lane: Any = None) -> None:
        """The current call's tokens arrived one by one (a block that streamed)."""
        state = self._lanes.get(lane)
        if state is not None:
            state.current.streamed = True

    def usage(
        self, at: float, in_tokens: int = 0, out_tokens: int = 0,
        message_id: str | None = None, lane: Any = None, *, ends: bool = False,
        context: int = 0,
    ) -> dict[str, float] | None:
        """A call's tokens, added: what to record now, or None for nothing.

        The result holds the change in the four sums for the call this usage
        belongs to, so recording each one keeps a row's totals right however
        many times a call's usage arrives.
        """
        state = self._lane(lane, at)
        call = self._by_id.get(message_id) if message_id else None
        if call is None:
            if state.current.first is not None and not state.current.claimed:
                # The current call has written and nobody claimed it: this is
                # its usage. Closed calls still waiting got none, and never will
                # (OpenCode compacting its session reports no usage).
                call = state.current
                state.closed.clear()
            elif state.closed:
                call = state.closed.popleft()
            else:
                call = state.current
            if message_id:
                self._by_id[message_id] = call
        if not call.claimed:
            call.claimed, call.claimed_at = True, at
            self.calls.append(call)
        call.context = max(call.context, int(context or 0))
        call.in_tokens += max(0, int(in_tokens))
        call.out_tokens += max(0, int(out_tokens))
        now = call.totals()
        change = {k: now[k] - call.recorded[k] for k in KEYS}
        call.recorded = now
        if ends and call is state.current:
            state.current = Call(start=at)
        return change if any(change.values()) else None


def in_tokens(counts: dict[str, Any]) -> int:
    """The input a call processed: what it read uncached, and what it cached."""
    return int(counts.get("input_tokens", 0) or 0) + int(counts.get("cache_write_tokens", 0) or 0)


def rate(tokens: float, seconds: float) -> float | None:
    """Tokens per second, or None with nothing timed."""
    return tokens / seconds if seconds > 0 and tokens > 0 else None
