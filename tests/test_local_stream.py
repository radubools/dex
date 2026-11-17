"""A local model's reasoning and text stream into dex as they arrive (the bridge's relay)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from dex import opencode
from dex.antigravity import Bridge


def _sse(*deltas: dict) -> bytes:
    out = b""
    for d in deltas:
        out += b"data: " + json.dumps({"choices": [{"index": 0, "delta": d}]}).encode() + b"\n\n"
    return out + b"data: [DONE]\n\n"


STREAM = _sse({"role": "assistant"}, {"reasoning_content": "Let me "}, {"reasoning_content": "think."},
              {"content": "Writing "}, {"content": "the file."})


async def _upstream() -> tuple[uvicorn.Server, asyncio.Task, int, list]:
    seen: list = []

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        body = b""
        while True:
            ev = await receive()
            body += ev.get("body", b"")
            if not ev.get("more_body"):
                break
        seen.append((scope["path"], json.loads(body or b"{}")))
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        # In uneven pieces, so a line can arrive split across chunks.
        for i in range(0, len(STREAM), 17):
            await send({"type": "http.response.body", "body": STREAM[i:i + 17], "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task, server.servers[0].sockets[0].getsockname()[1], seen


@pytest.mark.asyncio
async def test_the_bridge_relays_a_local_models_stream_and_reads_its_deltas() -> None:
    server, task, port, seen = await _upstream()
    calls: list = []
    bridge = Bridge({}, lambda _: None, relay=(f"http://127.0.0.1:{port}/v1", lambda *a: calls.append(a)))
    await bridge.start()
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(bridge.url("llm/chat/completions"),
                             json={"model": "m", "stream": True, "tools": [{"type": "function"}], "messages": []})
            assert r.content == STREAM  # OpenCode gets the reply unchanged
            assert seen[-1][0] == "/v1/chat/completions" and seen[-1][1]["tools"]
            assert calls == [("start",), ("reasoning", "Let me "), ("reasoning", "think."),
                             ("text", "Writing "), ("text", "the file."), ("end",)]
            # A request without tools (OpenCode's title) is relayed but not shown.
            calls.clear()
            r = await c.post(bridge.url("llm/chat/completions"), json={"model": "m", "stream": True, "messages": []})
            assert r.content == STREAM and calls == []
    finally:
        await bridge.stop()
        server.should_exit = True
        await task


def test_streamed_tokens_become_blocks_and_the_whole_parts_are_not_shown_again(monkeypatch) -> None:
    entry = {"id": "m", "url": "http://127.0.0.1:9/v1", "served": "m"}
    monkeypatch.setattr(opencode, "_local", lambda _id: entry)
    options = SimpleNamespace(cwd=".", model="oc:local/m", effort="medium", can_use_tool=None, system_prompt="",
                              resume=None, mcp_servers={}, env={}, stderr=None)
    client = opencode.OpencodeClient(options)
    assert client._bridge.relay[0] == entry["url"]
    for call in [("start",), ("reasoning", "Let me "), ("reasoning", "think."), ("text", "Done."), ("end",)]:
        client._stream(*call)
    # OpenCode prints the same reply's parts once they are whole.
    client._handle({"type": "reasoning", "part": {"text": "Let me think."}})
    client._handle({"type": "text", "part": {"text": "Done."}})
    events = []
    while not client._messages.empty():
        m = client._messages.get_nowait()
        events.append(getattr(m, "event", None) or type(m).__name__)
    kinds = [(e["type"], (e.get("content_block") or e.get("delta") or {}).get("type")) if isinstance(e, dict) else e
             for e in events]
    assert kinds == [("content_block_start", "thinking"), ("content_block_delta", "thinking_delta"),
                     ("content_block_delta", "thinking_delta"), ("content_block_stop", None),
                     ("content_block_start", "text"), ("content_block_delta", "text_delta"),
                     ("content_block_stop", None)]
    assert client._response == ["Done."]  # the turn's result still has the text
    # Without streaming (a Go model), whole parts are shown as before.
    client._handle({"type": "text", "part": {"text": "Again."}})
    assert client._messages.qsize() == 3
