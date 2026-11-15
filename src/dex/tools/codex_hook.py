"""The Codex CLI's PreToolUse hook for a dex task (design/29).

Codex runs this before every tool call of a dex session (shell commands,
`apply_patch`, MCP tools), with the call as JSON on stdin. It hands the call
to the dex task that started the session, over the loopback address in
`DEX_CODEX_GATE`, and prints dex's decision in Codex's hook format. The
task's permission policy is what decides, exactly as it does for Claude.

dex installs a copy at `~/.dex/codex/hook.py` and names it, with the system
Python, in the session's flags: Codex trusts a hook by the hash of how it is
named, so the name never changes and is trusted once (`codex.install`).

Standard library only, and Python 3.9: it starts once per tool call, on
`/usr/bin/python3`. Anything that goes wrong denies, so a run never gets past
dex because dex could not be asked.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import urllib.request


def _answer(decision: dict) -> dict:
    """dex's `{"decision", "reason", "input"}` as Codex's PreToolUse output.

    A plain allow is no output at all: Codex counts an "allow" with nothing to
    change as a failed hook run (and lets the call through anyway)."""
    if decision.get("decision") != "allow":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": str(decision.get("reason") or "dex does not allow this")}}
    if isinstance(decision.get("input"), dict):
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": decision["input"]}}
    return {}


def main() -> None:
    gate = os.environ.get("DEX_CODEX_GATE")
    if not gate:
        # Not a dex session: this hook is named only in the flags dex starts
        # Codex with, so this should not happen; it stays out of the way.
        sys.stdout.write("{}")
        return
    try:
        payload = sys.stdin.read()
        request = urllib.request.Request(
            gate, data=payload.encode(), headers={"content-type": "application/json"}, method="POST")
        # As long as the operator may take to answer a question about it.
        with urllib.request.urlopen(request, timeout=24 * 3600) as response:
            decision = json.loads(response.read() or b"{}")
    except Exception as exc:  # noqa: BLE001 -- every failure is a denial
        if isinstance(getattr(exc, "reason", exc), ConnectionRefusedError):
            # Nothing listens for this task any more (the dex server restarted
            # under it, and resumes the task in a session of its own): the
            # Codex run that asked, and this hook, end here.
            os.killpg(os.getpgid(0), signal.SIGTERM)  # Codex runs in a session of its own
        decision = {"decision": "deny", "reason": f"dex could not decide on this call: {exc}"}
    sys.stdout.write(json.dumps(_answer(decision)))


if __name__ == "__main__":
    main()
