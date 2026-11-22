"""A local model server that frees its memory when idle and comes back on request.

dex runs local models (`oc:local/<id>`, ~/.dex/local-models.json) on vllm-metal.
vLLM never unloads by itself, so this proxy sits on the address dex knows
(127.0.0.1:8100) and owns the server, which listens on an internal port:

- a request wakes or starts the server first, then is forwarded (streaming);
- after SLEEP_AFTER idle seconds the server sleeps (vLLM sleep mode, level 1:
  the KV cache is freed, the weights stay; it wakes in under a second);
- after STOP_AFTER idle seconds the server is stopped (everything freed; the
  next request waits for it to load again, about two minutes for a 27B);
- `GET /v1/models` is answered here, without waking anything, because dex asks
  every 15 seconds whether the model is there; `GET /proxy/status` says what
  the server is doing;
- `GET /proxy/power` (also in `/proxy/status` as `machine`) is a snapshot, taken every
  POWER_EVERY seconds (`--power-every`, default 5; 0 reads on request instead), of:
  - power: what comes in from the power adapter, the estimate at the wall, and the battery's
    share, from the battery controller (`ioreg`, no root). macOS refreshes these about once a
    minute, so `fresh_s` says how long ago the reading last changed;
  - dram: average and p95 DRAM bandwidth, read, write and total, and gpu: its busy share,
    clock and the FP16 ceiling at that clock (scripts/mac_metrics.py, IOReport, no root);
  - tokens: the server's prompt and output tokens per second, from llama-server's `/metrics`
    counters (start it with `--metrics`) plus the running replies' tokens in `/slots`, and the FP16 work they take in TFLOPS when the
    model's entry in ~/.dex/local-models.json gives `active_params_b`. llama.cpp dequantizes
    quantized weights to FP16 in its kernels, so its work is FLOPs, not integer TOPS.
  While requests are in flight each snapshot is logged as a `machine:` line.

    python local_model_proxy.py --serve ~/.dex/vllm-qwen27b.sh --model qwen3.8-27b

The serve script starts the server on $PORT (the backend port) and answers
`GET /health` with 200 once ready. For vLLM, start it with `--enable-sleep-mode`
(this sets VLLM_SERVER_DEV_MODE=1, which the sleep endpoints need). A server
with no sleep mode (llama.cpp's llama-server) runs with `--sleep-after 0`: it
is only stopped when idle.

Two servers are set up for dex's Local Qwen3.8 27B, one in use at a time on
port 8100: llama.cpp (pm2 `llama-local`, ~/.dex/llamacpp-qwen27b.sh, in use)
and vllm-metal (pm2 `vllm-local`, ~/.dex/vllm-qwen27b.sh, kept but stopped:
it wedged under dex's agent prompts, vllm-metal issue #479).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import signal
import time

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

log = logging.getLogger("local-model-proxy")

#: Not forwarded either way: they describe one hop, not the message.
HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive"}

_FIELD = re.compile(r'"(SystemPowerIn|AdapterEfficiencyLoss|BatteryPower|SystemVoltageIn|SystemCurrentIn)"=(\d+)')
_RATING = re.compile(r'"AdapterVoltage"=\d+[^{}]*?"Watts"=(\d+)')


def parse_power(ioreg: str) -> dict | None:
    """The power fields of `ioreg -rw0 -c AppleSmartBattery`, in watts, or None off a battery Mac.

    `adapter_w` is what comes in from the power adapter (SystemPowerIn); `wall_w` adds the
    adapter's own loss; `battery_w` is the battery's flow, positive when it powers the Mac and
    negative when it charges (ioreg prints the signed value as unsigned 64-bit)."""
    fields = {k: int(v) for k, v in _FIELD.findall(ioreg)}
    if "SystemPowerIn" not in fields:
        return None
    battery = fields.get("BatteryPower", 0)
    if battery >= 1 << 63:
        battery -= 1 << 64
    rating = _RATING.search(ioreg)
    return {
        "adapter_w": round(fields["SystemPowerIn"] / 1000, 1),
        "wall_w": round((fields["SystemPowerIn"] + fields.get("AdapterEfficiencyLoss", 0)) / 1000, 1),
        "battery_w": round(battery / 1000, 1),
        "volts": round(fields.get("SystemVoltageIn", 0) / 1000, 2),
        "amps": round(fields.get("SystemCurrentIn", 0) / 1000, 2),
        "adapter_rating_w": int(rating.group(1)) if rating else None,
    }


class Power:
    """The Mac's power draw, read when asked, with how long ago the reading last changed."""

    def __init__(self) -> None:
        self.last: dict | None = None
        self.changed = 0.0

    async def read(self) -> dict | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ioreg", "-rw0", "-c", "AppleSmartBattery",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
        except (OSError, TimeoutError):
            return None
        now = parse_power(out.decode("utf8", "replace"))
        if now is None:
            return None
        if now != self.last:
            self.last, self.changed = now, time.time()
        return {**now, "fresh_s": round(time.time() - self.changed)}


