from dex.pricing import estimate, price_for


def test_known_models_and_fallbacks():
    assert price_for("claude-opus-5") == (5.0, 25.0)
    assert price_for("claude-sonnet-5") == (2.0, 10.0)
    # Dated snapshots and provider prefixes still resolve.
    assert price_for("anthropic.claude-opus-5") == (5.0, 25.0)
    # Anything unrecognised falls back rather than pricing at zero.
    assert price_for("something-new") == (5.0, 25.0)
    assert price_for(None) == (5.0, 25.0)


def test_estimate_counts_each_token_class():
    cost = estimate("claude-opus-5", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert cost == 5.0

    cost = estimate("claude-opus-5", {"input_tokens": 0, "output_tokens": 1_000_000})
    assert cost == 25.0

    # Cache reads are a tenth of input; writes are 1.25x.
    assert estimate("claude-opus-5", {"cache_read_input_tokens": 1_000_000}) == 0.5
    assert estimate("claude-opus-5", {"cache_creation_input_tokens": 1_000_000}) == 6.25


def test_missing_usage_is_free():
    assert estimate("claude-opus-5", None) == 0.0
    assert estimate("claude-opus-5", {}) == 0.0
