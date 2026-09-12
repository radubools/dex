"""Credential detection across env vars and a CLI subscription login."""

from __future__ import annotations

import json

import pytest

from dex import runner


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    for var in runner.AUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(runner, "_auth_cache", (0.0, None))


@pytest.mark.parametrize("var", runner.AUTH_ENV_VARS)
def test_each_env_var_is_recognised(monkeypatch, var):
    monkeypatch.setenv(var, "value")
    assert runner.credential_source() == var
    assert runner.has_credentials()


def test_env_var_wins_without_spawning_the_cli(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def explode(*_args, **_kwargs):
        raise AssertionError("the CLI should not be consulted when a key is set")

    monkeypatch.setattr(runner.subprocess, "run", explode)
    assert runner.credential_source() == "ANTHROPIC_API_KEY"


def test_subscription_login_counts_as_credentials(monkeypatch):
    monkeypatch.setattr(runner, "bundled_cli", lambda: "/fake/claude")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": json.dumps({"loggedIn": True, "authMethod": "claudeai"})})(),
    )
    assert runner.credential_source() == "claude auth login (claudeai)"
    assert runner.has_credentials()


def test_logged_out_cli_reports_no_credentials(monkeypatch):
    monkeypatch.setattr(runner, "bundled_cli", lambda: "/fake/claude")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": json.dumps({"loggedIn": False, "authMethod": "none"})})(),
    )
    assert runner.credential_source() is None


def test_a_broken_cli_does_not_crash_the_check(monkeypatch):
    monkeypatch.setattr(runner, "bundled_cli", lambda: "/fake/claude")

    def boom(*_a, **_k):
        raise OSError("cannot spawn")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    assert runner.credential_source() is None


def test_result_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "bundled_cli", lambda: "/fake/claude")

    def counted(*_a, **_k):
        calls.append(1)
        return type("R", (), {"stdout": json.dumps({"loggedIn": True, "authMethod": "claudeai"})})()

    monkeypatch.setattr(runner.subprocess, "run", counted)
    runner.credential_source()
    runner.credential_source()
    assert len(calls) == 1


def test_reported_source_follows_the_cli_precedence_order(monkeypatch):
    # The CLI prefers ANTHROPIC_AUTH_TOKEN over ANTHROPIC_API_KEY over
    # CLAUDE_CODE_OAUTH_TOKEN, so dex must name the one that will actually win.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    monkeypatch.setattr(runner, "_auth_cache", (0.0, None))
    assert runner.credential_source() == "CLAUDE_CODE_OAUTH_TOKEN"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    assert runner.credential_source() == "ANTHROPIC_API_KEY"

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer")
    assert runner.credential_source() == "ANTHROPIC_AUTH_TOKEN"