_COUNTER = re.compile(r"^llamacpp:(prompt_tokens_total|tokens_predicted_total)\s+([\d.eE+]+)\s*$", re.M)


def parse_counters(metrics: str) -> dict[str, float]:
    """llama-server's Prometheus token counters (`--metrics`): prompt tokens read (counted as
    they are read) and tokens written (counted when the request finishes)."""
    return {k: float(v) for k, v in _COUNTER.findall(metrics)}


def in_progress(slots: list) -> dict[str, float]:
    """What the requests still running have written so far, from llama-server's `/slots`
    (tokens decoded), keyed like the counters. llama-server counts prompt tokens as it reads
    them, but adds a reply's tokens only once the request finishes; finished totals plus this is
    all the output so far, and when a request finishes its count moves from here into the total,
    so the sum neither drops nor counts it twice."""
    decoded = 0.0
    for s in slots if isinstance(slots, list) else []:
        if isinstance(s, dict) and s.get("is_processing"):
            nxt = s.get("next_token")
            nxt = nxt[0] if isinstance(nxt, list) and nxt else nxt
            decoded += float((nxt or {}).get("n_decoded") or 0) if isinstance(nxt, dict) else 0.0
    return {"tokens_predicted_total": decoded}


def model_gflop_per_token(model: str, path: str | None = None) -> float:
    """2 x the model's active parameters (billions), from ~/.dex/local-models.json's
    `active_params_b`: the floating-point work one token takes through the weights. Attention is
    not counted, so long prompts do somewhat more. 0 when the entry gives no size."""
    import json
    path = path or os.environ.get("DEX_LOCAL_MODELS", os.path.expanduser("~/.dex/local-models.json"))
    try:
        entries = json.loads(open(path).read())
    except (OSError, ValueError):
        return 0.0
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and model in (e.get("served"), e.get("id")) and e.get("active_params_b"):
            return 2 * float(e["active_params_b"])
    return 0.0


