"""List-price token costs, used only for the running estimate.

The authoritative figure is `total_cost_usd` from the agent's own result
message; this table exists so a task shows a plausible number *while* it runs
rather than nothing until it finishes. Anything derived from it is labelled an
estimate.

Prices are US dollars per million tokens.
"""

from __future__ import annotations

from typing import Any

#: model id -> (input, output)
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

DEFAULT_PRICE = (5.0, 25.0)

#: Cache reads bill at a tenth of input; cache writes at 1.25x.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


def price_for(model: str | None) -> tuple[float, float]:
    if not model:
        return DEFAULT_PRICE
    if model in PRICES:
        return PRICES[model]
    # Tolerate dated snapshots and provider prefixes: match the longest key
    # that the id contains.
    matches = [key for key in PRICES if key in model]
    return PRICES[max(matches, key=len)] if matches else DEFAULT_PRICE


def estimate(model: str | None, usage: dict[str, float] | None) -> float:
    """Dollars for one message's usage, at list price."""
    if not usage:
        return 0.0
    input_price, output_price = price_for(model)
    plain = float(usage.get("input_tokens") or 0)
    cached_read = float(usage.get("cache_read_input_tokens") or 0)
    cached_write = float(usage.get("cache_creation_input_tokens") or 0)
    output = float(usage.get("output_tokens") or 0)

    return (
        plain * input_price
        + cached_read * input_price * CACHE_READ_MULTIPLIER
        + cached_write * input_price * CACHE_WRITE_MULTIPLIER
        + output * output_price
    ) / 1_000_000


#: The token counts worth keeping, in the order they are reported.
TOKEN_FIELDS = (
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "thinking_tokens",
)


def tokens(usage: dict[str, Any] | None) -> dict[str, int]:
    """The counts from one usage payload, flattened to plain ints.

    `thinking_tokens` lives under `output_tokens_details` and is a **subset** of
    `output_tokens`, not an addition to it — the API bills thinking as output.
    Only the agent's final result carries the detail; a per-message usage
    generally does not, so a missing count reads as zero rather than an error.
    """
    if not usage:
        return dict.fromkeys(TOKEN_FIELDS, 0)
    details = usage.get("output_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}

    def count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    output = count(usage.get("output_tokens"))
    # A thinking count above the output it belongs to would be a report we do
    # not understand; clamp rather than let a subset exceed its whole.
    thinking = min(output, count(details.get("thinking_tokens")))
    return {
        "input_tokens": count(usage.get("input_tokens")),
        "cache_read_tokens": count(usage.get("cache_read_input_tokens")),
        "cache_write_tokens": count(usage.get("cache_creation_input_tokens")),
        "output_tokens": output,
        "thinking_tokens": thinking,
    }
