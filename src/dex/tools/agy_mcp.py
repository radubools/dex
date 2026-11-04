"""dex's MCP tools for an Antigravity CLI session, over stdio (design/27).

`agy` reads MCP servers from static config, with no per-session address, so
the dex plugin names this relay instead: each JSON-RPC line it reads on stdin
is posted to the task's own MCP endpoint (`DEX_AGY_MCP`, a stateless JSON
server on the loopback address) and the answer written back as one line.

Standard library only, and it holds no state: the endpoint is stateless.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> None:
    url = os.environ.get("DEX_AGY_MCP")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = None
        try:
            if not url:
                raise RuntimeError("this session was not started by dex")
            request = urllib.request.Request(url, data=line.encode(), method="POST", headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            })
            # A tool can wait on other tasks or on the operator for hours.
            with urllib.request.urlopen(request, timeout=24 * 3600) as response:
                body = response.read()
            if body.strip():
                reply = json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code != 202:
                reply = _error(message, f"dex answered {exc.code}")
        except Exception as exc:  # noqa: BLE001 -- reported to the agent
            reply = _error(message, str(exc))
        if reply is not None and "id" in message:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


def _error(message: dict, text: str) -> dict | None:
    if "id" not in message:
        return None
    return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": text}}


if __name__ == "__main__":
    main()