class Machine:
    """One snapshot every few seconds of what the Mac and the server are doing: power draw, DRAM
    bandwidth, the GPU's busy share and clock (scripts/mac_metrics.py), the server's prompt and
    output tokens per second (its /metrics counters), and the work those tokens take in TFLOPS.

    Bandwidth, GPU and token rates are each over the time since the previous snapshot, so one
    loop takes them all and `/proxy/power` returns the latest one rather than reading again."""

    def __init__(self, backend: "Backend", gflop_per_token: float = 0.0) -> None:
        self.backend, self.gflop_per_token = backend, gflop_per_token
        self.power = Power()
        self.bw = self.gpu = None
        try:
            import sys
            here = os.path.dirname(os.path.abspath(__file__))
            if here not in sys.path:
                sys.path.insert(0, here)
            import mac_metrics
            self.bw, self.gpu = mac_metrics.MemBW(), mac_metrics.GPUState()
        except (ImportError, OSError) as e:
            log.info("no bandwidth or GPU readings: %s", e)
        self.counters: tuple[float, dict[str, float]] | None = None
        self.latest: dict | None = None
        self.clock = time.time

    async def _rates(self) -> dict | None:
        if self.backend.state != "awake":
            self.counters = None
            return None
        try:
            r = await self.backend.http.get(f"{self.backend.url}/metrics", timeout=3)
            done = parse_counters(r.text) if r.status_code == 200 else {}
            running = {}
            if done:
                sr = await self.backend.http.get(f"{self.backend.url}/slots", timeout=3)
                running = in_progress(sr.json()) if sr.status_code == 200 else {}
            now = (self.clock(), {k: v + running.get(k, 0.0) for k, v in done.items()}) if done else None
        except (httpx.HTTPError, ValueError):
            now = None
        if not now or not now[1]:
            return None
        before, self.counters = self.counters, now
        if not before or now[0] <= before[0]:
            return None
        dt = now[0] - before[0]
        rate = {k: max(0.0, (now[1].get(k, 0) - before[1].get(k, 0)) / dt) for k in now[1]}
        in_tps, out_tps = rate.get("prompt_tokens_total", 0.0), rate.get("tokens_predicted_total", 0.0)
        out = {"in_tps": round(in_tps, 1), "out_tps": round(out_tps, 1)}
        if self.gflop_per_token:
            out["tflops"] = round((in_tps + out_tps) * self.gflop_per_token / 1000, 2)
        return out

    async def snapshot(self) -> dict:
        snap: dict = {"at": round(time.time()), "power": await self.power.read()}
        if self.bw:
            snap["dram"] = await asyncio.to_thread(self.bw.read)
        if self.gpu:
            snap["gpu"] = await asyncio.to_thread(self.gpu.read)
        snap["tokens"] = await self._rates()
        self.latest = snap
        return snap

    @staticmethod
    def describe(s: dict, active: int) -> str:
        parts = []
        if p := s.get("power"):
            parts.append(f"{p['adapter_w']:.1f} W from the adapter (~{p['wall_w']:.1f} W at the wall), "
                         f"battery {p['battery_w']:+.1f} W (reading {p['fresh_s']} s old)")
        if (d := s.get("dram")) and "total" in d:
            parts.append(f"DRAM {d['total']['avg_gbs']:.0f} GB/s (read {d.get('read', {}).get('avg_gbs', 0):.0f}, "
                         f"write {d.get('write', {}).get('avg_gbs', 0):.0f}, p95 {d['total']['p95_gbs']:.0f})")
        if g := s.get("gpu"):
            parts.append(f"GPU {g['active'] * 100:.0f}% busy at {g['mhz']} MHz "
                         f"(ceiling {g['busy_ceiling_tflops']:.1f} TFLOPS)")
        if t := s.get("tokens"):
            parts.append(f"{t['in_tps']:.0f} t/s in, {t['out_tps']:.1f} t/s out"
                         + (f", ~{t['tflops']:.2f} TFLOPS of FP16 work" if "tflops" in t else ""))
        return f"machine: {'; '.join(parts)}; {active} in flight"


