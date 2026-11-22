"""The local model proxy counts each request in flight and always ends its count.

A count left behind makes the proxy report "busy" for good: its server never stops and every
peer waits on it (2026-11-21: a client that left while Qwen3.6 was starting held it so).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
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


def test_power_reads_the_adapter_the_wall_and_a_discharging_battery():
    assert proxy.parse_power(SAMPLE) == {"adapter_w": 74.1, "wall_w": 76.4, "battery_w": -1.2, "volts": 19.73,
                                         "amps": 3.76, "adapter_rating_w": 94}


def test_a_charging_battery_is_positive_and_a_mac_without_one_reads_none():
    # seen with pmset saying "charging"
    assert proxy.parse_power('"SystemPowerIn"=20000 "BatteryPower"=9160')["battery_w"] == 9.2
    assert proxy.parse_power("") is None
    assert proxy.battery_text(9.2) == "battery charging 9.2 W"
    assert proxy.battery_text(-1.2) == "battery discharging 1.2 W"
    assert proxy.battery_text(0) == "battery idle"


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
    assert power["power"]["adapter_w"] == 74.1 and power["power"]["fresh_s"] == 0
    assert status["machine"]["power"]["wall_w"] == 76.4


METRICS = """# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total {p}
llamacpp:prompt_seconds_total 12.5
llamacpp:tokens_predicted_total {o}
"""


def test_counters_are_read_from_llama_server_metrics():
    assert proxy.parse_counters(METRICS.format(p=1200, o=34)) == {
        "prompt_tokens_total": 1200.0, "tokens_predicted_total": 34.0}


def test_token_rates_and_tflops_come_from_counter_deltas():
    counts = iter([(1000, 10), (2900, 180)])
    clock = iter([100.0, 105.0])

    def metrics(request):
        if request.url.path == "/slots":
            return httpx.Response(200, json=[])
        p, o = next(counts)
        return httpx.Response(200, text=METRICS.format(p=p, o=o))
    backend = make_backend()
    backend.http = httpx.AsyncClient(transport=httpx.MockTransport(metrics))
    backend.state = "awake"
    machine = proxy.Machine.__new__(proxy.Machine)
    machine.backend, machine.gflop_per_token, machine.counters = backend, 13.4, None
    machine.clock = lambda: next(clock)

    async def run():
        first = await machine._rates()
        second = await machine._rates()
        return first, second
    first, second = asyncio.run(run())
    assert first is None  # one reading: no rate yet
    assert second == {"in_tps": 380.0, "out_tps": 34.0, "tflops": round(414 * 13.4 / 1000, 2)}


def test_a_stopped_server_has_no_token_rates():
    backend = make_backend()
    backend.state = "stopped"
    machine = proxy.Machine.__new__(proxy.Machine)
    machine.backend, machine.gflop_per_token, machine.counters = backend, 13.4, (1.0, {"x": 1})
    assert asyncio.run(machine._rates()) is None and machine.counters is None


def test_work_per_token_is_twice_the_active_weights(tmp_path):
    f = tmp_path / "local-models.json"
    f.write_text('[{"id": "a", "served": "qwen3.8-flash-next", "active_params_b": 6.7}, {"id": "b"}]')
    assert proxy.model_gflop_per_token("qwen3.8-flash-next", str(f)) == 13.4
    assert proxy.model_gflop_per_token("b", str(f)) == 0.0
    assert proxy.model_gflop_per_token("missing", str(tmp_path / "none.json")) == 0.0


def test_a_snapshot_reads_as_one_log_line():
    line = proxy.Machine.describe({
        "power": {"adapter_w": 74.1, "wall_w": 76.4, "battery_w": -1.2, "fresh_s": 12},
        "dram": {"read": {"avg_gbs": 109.1}, "write": {"avg_gbs": 26.5}, "total": {"avg_gbs": 127.3, "p95_gbs": 256.0}},
        "gpu": {"active": 0.995, "mhz": 1182, "busy_ceiling_tflops": 12.0},
        "tokens": {"in_tps": 0.0, "out_tps": 43.2, "tflops": 0.58}}, 2)
    assert line == ("machine: 74.1 W from the adapter (~76.4 W at the wall), battery discharging 1.2 W (reading 12 s old); "
                    "DRAM 127 GB/s (read 109, write 26, p95 256); GPU 100% busy at 1182 MHz (ceiling 12.0 TFLOPS); "
                    "0 t/s in, 43.2 t/s out, ~0.58 TFLOPS of FP16 work; 2 in flight")


SNAP1 = {"at": 1, "power": {"adapter_w": 74.1}}
SNAP2 = {"at": 2, "power": {"adapter_w": 80.0}}


def sse(obj) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


def events(raw: bytes) -> list:
    return [json.loads(line[5:]) for line in raw.split(b"\n")
            if line.startswith(b"data:") and not line.strip().endswith(b"[DONE]")]


def test_a_stream_carries_the_snapshot_first_when_it_changes_and_at_its_end():
    latest = {"v": SNAP1}
    tagger = proxy.Tagger(lambda: latest["v"], stream=True)
    first = sse({"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]})
    # a chunk boundary inside the second event's line: kept until the line ends
    second = sse({"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]})
    out = tagger.feed(first + second[:20]) + tagger.feed(second[20:])
    latest["v"] = SNAP2
    out += tagger.feed(sse({"choices": [{"delta": {"content": "!"}, "finish_reason": None}]}))
    out += tagger.feed(sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n")
    out += tagger.close()
    got = events(out)
    assert [e["choices"][0]["delta"].get("content") for e in got] == ["Hel", "lo", "!", None]
    assert [e.get("dex_machine", {}).get("at") for e in got] == [1, None, 2, 2]
    assert out.endswith(b"data: [DONE]\n\n")


def test_a_stream_with_no_snapshot_yet_passes_unchanged():
    tagger = proxy.Tagger(lambda: None, stream=True)
    raw = sse({"choices": [{"delta": {"content": "x"}}]}) + b": keep-alive\n\n"
    assert tagger.feed(raw) + tagger.close() == raw


def test_a_json_reply_carries_the_snapshot_at_its_top_level():
    tagger = proxy.Tagger(lambda: SNAP1, stream=False)
    raw = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()
    assert tagger.feed(raw[:7]) + tagger.feed(raw[7:]) == b""
    out = json.loads(tagger.close())
    assert out["choices"][0]["message"]["content"] == "hi" and out["dex_machine"] == SNAP1


def test_a_forwarded_stream_is_tagged_and_other_paths_are_not():
    def upstream(chunks: int = 3, delay: float = 0.0):
        async def chat(request):
            await request.body()

            async def gen():
                for i in range(chunks):
                    yield sse({"choices": [{"delta": {"content": str(i)},
                                            "finish_reason": "stop" if i == chunks - 1 else None}]})
                yield b"data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        return Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                                 Route("/v1/completions", chat, methods=["POST"])])
    backend = make_backend(upstream())
    app = proxy.app_for(backend, power_every=0)
    app.state.machine.latest = SNAP1  # what the snapshot loop would hold

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://p") as c:
            return ((await c.post("/v1/chat/completions", json={"stream": True})).content,
                    (await c.post("/v1/completions", json={"stream": True})).content)
    chat, other = asyncio.run(run())
    got = events(chat)
    assert [e["choices"][0]["delta"]["content"] for e in got] == ["0", "1", "2"]
    assert [e.get("dex_machine", {}).get("at") for e in got] == [1, None, 1]  # first and closing chunk
    assert chat.endswith(b"data: [DONE]\n\n")
    assert all("dex_machine" not in e for e in events(other))  # only chat completions
    assert backend.active == 0


mspec = importlib.util.spec_from_file_location("mac_metrics", SCRIPT.parent / "mac_metrics.py")
mac = importlib.util.module_from_spec(mspec)
mspec.loader.exec_module(mac)


def test_bandwidth_is_the_time_weighted_band_middle():
    # half the time in 0-32 GB/s, half in 96-128: means 16 and 112, so 64; p95 in the top band
    assert mac.bandwidth([("32GB/s", 50), ("64GB/s", 0), ("96GB/s", 0), ("128GB/s", 50)]) == {
        "avg_gbs": 64.0, "p95_gbs": 128.0}
    assert mac.bandwidth([("32GB/s", 0)]) is None


def test_gpu_busy_share_clock_and_ceiling():
    clocks = [0, 338, 618, 796, 924, 952, 1056, 1062, 1182]
    g = mac.gpu_state([("OFF", 100), ("P1", 0), ("P8", 300), ("P6", 600)], clocks, 40)
    assert g["active"] == 0.9 and g["mhz"] == round((1182 * 300 + 1056 * 600) / 900)
    assert g["ceiling_tflops"] == round(40 * 128 * 2 * g["mhz"] * 1e6 / 1e12, 1)


def test_running_replies_count_before_they_finish():
    slots = [{"id": 0, "is_processing": True, "n_prompt_tokens_processed": 5000,
              "next_token": [{"n_decoded": 0}]},
             {"id": 1, "is_processing": True, "n_prompt_tokens_processed": 300, "next_token": [{"n_decoded": 40}]},
             {"id": 2, "is_processing": False}]
    # prompt tokens are already in llama-server's counter as they are read: not added again
    assert proxy.in_progress(slots) == {"tokens_predicted_total": 40.0}


def test_a_request_finishing_moves_its_count_without_a_jump():
    """5 s apart: the prompt counter goes 12050 -> 14050 as a prefill is read; a reply at 90
    tokens finishes at 100 (the output counter takes its 100 only then)."""
    reads = iter([
        (dict(p=12050, o=500), [{"is_processing": True, "n_prompt_tokens_processed": 2000},
                                {"is_processing": True, "n_prompt_tokens_processed": 50, "next_token": [{"n_decoded": 90}]}]),
        (dict(p=14050, o=600), [{"is_processing": True, "n_prompt_tokens_processed": 4000}]),
    ])
    current = {}

    def server(request):
        if request.url.path == "/metrics":
            current["c"], current["s"] = next(reads)
            return httpx.Response(200, text=METRICS.format(**current["c"]))
        return httpx.Response(200, json=current["s"])
    clock = iter([0.0, 5.0])
    backend = make_backend()
    backend.http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    backend.state = "awake"
    machine = proxy.Machine.__new__(proxy.Machine)
    machine.backend, machine.gflop_per_token, machine.counters = backend, 0.0, None
    machine.clock = lambda: next(clock)

    async def run():
        await machine._rates()
        return await machine._rates()
    # prompt: 14050 - 12050 = 2000 in 5 s; output: 600 - (500 + 90) = 10 in 5 s
    assert asyncio.run(run()) == {"in_tps": 400.0, "out_tps": 2.0}


def test_the_backend_waits_on_its_ready_path_which_defaults_to_health() -> None:
    # Splash answers /health at once and /ready only once its model is loaded.
    assert proxy.Backend("true", 0, "m", sleep_after=0, stop_after=3600).ready_path == "/health"
    assert proxy.Backend("true", 0, "m", sleep_after=0, stop_after=3600, ready_path="ready").ready_path == "/ready"
