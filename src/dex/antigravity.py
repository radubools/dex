"""Antigravity as a task's agent, on the operator's Google subscription (design/27).

A task whose model is an Antigravity one (`agy:<model>`) runs Google's
Antigravity CLI, `agy`, signed in with the operator's Google account: the work
is paid for by the subscription, as Claude's is, and an API key is never
passed on. Gemini CLI was the first choice and no longer serves personal
accounts.

`agy` runs in print mode with stream-json in and out, one process per run and
one input line per turn. `AgyClient` turns that stream into the Claude Agent
SDK's own messages, so the runner observes and judges an Antigravity run with
the code it already has for Claude: the same events in the task panel, the
same permission policy, the same inbox of messages typed while it works.

**Permissions.** In print mode `agy` cannot ask anybody, so dex decides every
call. Each session runs as `dex-task`, a custom agent whose plugin carries a
PreToolUse hook (`tools/agy_hook.py`) that hands the call to this task over
the loopback interface; the task's own policy answers, given the call as the
Claude tool it amounts to (`run_command` is Bash, `write_to_file` is Write).
`agy`'s own approval is opened for the session only (`--settings`, never
saved) so that the hook is what decides; a hook that cannot reach dex denies.

**dex's tools** (ask the operator, start or wait on other tasks) are served
over HTTP on the loopback interface from the same in-process MCP servers a
Claude run gets, and reach `agy` through a stdio relay (`tools/agy_mcp.py`)
the plugin names, since its MCP config cannot carry a per-session address.

The agent and the plugin are written by dex (`install`). Nothing else in the
operator's `agy` setup changes, and their own sessions never load either.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    ThinkingBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

log = logging.getLogger("dex.antigravity")

#: How a model id says it runs on Antigravity: `agy:gemini-4-argon-high`.
PREFIX = "agy:"

#: The custom agent every dex session runs as, and where dex keeps its plugin.
AGENT = "dex-task"
HOME = Path(os.environ.get("DEX_AGY_HOME", Path.home() / ".dex" / "antigravity"))

#: Variables that would sign `agy` in some other way, billed per call.
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI",
                "GOOGLE_CLOUD_PROJECT", "AGY_ADC_AUTH")

#: The tools a dex session has: files, commands, the web, and MCP (dex's own).
#: Browser automation, subagents, schedules and the rest are left out, being
#: nothing a dex task does and nothing its policy knows how to judge.
TOOLS = ["run_command", "command_status", "send_command_input", "view_file", "list_dir",
         "find_by_name", "grep_search", "write_to_file", "replace_file_content",
         "multi_replace_file_content", "read_url_content", "search_web"]

#: Calls about a command already running, which the policy allowed to start.
FOLLOW_UPS = {"command_status", "send_command_input"}

#: `agy`'s own approval, opened for the session so the hook decides.
SESSION_SETTINGS = {"permissions": {"allow": [
    "command(*)", "write_file(*)", "read_file(*)", "read_url(*)", "mcp(*)"]}}

#: Models to offer before `agy models` has been read, or when it cannot be.
FALLBACK_MODELS = [
    ("gemini-4-argon-high", "Gemini 4 Argon (High)"),
    ("gemini-4-argon-medium", "Gemini 4 Argon (Medium)"),
    ("gemini-3.8-flash-medium", "Gemini 3.8 Flash (Medium)"),
]


def is_agy(model: str | None) -> bool:
    return bool(model) and str(model).startswith(PREFIX)


def cli() -> Path | None:
    """`agy`: DEX_AGY_BIN, else on the PATH, else where its installer puts it."""
    named = os.environ.get("DEX_AGY_BIN")
    if named:
        return Path(named) if Path(named).exists() else None
    found = shutil.which("agy") or next(
        (str(p) for p in (Path.home() / ".local" / "bin" / "agy",) if p.exists()), None)
    return Path(found) if found else None


def gemini_home() -> Path:
    return Path(os.environ.get("GEMINI_CLI_HOME", Path.home())) / ".gemini"


def signed_in() -> bool:
    """Whether `agy` holds a Google sign-in (run `agy`, then Google OAuth)."""
    return (gemini_home() / "oauth_creds.json").exists()


_models_cache: tuple[float, list[tuple[str, str]]] = (0.0, [])
_MODELS_TTL = 600.0


def models() -> list[tuple[str, str]]:
    """(id, label) for every model the signed-in plan offers, from `agy models`."""
    global _models_cache
    cached_at, cached = _models_cache
    if time.time() - cached_at < _MODELS_TTL:
        return cached or FALLBACK_MODELS
    found: list[tuple[str, str]] = []
    binary = cli()
    if binary is not None and signed_in():
        try:
            out = subprocess.run([str(binary), "models"], capture_output=True, text=True,
                                 timeout=30, env=_env()).stdout
            for line in out.splitlines():
                model_id, _, label = line.partition("\t")
                if model_id.strip() and label.strip():
                    found.append((model_id.strip(), label.strip()))
        except (OSError, subprocess.SubprocessError):
            found = []
    _models_cache = (time.time(), found)
    return found or FALLBACK_MODELS


def choices() -> list[dict[str, Any]]:
    """The Antigravity models for the picker, each saying whether it can run here."""
    ready = cli() is not None and signed_in()
    why = None if ready else ("install the Antigravity CLI" if cli() is None
                              else "sign in: run `agy`, then Google OAuth")
    return [{"id": PREFIX + model_id, "label": label, "note": "Google subscription, via Antigravity",
             "provider": "antigravity", "available": ready, **({"unavailable": why} if why else {})}
            for model_id, label in models()]


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in API_KEY_VARS and not k.startswith("DEX_AGY_")}


# ---------------------------------------------------------------- install


def install() -> None:
    """Write the `dex-task` agent and its plugin, where `agy` finds them.

    Rewritten only when they differ, and by rename, so a running session never
    reads a file half written. The scripts are copied in: they are standard
    library only, and the plugin then works whichever checkout of dex wrote it.
    """
    plugin = HOME / "dex-plugin"
    tools = Path(__file__).parent / "tools"
    python = sys.executable
    files = {
        plugin / "plugin.json": json.dumps({"name": "dex"}, indent=2),
        plugin / "agy_hook.py": (tools / "agy_hook.py").read_text(),
        plugin / "agy_mcp.py": (tools / "agy_mcp.py").read_text(),
        plugin / "hooks.json": json.dumps({"dex": {"PreToolUse": [{"matcher": "*", "hooks": [{
            "type": "command", "command": f"{python} -I {plugin / 'agy_hook.py'}",
            # As long as the operator may take to answer.
            "timeout": 24 * 3600}]}]}}, indent=2),
        plugin / "mcp_config.json": json.dumps({"mcpServers": {"dex": {
            "command": python, "args": ["-I", str(plugin / "agy_mcp.py")]}}}, indent=2),
        gemini_home() / "config" / "agents" / f"{AGENT}.md": "\n".join([
            "---",
            f"name: {AGENT}",
            "description: A task started by dex. Every tool call is decided by dex.",
            f"tools: [{', '.join(TOOLS)}]",
            f"plugins: [{plugin}]",
            "---",
            "",
        ]),
    }
    for path, text in files.items():
        if path.exists() and path.read_text() == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f".{path.name}.{os.getpid()}")
        staged.write_text(text)
        staged.replace(path)


# ------------------------------------------------------------- tool shapes


def _path(args: dict[str, Any], *keys: str, cwd: Path) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return str(value if Path(value).is_absolute() else cwd / value)
    return None


def claude_shape(name: str | None, args: dict[str, Any], cwd: Path) -> tuple[str, dict[str, Any]]:
    """An `agy` tool call as (Claude tool name, Claude-shaped input), with the
    model's own one-line summary of it as `description` when it wrote one.

    The hook and the stream name some tools differently (`view_file` against
    `read_file`, `write_to_file` against `write_file`): both are covered.
    """
    args = args if isinstance(args, dict) else {}
    claude, shaped = _shape(name, args, cwd)
    summary = args.get("toolSummary") or args.get("Description")
    if summary and not claude.startswith("mcp__") and isinstance(shaped, dict):
        shaped = {**shaped, "description": str(summary)}
    return claude, shaped


def _shape(name: str | None, args: dict[str, Any], cwd: Path) -> tuple[str, dict[str, Any]]:
    if name == "run_command":
        shaped: dict[str, Any] = {"command": str(args.get("CommandLine") or "")}
        if args.get("Cwd"):
            shaped["cwd"] = _path(args, "Cwd", cwd=cwd)
        return "Bash", shaped
    if name in ("write_to_file", "write_file"):
        return "Write", {"file_path": _path(args, "TargetFile", "Path", "AbsolutePath", cwd=cwd),
                         "content": args.get("CodeContent", args.get("Content", ""))}
    if name == "replace_file_content":
        return "Edit", {"file_path": _path(args, "TargetFile", "Path", cwd=cwd),
                        "old_string": args.get("TargetContent", ""),
                        "new_string": args.get("ReplacementContent", ""),
                        **({"replace_all": True} if args.get("AllowMultiple") else {})}
    if name == "edit_file":
        return "Edit", {"file_path": _path(args, "Path", "TargetFile", cwd=cwd),
                        "old_string": args.get("Target", ""), "new_string": args.get("Replacement", "")}
    if name == "multi_replace_file_content":
        chunks = args.get("ReplacementChunks") or []
        return "MultiEdit", {"file_path": _path(args, "TargetFile", "Path", cwd=cwd), "edits": [
            {"old_string": c.get("TargetContent", ""), "new_string": c.get("ReplacementContent", "")}
            for c in chunks if isinstance(c, dict)]}
    if name in ("view_file", "read_file"):
        return "Read", {"file_path": _path(args, "AbsolutePath", "Path", "TargetFile", cwd=cwd) or ""}
    if name == "list_dir":
        return "Glob", {"pattern": "*", "path": _path(args, "DirectoryPath", "Path", cwd=cwd) or str(cwd)}
    if name == "find_by_name":
        return "Glob", {"pattern": str(args.get("Pattern") or "*"),
                        "path": _path(args, "SearchDirectory", "Path", cwd=cwd) or str(cwd)}
    if name == "grep_search":
        return "Grep", {"pattern": str(args.get("Query") or ""),
                        "path": _path(args, "SearchPath", "Path", cwd=cwd) or str(cwd)}
    if name == "read_url_content":
        return "WebFetch", {"url": str(args.get("Url") or ""), "prompt": ""}
    if name == "search_web":
        return "WebSearch", {"query": str(args.get("query") or args.get("Query") or "")}
    if name == "call_mcp_tool":
        server = str(args.get("ServerName") or "")
        # The plugin's servers are namespaced by it: `dex-plugin_dex`.
        server = server.rsplit("_", 1)[-1] if server.startswith("dex-plugin") else server
        arguments = args.get("Arguments")
        return f"mcp__{server}__{args.get('ToolName')}", arguments if isinstance(arguments, dict) else {}
    if name in ("command_status", "send_command_input"):
        return "BashOutput", dict(args)
    # Something dex has no Claude name for: the policy asks the operator.
    return f"agy:{name or 'tool'}", dict(args)


# ----------------------------------------------------------------- bridge


class Bridge:
    """The task's side of a session: the hook's gate and dex's MCP servers.

    On 127.0.0.1, on a port the OS picks, behind a random path, so no other
    local process can guess its way to a task's tools or its decisions.
    """

    def __init__(self, servers: dict[str, Any], gate: Callable[[dict[str, Any]], Any]) -> None:
        self.servers = {name: s["instance"] for name, s in (servers or {}).items()
                        if isinstance(s, dict) and s.get("type") == "sdk"}
        self.gate = gate
        self.token = secrets.token_urlsafe(18)
        self.port: int | None = None
        self._stack = contextlib.AsyncExitStack()
        self._server: Any = None
        self._serving: asyncio.Task[Any] | None = None

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/{path}"

    async def start(self) -> None:
        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        managers = {}
        for name, server in self.servers.items():
            manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)
            await self._stack.enter_async_context(manager.run())
            managers[name] = manager
        prefix = f"/{self.token}/"

        async def reply(send: Any, status: int, body: bytes) -> None:
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            path = scope.get("path", "")
            if scope["type"] != "http" or not path.startswith(prefix):
                return await reply(send, 404, b"{}")
            name = path[len(prefix):].strip("/")
            if name == "gate" and scope.get("method") == "POST":
                chunks = []
                while True:
                    event = await receive()
                    chunks.append(event.get("body", b""))
                    if not event.get("more_body"):
                        break
                try:
                    decision = await self.gate(json.loads(b"".join(chunks) or b"{}"))
                except Exception as exc:  # noqa: BLE001 -- a failure denies
                    log.exception("the Antigravity gate failed")
                    decision = {"decision": "deny", "reason": f"dex failed to decide: {exc}"}
                return await reply(send, 200, json.dumps(decision).encode())
            if name.startswith("mcp/") and name[4:] in managers:
                return await managers[name[4:]].handle_request(scope, receive, send)
            return await reply(send, 404, b"{}")

        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._serving = asyncio.create_task(self._server.serve(), name="dex-agy-bridge")
        for _ in range(500):
            if self._server.started:
                break
            if self._serving.done():
                self._serving.result()
            await asyncio.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._serving is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(self._serving, 5)
        with contextlib.suppress(Exception):
            await self._stack.aclose()


# ----------------------------------------------------------------- client


class AgyError(RuntimeError):
    pass


class AgyClient:
    """`ClaudeSDKClient`'s interface, over `agy -p` with stream-json.

    Takes the `ClaudeAgentOptions` the Claude run is built with and reads what
    applies: the directory, the model, the appended system prompt, the
    permission callback, dex's MCP servers, the conversation to resume and the
    environment. Yields Claude SDK messages: text as `StreamEvent`s, each tool
    call as an `AssistantMessage` with its `ToolUseBlock` and its outcome as a
    `UserMessage`, and one `ResultMessage` per turn.

    `agy` has no way to stop a turn and keep the process, so an interrupt ends
    the process, and the next turn starts it again on the same conversation.
    """

    def __init__(self, options: Any, *, notice: Callable[[str], None] | None = None) -> None:
        self.options = options
        self.cwd = Path(options.cwd or ".")
        self.model = str(options.model)[len(PREFIX):] if is_agy(options.model) else options.model
        self.decide = options.can_use_tool
        self.notice = notice or (lambda _text: None)
        system = options.system_prompt
        self.system = system.get("append", "") if isinstance(system, dict) else (system or "")
        self.conversation: str | None = options.resume
        self._bridge = Bridge(options.mcp_servers if isinstance(options.mcp_servers, dict) else {}, self._gate)
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[Any] | None = None
        self._stderr: asyncio.Task[Any] | None = None
        self._messages: asyncio.Queue[Any] = asyncio.Queue()
        self._first_prompt = not options.resume
        self._in_turn = False
        self._interrupted = False
        self._turn_started = 0.0
        self._steps = 0
        self._block: tuple[str, int] | None = None
        self._index = 0
        self._open_tools: set[str] = set()
        self._announced = False
        #: How far the conversation's transcript has been read, and the
        #: reasoning steps still to show (step index -> found yet).
        self._transcript_at = 0
        self._thoughts: dict[int, str] = {}
        self._unshown: list[int] = []
        #: Steps the stream has announced, waiting for the gate's whole call.
        self._held: dict[str, tuple[str | None, dict[str, Any]]] = {}
        #: Each call as the gate saw it, by tool id, for the stream to show.
        self._gated: dict[str, tuple[str, dict[str, Any]]] = {}

    # -------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> "AgyClient":
        if cli() is None:
            raise AgyError("The Antigravity CLI is not installed: "
                           "curl -fsSL https://antigravity.google/cli/install.sh | bash")
        if not signed_in():
            raise AgyError("The Antigravity CLI is not signed in: run `agy` and choose Google OAuth")
        install()
        await self._bridge.start()
        await self._spawn()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self._end_process()
        await self._bridge.stop()

    async def _spawn(self) -> None:
        env = _env()
        env.update({k: v for k, v in (self.options.env or {}).items()})
        env["DEX_AGY_GATE"] = self._bridge.url("gate")
        env["DEX_AGY_MCP"] = self._bridge.url("mcp/dex")
        args = [str(cli()), "--input-format", "stream-json", "--output-format", "stream-json",
                "--agent", AGENT, "--settings", json.dumps(SESSION_SETTINGS),
                *(["--model", self.model] if self.model else []),
                *(["--conversation", self.conversation] if self.conversation else []),
                # The prompt arrives on stdin, one line per turn.
                "-p="]
        self._proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(self.cwd), env=env, start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024,
        )
        self._reader = asyncio.create_task(self._read(self._proc), name="dex-agy-read")
        self._stderr = asyncio.create_task(self._read_stderr(self._proc), name="dex-agy-stderr")

    async def _end_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        for task in (self._reader, self._stderr):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._reader = self._stderr = None

    # ---------------------------------------------------- the SDK's interface

    async def query(self, prompt: str) -> None:
        """Start a turn. Its messages, and its result, arrive on the stream."""
        if self._proc is None or self._proc.returncode is not None:
            await self._end_process()
            await self._spawn()
        if self._first_prompt and self.system:
            # `agy` keeps its own system prompt, which is what teaches the model
            # its tools; dex's part goes in front of the first message.
            prompt = f"<dex-instructions>\n{self.system}\n</dex-instructions>\n\n{prompt}"
        self._first_prompt = False
        self._in_turn, self._interrupted = True, False
        self._turn_started, self._steps = time.time(), 0
        line = json.dumps({"event": "user", "message": {"role": "user", "content": prompt}})
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write((line + "\n").encode())
        await self._proc.stdin.drain()

    async def interrupt(self) -> None:
        """Stop the turn under way: the process ends, the conversation stays."""
        if not self._in_turn:
            return
        self._interrupted = True
        await self._end_process()
        self._finish_turn(error="interrupted", usage=None, response="")

    async def receive_messages(self) -> AsyncIterator[Any]:
        while True:
            message = await self._messages.get()
            if isinstance(message, BaseException):
                raise message
            yield message

    async def receive_response(self) -> AsyncIterator[Any]:
        async for message in self.receive_messages():
            yield message
            if isinstance(message, ResultMessage):
                return

    # --------------------------------------------------------------- the gate

    async def _gate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The hook's question: may this call run? The policy answers."""
        call = payload.get("toolCall") or {}
        args = call.get("args") or {}
        if call.get("name") in FOLLOW_UPS:
            # Reading, or typing into, a command the policy already let run.
            return {"decision": "allow", "reason": "dex policy: a command it allowed"}
        name, shaped = claude_shape(call.get("name"), args, self.cwd)
        tool_id = self._tool_id(payload.get("stepIdx"))
        # The stream gives a call's bare arguments only: no content, no summary.
        # The hook is given all of it, just before the call runs, so a step
        # the stream announced is shown from here; one it has not, when it does.
        self._gated[tool_id] = (name, shaped)
        if self._held.pop(tool_id, None) is not None:
            self._close_block()
            self._tool_use(tool_id, None, {})
        context = ToolPermissionContext(signal=None, suggestions=[], tool_use_id=tool_id,
                                        title=str(args.get("toolSummary") or name))
        decision = await self.decide(name, shaped, context)
        if not isinstance(decision, PermissionResultAllow):
            return {"decision": "deny", "reason": getattr(decision, "message", "") or "dex does not allow this"}
        updated = decision.updated_input
        if updated not in (None, shaped):
            if name == "Bash" and updated.get("command") and updated["command"] != shaped.get("command"):
                # Confined, rewritten: what runs is what the policy allowed.
                return {"decision": "allow", "reason": "dex policy",
                        "overwrite": {"CommandLine": updated["command"]}}
            # The summary is only for the panel: the policy may drop it.
            asked = {k: v for k, v in shaped.items() if k != "description"}
            if {k: v for k, v in updated.items() if k in asked} != asked:
                return {"decision": "deny", "reason": "dex would only allow this changed, which it cannot do here"}
        return {"decision": "allow", "reason": "dex policy"}

    # ------------------------------------------------------------ the stream

    def _tool_id(self, step: Any) -> str:
        return f"agy-{self.conversation or 'x'}-{step}"

    def _event(self, event: dict[str, Any]) -> None:
        self._messages.put_nowait(StreamEvent(
            uuid=f"agy-{self.conversation}-{self._turn_started}", session_id=self.conversation or "", event=event))

    def _open_block(self, kind: str) -> None:
        if self._block and self._block[0] == kind:
            return
        self._close_block()
        self._index += 1
        self._block = (kind, self._index)
        self._event({"type": "content_block_start", "index": self._index, "content_block": {"type": kind}})

    def _close_block(self) -> None:
        if self._block is not None:
            self._event({"type": "content_block_stop", "index": self._block[1]})
            self._block = None

    def _tool_use(self, tool_id: str, name: str | None, params: dict[str, Any]) -> None:
        claude, shaped = self._gated.pop(tool_id, None) or claude_shape(name, params, self.cwd)
        self._open_tools.add(tool_id)
        self._messages.put_nowait(AssistantMessage(
            content=[ToolUseBlock(id=tool_id, name=claude, input=shaped)], model=PREFIX + str(self.model)))

    def _tool_result(self, tool_id: str, output: str, *, ok: bool) -> None:
        if tool_id not in self._open_tools:
            return
        self._open_tools.discard(tool_id)
        self._messages.put_nowait(UserMessage(content=[ToolResultBlock(
            tool_use_id=tool_id, content=output, is_error=not ok)]))

    def _finish_turn(self, *, error: str | None, usage: Any, response: str) -> None:
        if not self._in_turn:
            return
        self._in_turn = False
        self._show_reasoning()
        self._unshown.clear()
        self._close_block()
        for tool_id in [*self._open_tools]:
            self._tool_result(tool_id, "", ok=error is None)
        took = int((time.time() - self._turn_started) * 1000)
        self._messages.put_nowait(ResultMessage(
            subtype="error" if error else "success", duration_ms=took, duration_api_ms=took,
            is_error=error is not None, num_turns=self._steps, session_id=self.conversation or "",
            # `agy` reports no dollars; the runner's estimate at the public
            # list price (pricing.SUBSCRIPTION_PRICES) stands instead.
            total_cost_usd=None, usage=_claude_usage(usage),
            result=error if error and error != "interrupted" else response,
        ))

    def _transcript(self) -> Path:
        return (gemini_home() / "antigravity-cli" / "brain" / str(self.conversation)
                / ".system_generated" / "logs" / "transcript.jsonl")

    def _show_reasoning(self) -> None:
        """Each finished step's reasoning, from the conversation's transcript.

        `agy` streams no reasoning, only its token count, but writes a summary
        of it per model step to the transcript it keeps. Read from where the
        last read stopped, and shown as a finished thinking block, which the
        runner puts in the panel's reasoning step as it does for Claude's.
        """
        if not self._unshown or not self.conversation:
            return
        try:
            with self._transcript().open("rb") as transcript:
                transcript.seek(self._transcript_at)
                for line in transcript:
                    if not line.endswith(b"\n"):
                        break  # still being written: read it next time
                    self._transcript_at += len(line)
                    with contextlib.suppress(ValueError, TypeError):
                        entry = json.loads(line)
                        if entry.get("thinking"):
                            self._thoughts[int(entry.get("step_index") or 0)] = str(entry["thinking"]).strip()
        except OSError:
            return
        for index in [i for i in self._unshown if i in self._thoughts]:
            self._unshown.remove(index)
            self._messages.put_nowait(AssistantMessage(
                content=[ThinkingBlock(thinking=self._thoughts.pop(index), signature="")],
                model=PREFIX + str(self.model)))

    def _handle(self, message: dict[str, Any]) -> None:
        kind = message.get("event")
        if kind == "init":
            conversation = message.get("conversation_id") or (message.get("init") or {}).get("conversation_id")
            if conversation:
                self.conversation = conversation
            if not self._announced:
                self._announced = True
                self._messages.put_nowait(SystemMessage(subtype="init", data={
                    "session_id": self.conversation, "model": PREFIX + str(self.model), "provider": "antigravity"}))
            return
        if kind == "step_update":
            self._step(message.get("step_update") or {})
            return
        if kind == "result":
            result = message.get("result") or {}
            if result.get("conversation_id"):
                self.conversation = result["conversation_id"]
            reason = result.get("finished_reason")
            error = None
            if result.get("status") != "SUCCESS" or reason in ("error", "print_timeout"):
                error = str(result.get("error") or reason or "Antigravity reported an error")
            if reason == "interrupted" or self._interrupted:
                error = "interrupted"
            self._finish_turn(error=error, usage=result.get("usage"), response=str(result.get("response") or ""))

    def _step(self, step: dict[str, Any]) -> None:
        kind = step.get("step_type")
        state = step.get("state")
        if kind == "agent_response":
            for key, block in (("thinking_delta", "thinking"), ("text_delta", "text")):
                text = step.get(key)
                if text:
                    self._open_block(block)
                    self._event({"type": "content_block_delta", "index": self._index, "delta": (
                        {"type": "text_delta", "text": text} if block == "text"
                        else {"type": "thinking_delta", "thinking": text})})
            if state == "DONE":
                self._close_block()
                self._steps += 1
                if (step.get("usage") or {}).get("thinking_tokens"):
                    self._unshown.append(int(step.get("step_index") or 0))
                    self._show_reasoning()
                if step.get("usage"):
                    # Each model call's tokens, as Claude reports them per
                    # message: the runner files them by hour and skill
                    # (task_usage), which the cost dashboard reads.
                    self._messages.put_nowait(AssistantMessage(
                        content=[], model=PREFIX + str(self.model), usage=_claude_usage(step["usage"]),
                        message_id=f"agy-{self.conversation}-{step.get('step_index')}"))
            return
        if kind != "tool":
            return
        info = step.get("tool_info") or {}
        name = step.get("tool_name") or info.get("name")
        params = info.get("parameters") or {}
        tool_id = self._tool_id(step.get("step_index"))
        if tool_id not in self._open_tools:
            if tool_id in self._gated or state in ("DONE", "ERROR"):
                # Gated already, or finished with no gate seen: shown now, from
                # what the stream said when it started if it says less now.
                held = self._held.pop(tool_id, None)
                if held is not None:
                    name, params = name or held[0], params or held[1]
                self._close_block()
                self._tool_use(tool_id, name, params)
            elif state == "ACTIVE":
                # Held for the gate, which follows at once with the whole call.
                self._held[tool_id] = (name, params)
        if state in ("DONE", "ERROR"):
            error = info.get("error") or {}
            self._tool_result(tool_id, str(error.get("message") if state == "ERROR" else info.get("output") or ""),
                              ok=state == "DONE")

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            try:
                self._handle(message)
            except Exception:
                log.exception("could not translate an Antigravity event")
        code = await proc.wait()
        if proc is self._proc and not self._interrupted and self._in_turn:
            self._finish_turn(error=f"The Antigravity CLI exited ({code})", usage=None, response="")

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        stderr = self.options.stderr
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            # Its own log lines ("I1103 …") are not news for the task panel.
            # Nor is the input closing, which is how a run ends.
            if (text and stderr is not None and not re.match(r"^[IWE]\d{4} ", text)
                    and "stream input cancelled" not in text):
                with contextlib.suppress(Exception):
                    stderr(text)


def _claude_usage(usage: Any) -> dict[str, Any] | None:
    """`agy`'s token counts under the names dex's pricing reads."""
    if not isinstance(usage, dict):
        return None
    count = lambda key: int(usage.get(key) or 0)  # noqa: E731
    return {
        "input_tokens": count("input_tokens"),
        "output_tokens": count("output_tokens"),
        "cache_read_input_tokens": count("cache_read_tokens"),
        "output_tokens_details": {"thinking_tokens": count("thinking_tokens")},
    }