class Backend:
    """The vLLM process: stopped, starting, awake or asleep."""

    def __init__(self, serve: str, port: int, model: str, sleep_after: float, stop_after: float,
                 start_timeout: float = 900, peers: list[str] | None = None, peer_wait: float = 1800,
                 stop_grace: float = 30) -> None:
        self.stop_grace = stop_grace
        self.peers, self.peer_wait = [u.rstrip("/") for u in peers or []], peer_wait
        self.serve, self.port, self.model = os.path.expanduser(serve), port, model
        self.sleep_after, self.stop_after, self.start_timeout = sleep_after, stop_after, start_timeout
        self.url = f"http://127.0.0.1:{port}"
        self.state = "stopped"
        self.proc: asyncio.subprocess.Process | None = None
        self.active = 0
        #: Requests waiting for the server to start or wake (not yet in flight).
        self.waiting = 0
        self.last = time.time()
        self.lock = asyncio.Lock()
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10))

    async def ready(self) -> None:
        """Wake or start the server, whichever it needs. Requests wait here."""
        async with self.lock:
            if self.state == "awake" and self.proc and self.proc.returncode is None:
                return
            if self.proc is None or self.proc.returncode is not None:
                await self._release_peers()
                await self._start()
            elif self.state == "asleep":
                r = await self.http.post(f"{self.url}/wake_up")
                r.raise_for_status()
                log.info("woke the server")
            self.state = "awake"

    async def _release_peers(self) -> None:
        """Wait until every peer has stopped its server, so both never load at once."""
        for peer in self.peers:
            t0 = time.time()
            while True:
                try:
                    r = (await self.http.post(f"{peer}/proxy/release", timeout=60)).json()
                except (httpx.HTTPError, ValueError):
                    break  # the peer proxy is not running: nothing of it is loaded
                if r.get("stopped"):
                    break
                if time.time() - t0 > self.peer_wait:
                    raise RuntimeError(f"{peer} stayed busy for {self.peer_wait:.0f} s")
                log.info("waiting for %s to finish %s request(s) before loading", peer, r.get("busy"))
                await asyncio.sleep(5)

    async def release(self) -> dict:
        """A peer needs the memory: stop now unless requests are in flight."""
        async with self.lock:
            if self.active:
                return {"stopped": False, "busy": self.active}
            if self.proc is not None:
                log.info("a peer needs the memory: stopping")
                await self.stop()
            return {"stopped": True}

    async def _start(self) -> None:
        self.state = "starting"
        log.info("starting the server: %s", self.serve)
        env = {**os.environ, "PORT": str(self.port), "VLLM_SERVER_DEV_MODE": "1"}
        self.proc = await asyncio.create_subprocess_exec(self.serve, env=env, start_new_session=True)
        t0 = time.time()
        while time.time() - t0 < self.start_timeout:
            if self.proc.returncode is not None:
                self.state = "stopped"
                raise RuntimeError(f"the server exited while starting ({self.proc.returncode})")
            with contextlib.suppress(httpx.HTTPError):
                if (await self.http.get(f"{self.url}/health", timeout=3)).status_code == 200:
                    log.info("server up after %.0f s", time.time() - t0)
                    return
            await asyncio.sleep(2)
        await self.stop()
        raise RuntimeError("the server did not start in time")

    async def sleep(self) -> None:
        async with self.lock:
            if self.state != "awake" or self.active:
                return
            r = await self.http.post(f"{self.url}/sleep", params={"level": 1})
            if r.status_code < 300:
                self.state = "asleep"
                log.info("idle %.0f s: asleep (KV cache freed)", time.time() - self.last)
            else:
                log.warning("sleep refused: %s %s", r.status_code, r.text[:200])

    async def stop(self) -> None:
        proc, self.proc = self.proc, None
        self.state = "stopped"
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGINT)
        try:
            # A server whose stop is slow (a Colab runtime being released) gets its grace.
            await asyncio.wait_for(proc.wait(), self.stop_grace)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        log.info("stopped the server (memory freed)")

    async def watch(self) -> None:
        """Sleep, then stop, the server once nothing has used it for a while."""
        while True:
            await asyncio.sleep(20)
            if self.active or self.proc is None:
                continue
            idle = time.time() - self.last
            if idle >= self.stop_after:
                async with self.lock:
                    if not self.active and time.time() - self.last >= self.stop_after:
                        log.info("idle %.0f s: stopping", idle)
                        await self.stop()
            elif self.sleep_after > 0 and idle >= self.sleep_after and self.state == "awake":
                await self.sleep()


class Done:
    """Ends one request's time in flight, once, whichever way the request ends. A count left
    behind keeps the server loaded for good and makes every peer wait on it (2026-11-21: a
    client that left while Qwen3.6 was starting kept it "busy" and Inkling waiting 30 min)."""

    def __init__(self, backend: Backend) -> None:
        self.backend, self.ended = backend, False

    def __call__(self) -> None:
        if not self.ended:
            self.ended = True
            self.backend.active -= 1
            self.backend.last = time.time()


