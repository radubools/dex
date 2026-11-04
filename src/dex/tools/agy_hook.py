"""The Antigravity CLI's PreToolUse hook for a dex task (design/27).

`agy` runs this before every tool call of a dex session, with the call as
JSON on stdin. It hands the call to the dex task that started the session,
over the loopback address in `DEX_AGY_GATE`, and prints dex's decision. The
task's permission policy is what decides, exactly as it does for Claude.

Standard library only: it starts once per tool call. Anything that goes wrong
denies, so a run never gets past dex because dex could not be asked.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> None:
    gate = os.environ.get("DEX_AGY_GATE")
    try:
        payload = sys.stdin.read()
        if not gate:
            raise RuntimeError("this session was not started by dex")
        request = urllib.request.Request(
            gate, data=payload.encode(), headers={"content-type": "application/json"}, method="POST")
        # As long as the operator may take to answer a question about it.
        with urllib.request.urlopen(request, timeout=24 * 3600) as response:
            decision = json.loads(response.read() or b"{}")
    except Exception as exc:  # noqa: BLE001 -- every failure is a denial
        decision = {"decision": "deny", "reason": f"dex could not decide on this call: {exc}"}
    sys.stdout.write(json.dumps(decision))


if __name__ == "__main__":
    main()
