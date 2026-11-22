"""The local model proxy counts each request in flight and always ends its count.

A count left behind makes the proxy report "busy" for good: its server never stops and every
peer waits on it (2026-11-21: a client that left while Qwen3.6 was starting held it so).
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_model_proxy.py"
spec = importlib.util.spec_from_file_location("local_model_proxy", SCRIPT)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


def upstream_app(chunks: int = 3, delay: float = 0.0) -> Starlette:
    async def chat(request):
        await request.body()

        async def gen():
            for i in range(chunks):
                await asyncio.sleep(delay)
                yield f"data: {i}\n\n".encode()
        return StreamingResponse(gen(), media_type="text/event-stream")

    async def health(_):
        return JSONResponse({"status": "ok"})

    return Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                             Route("/health", health)])


def make_backend(upstream: Starlette | None = None, ready_delay: float = 0.0) -> proxy.Backend:
    b = proxy.Backend("true", 0, "m", sleep_after=0, stop_after=3600)
    b.url = "http://upstream"
    if upstream is not None:
        b.http = httpx.AsyncClient(transport=httpx.ASGITransport(upstream), timeout=10)

    async def ready():
        await asyncio.sleep(ready_delay)
        b.state = "awake"
    b.ready = ready
    return b


def scope() -> dict:
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
            "query_string": b"", "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8100)}


async def call(app, receive, send) -> None:
    """One request as uvicorn would run it; an error is what the server would log, the count is
    what matters here."""
    try:
        await app(scope(), receive, send)
    except Exception:  # noqa: BLE001
        pass


def test_a_normal_request_ends_its_count():
    backend = make_backend(upstream_app())
    app = proxy.app_for(backend)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://p") as c:
            r = await c.post("/v1/chat/completions", json={"x": 1})
            assert r.status_code == 200 and b"data: 2" in r.content
    asyncio.run(run())
    assert backend.active == 0 and backend.waiting == 0


def test_a_client_gone_before_its_body_is_read_is_never_counted():
    """The 2026-11-21 case: the client left while the server was starting."""
    backend = make_backend(upstream_app(), ready_delay=0.05)
    app = proxy.app_for(backend)
    sent: list[dict] = []

    async def receive():
        await asyncio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(call(app, receive, send))
    assert backend.active == 0 and backend.waiting == 0


def test_a_client_gone_while_the_reply_streams_ends_its_count():
    backend = make_backend(upstream_app(chunks=50, delay=0.01))
    app = proxy.app_for(backend)
    got = {"body": False}

    async def receive():
        if not got["body"]:
            got["body"] = True
            return {"type": "http.request", "body": b"{}", "more_body": False}
        await asyncio.sleep(0.05)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("client went away")

    asyncio.run(call(app, receive, send))
    assert backend.active == 0


def test_a_client_gone_before_the_reply_starts_ends_its_count():
    backend = make_backend(upstream_app(chunks=5, delay=0.01))
    app = proxy.app_for(backend)
    got = {"body": False}

    async def receive():
        if not got["body"]:
            got["body"] = True
            return {"type": "http.request", "body": b"{}", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        raise OSError("client went away")  # even the response's start cannot be sent

    asyncio.run(call(app, receive, send))
    assert backend.active == 0


def test_an_unreachable_server_ends_its_count():
    backend = make_backend()

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)
    backend.http = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    app = proxy.app_for(backend)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://p") as c:
            r = await c.post("/v1/chat/completions", json={})
            assert r.status_code == 502
    asyncio.run(run())
    assert backend.active == 0


def test_a_peer_may_release_once_the_client_is_gone():
    backend = make_backend(upstream_app(), ready_delay=0.05)
    app = proxy.app_for(backend)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        pass

    async def run():
        await call(app, receive, send)
        return await backend.release()
    assert asyncio.run(run()) == {"stopped": True}


SAMPLE = ('"AdapterDetails"={"UsbHvcMenu"=({"Index"=0}),"AdapterVoltage"=20000,"UsbHvcHvcIndex"=3,"Watts"=94} "SystemPowerIn"=74149 "AdapterEfficiencyLoss"=2207 '
          '"BatteryPower"=18446744073709550398 "SystemVoltageIn"=19734 "SystemCurrentIn"=3757')


def test_power_reads_the_adapter_the_wall_and_a_charging_battery():
    assert proxy.parse_power(SAMPLE) == {"adapter_w": 74.1, "wall_w": 76.4, "battery_w": -1.2, "volts": 19.73,
                                         "amps": 3.76, "adapter_rating_w": 94}


def test_power_on_battery_is_positive_and_a_mac_without_one_reads_none():
    assert proxy.parse_power('"SystemPowerIn"=20000 "BatteryPower"=20000')["battery_w"] == 20.0
    assert proxy.parse_power("") is None


def test_power_is_served_and_says_how_old_it_is(monkeypatch):
    class Proc:
        async def communicate(self):
            return SAMPLE.encode(), b""

    async def fake_exec(*_a, **_k):
        return Proc()
    monkeypatch.setattr(proxy.asyncio, "create_subprocess_exec", fake_exec)
    app = proxy.app_for(make_backend(upstream_app()), power_every=0)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://p") as c:
            power = (await c.get("/proxy/power")).json()
            status = (await c.get("/proxy/status")).json()
        return power, status
    power, status = asyncio.run(run())
    assert power["adapter_w"] == 74.1 and power["fresh_s"] == 0
    assert status["power"]["wall_w"] == 76.4