class Forwarded(StreamingResponse):
    """The server's streamed reply. Ends the request's time in flight even when the client goes
    before the reply starts, when the body generator never runs and so never cleans up."""

    def __init__(self, done: Done, upstream: httpx.Response, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.done, self.upstream = done, upstream

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if not self.done.ended:
                await self.upstream.aclose()
                self.done()


def app_for(backend: Backend, power_every: float = 5, gflop_per_token: float = 0.0) -> Starlette:
    machine = Machine(backend, gflop_per_token)

    async def sample() -> None:
        """A snapshot every `power_every` seconds, logged while requests are in flight."""
        while True:
            await asyncio.sleep(power_every)
            try:
                snap = await machine.snapshot()
            except Exception:  # noqa: BLE001 - a reading must never take the proxy down
                log.exception("machine snapshot failed")
                continue
            if backend.active:
                log.info(Machine.describe(snap, backend.active))

    async def current() -> dict:
        # Without the loop (power_every 0) there is no shared interval to keep, so read now.
        return machine.latest if power_every > 0 and machine.latest else await machine.snapshot()

    async def power_now(_: Request) -> Response:
        return JSONResponse(await current())

    async def models(_: Request) -> Response:
        # Answered here: dex polls it, and asking must not wake or keep the model.
        return JSONResponse({"object": "list", "data": [
            {"id": backend.model, "object": "model", "owned_by": "local", "root": backend.model}]})

    async def status(_: Request) -> Response:
        return JSONResponse({"state": backend.state, "active": backend.active, "waiting": backend.waiting,
                             "peers": backend.peers,
                             "idle_s": round(time.time() - backend.last), "sleep_after_s": backend.sleep_after,
                             "stop_after_s": backend.stop_after, "machine": await current()})

    async def forward(request: Request) -> Response:
        # The body is read before the wait for the server: a start takes a minute or more, and a
        # client that left meanwhile must not be found out only once the request counts as in flight.
        content = await request.body()
        backend.waiting += 1
        backend.last = time.time()
        try:
            await backend.ready()
            # In flight from here: counted before the lock is free for a peer's release.
            backend.active += 1
        except Exception as e:  # noqa: BLE001 - reported to the caller as a 503
            return JSONResponse({"error": {"message": f"local model server unavailable: {e}"}}, status_code=503)
        finally:
            backend.waiting -= 1
        done = Done(backend)
        try:
            headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
            upstream = backend.http.build_request(request.method, f"{backend.url}{request.url.path}",
                                                  params=request.query_params, headers=headers, content=content)
            r = await backend.http.send(upstream, stream=True)
        except httpx.HTTPError as e:
            done()
            return JSONResponse({"error": {"message": f"local model server: {e}"}}, status_code=502)
        except BaseException:
            done()
            raise

        async def body():
            try:
                async for chunk in r.aiter_raw():
                    yield chunk
            finally:
                await r.aclose()
                done()

        return Forwarded(done, r, body(), status_code=r.status_code,
                         headers={k: v for k, v in r.headers.items() if k.lower() not in HOP})

    async def release(_: Request) -> Response:
        return JSONResponse(await backend.release())

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        watcher = asyncio.create_task(backend.watch())
        logger = asyncio.create_task(sample()) if power_every > 0 else None
        try:
            yield
        finally:
            watcher.cancel()
            if logger:
                logger.cancel()
            await backend.stop()

    return Starlette(routes=[
        Route("/v1/models", models, methods=["GET"]),
        Route("/proxy/status", status, methods=["GET"]),
        Route("/proxy/power", power_now, methods=["GET"]),
        Route("/proxy/release", release, methods=["POST"]),
        Route("/{path:path}", forward, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
    ], lifespan=lifespan)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", required=True, help="script that starts vLLM on $PORT with --enable-sleep-mode")
    ap.add_argument("--model", required=True, help="the served model name")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--backend-port", type=int, default=8101)
    ap.add_argument("--sleep-after", type=float, default=600,
                    help="idle seconds before sleeping (default 10 min); 0 never sleeps, for a server with no "
                         "sleep mode such as llama.cpp's")
    ap.add_argument("--stop-after", type=float, default=3600, help="idle seconds before stopping (default 1 h)")
    ap.add_argument("--start-timeout", type=float, default=900,
                    help="seconds a start may take before the request gets a 503 (default 15 min)")
    ap.add_argument("--power-every", type=float, default=5,
                    help="seconds between power-draw log lines while requests are in flight; 0 turns them off")
    ap.add_argument("--stop-grace", type=float, default=30,
                    help="seconds a stopping server gets before it is killed (default 30; more for a remote "
                         "server whose release takes a while)")
    ap.add_argument("--peer", action="append", default=[],
                    help="another proxy (e.g. http://127.0.0.1:8100) whose model must be unloaded before this one "
                         "loads; repeatable")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    backend = Backend(a.serve, a.backend_port, a.model, a.sleep_after, a.stop_after, start_timeout=a.start_timeout,
                      peers=a.peer, stop_grace=a.stop_grace)
    uvicorn.run(app_for(backend, a.power_every, model_gflop_per_token(a.model)), host="127.0.0.1", port=a.port, log_level="warning",
                timeout_graceful_shutdown=max(40, a.stop_grace + 10))


if __name__ == "__main__":
    main()
