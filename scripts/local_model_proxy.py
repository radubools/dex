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
  the server is doing.

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


class Backend:
    """The vLLM process: stopped, starting, awake or asleep."""

    def __init__(self, serve: str, port: int, model: str, sleep_after: float, stop_after: float,
                 start_timeout: float = 900) -> None:
        self.serve, self.port, self.model = os.path.expanduser(serve), port, model
        self.sleep_after, self.stop_after, self.start_timeout = sleep_after, stop_after, start_timeout
        self.url = f"http://127.0.0.1:{port}"
        self.state = "stopped"
        self.proc: asyncio.subprocess.Process | None = None
        self.active = 0
        self.last = time.time()
        self.lock = asyncio.Lock()
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10))

    async def ready(self) -> None:
        """Wake or start the server, whichever it needs. Requests wait here."""
        async with self.lock:
            if self.state == "awake" and self.proc and self.proc.returncode is None:
                return
            if self.proc is None or self.proc.returncode is not None:
                await self._start()
            elif self.state == "asleep":
                r = await self.http.post(f"{self.url}/wake_up")
                r.raise_for_status()
                log.info("woke the server")
            self.state = "awake"

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
            await asyncio.wait_for(proc.wait(), 30)
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


def app_for(backend: Backend) -> Starlette:
    async def models(_: Request) -> Response:
        # Answered here: dex polls it, and asking must not wake or keep the model.
        return JSONResponse({"object": "list", "data": [
            {"id": backend.model, "object": "model", "owned_by": "local", "root": backend.model}]})

    async def status(_: Request) -> Response:
        return JSONResponse({"state": backend.state, "active": backend.active,
                             "idle_s": round(time.time() - backend.last), "sleep_after_s": backend.sleep_after,
                             "stop_after_s": backend.stop_after})

    async def forward(request: Request) -> Response:
        backend.active += 1
        backend.last = time.time()
        try:
            await backend.ready()
        except Exception as e:  # noqa: BLE001 - reported to the caller as a 503
            backend.active -= 1
            return JSONResponse({"error": {"message": f"local model server unavailable: {e}"}}, status_code=503)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        upstream = backend.http.build_request(request.method, f"{backend.url}{request.url.path}",
                                              params=request.query_params, headers=headers,
                                              content=await request.body())
        try:
            r = await backend.http.send(upstream, stream=True)
        except httpx.HTTPError as e:
            backend.active -= 1
            backend.last = time.time()
            return JSONResponse({"error": {"message": f"local model server: {e}"}}, status_code=502)

        async def body():
            try:
                async for chunk in r.aiter_raw():
                    yield chunk
            finally:
                await r.aclose()
                backend.active -= 1
                backend.last = time.time()

        return StreamingResponse(body(), status_code=r.status_code,
                                 headers={k: v for k, v in r.headers.items() if k.lower() not in HOP})

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        watcher = asyncio.create_task(backend.watch())
        try:
            yield
        finally:
            watcher.cancel()
            await backend.stop()

    return Starlette(routes=[
        Route("/v1/models", models, methods=["GET"]),
        Route("/proxy/status", status, methods=["GET"]),
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
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    backend = Backend(a.serve, a.backend_port, a.model, a.sleep_after, a.stop_after)
    uvicorn.run(app_for(backend), host="127.0.0.1", port=a.port, log_level="warning",
                timeout_graceful_shutdown=40)


if __name__ == "__main__":
    main()
