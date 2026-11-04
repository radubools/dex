"""OpenCode as a task's agent, on the operator's OpenCode Go subscription (design/28).

A task whose model is an OpenCode one (`oc:<model>`) runs the OpenCode CLI,
signed in to the operator's OpenCode account, on a model of the **Go** plan
(provider `opencode-go`) only: the work is paid for by the subscription, as
Claude's and Google's are. OpenCode's pay-as-you-go models (Zen, `opencode/…`)
and every provider reached with an API key are never offered, and API keys
are stripped from its environment.

Each turn is one `opencode run --format json` on a private server
(`--standalone`), resuming the task's session with `--session`.
`OpencodeClient` turns its events into the Claude Agent SDK's own messages, as
`antigravity.AgyClient` does for `agy`, so the runner drives it with the code it
has for Claude.

**Permissions.** dex decides every call. The session runs as `dex-task`, a
primary agent in dex's own OpenCode config directory (never the operator's),
whose plugin (`tools/opencode_gate.js`) hands each call to this task before it
runs; the task's policy answers, given the call as the Claude tool it amounts
to (`shell` is Bash, `edit` is Edit). OpenCode's own approval stays open so
the gate is what decides; a gate that cannot reach dex denies.

**dex's tools** are served over HTTP on the loopback interface from the same
in-process MCP servers a Claude run gets (`antigravity.Bridge`), and named to
OpenCode as a remote MCP server in the session's inline config.

**Usage.** Each model call's tokens arrive as it finishes, except the last
one's, which `opencode run` exits before printing; the session's export has it.
OpenCode prices a Go call at nothing, so dex prices it at the plan's own list
rates for that model (`rates`): that is what the plan's limits are counted in.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
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

from .antigravity import Bridge

log = logging.getLogger("dex.opencode")

#: How a model id says it runs on OpenCode Go: `oc:qwen3.8-flash`.
PREFIX = "oc:"

#: The plan's provider: the only one dex runs.
PROVIDER = "opencode-go"

#: Listed first, in this order; the rest follow by name.
PREFERRED = ("qwen3.8-flash",)

#: The agent every dex session runs as, and where dex keeps its config.
AGENT = "dex-task"
HOME = Path(os.environ.get("DEX_OC_HOME", Path.home() / ".dex" / "opencode"))

#: What signs a provider in per call. A Go model never needs one.
API_KEY_PATTERN = re.compile(r"(_API_KEY|_AUTH_TOKEN|^HF_TOKEN)$")

#: OpenCode's tools that are nothing a dex task does (sessions, worktrees,
#: subagents, its browser) and that the policy has no way to judge: refused at
#: the gate with no question to the operator.
REFUSED = re.compile(r"^(question|subagent|skill|browser.*|opencode_.*)$")

#: OpenCode's code tool: a sandbox from which the model calls MCP tools
#: (`tools.dex.ask_user(...)`). Each call it makes comes to the gate itself,
#: so it runs unasked and the panel shows those calls, not it.
CODE_TOOL = "execute"

#: Effort as OpenCode names its variants, least to most.
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

#: Offered before the plan's list has been read, or when it cannot be.
FALLBACK_MODELS = [
    {"id": "qwen3.8-flash", "name": "Qwen3.8 Flash", "variants": ["none", "low", "medium", "xhigh"],
     "cost": [{"input": 0.15, "output": 0.47, "cache": {"read": 0.016, "write": 0.2}}]},
]


def is_oc(model: str | None) -> bool:
    return bool(model) and str(model).startswith(PREFIX)


def cli() -> Path | None:
    """`opencode`: DEX_OC_BIN, else on the PATH, else where its installers put it."""
    named = os.environ.get("DEX_OC_BIN")
    if named:
        return Path(named) if Path(named).exists() else None
    found = shutil.which("opencode") or next(
        (str(p) for p in (Path("/opt/homebrew/bin/opencode"), Path.home() / ".opencode" / "bin" / "opencode")
         if p.exists()), None)
    return Path(found) if found else None


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if not API_KEY_PATTERN.search(k) and not k.startswith(("DEX_OC_", "OPENCODE_"))}


# ------------------------------------------------------------------ models

_models_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_MODELS_TTL = 600.0


def _read_models() -> list[dict[str, Any]]:
    binary = cli()
    if binary is None:
        return []
    try:
        out = subprocess.run([str(binary), "api", "--standalone", "GET", "/api/model"], capture_output=True,
                             text=True, timeout=60, env={**_env(), "PWD": str(Path.home())}, cwd=str(Path.home())).stdout
        data = json.loads(out).get("data") or []
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return []
    return [m for m in data if isinstance(m, dict) and m.get("providerID") == PROVIDER
            and m.get("status", "active") == "active" and (m.get("capabilities") or {}).get("tools", True)]


def models() -> list[dict[str, Any]]:
    """The Go plan's models, the preferred ones first: `id`, `name`,
    `variants` (effort ids) and `cost` (list rates, per million tokens)."""
    global _models_cache
    cached_at, cached = _models_cache
    if time.time() - cached_at < _MODELS_TTL:
        return cached or FALLBACK_MODELS
    found = [{"id": m["id"], "name": m.get("name") or m["id"],
              "variants": [v.get("id") for v in m.get("variants") or [] if isinstance(v, dict)],
              "cost": m.get("cost") or []} for m in _read_models() if m.get("id")]
    rank = {model_id: i for i, model_id in enumerate(PREFERRED)}
    found.sort(key=lambda m: (rank.get(m["id"], len(rank)), m["name"].lower()))
    _models_cache = (time.time(), found)
    return found or FALLBACK_MODELS


def signed_in() -> bool:
    """Whether OpenCode can reach the Go plan: it lists the plan's models
    only when signed in to an account that has it (`opencode auth login`)."""
    models()
    return bool(_models_cache[1])


def choices() -> list[dict[str, Any]]:
    """The Go models for the picker, each saying whether it can run here."""
    listed = models()
    ready = cli() is not None and signed_in()
    why = None if ready else ("install OpenCode" if cli() is None
                              else "sign in: run `opencode auth login` with an OpenCode Go account")
    return [{"id": PREFIX + m["id"], "label": m["name"], "note": "OpenCode Go subscription",
             "provider": "opencode", "available": ready, **({"unavailable": why} if why else {})}
            for m in listed]


def _model(model_id: str) -> dict[str, Any] | None:
    bare = model_id[len(PREFIX):] if is_oc(model_id) else model_id
    return next((m for m in models() if m["id"] == bare), None)


def rates(model_id: str, prompt_tokens: int = 0) -> tuple[float, float, float, float] | None:
    """(input, output, cache read, cache write) in dollars per million tokens,
    at the plan's list price for a prompt this long: a model priced in
    tiers charges a long prompt at the tier it reaches. None when the plan
    gives the model no price."""
    model = _model(model_id) or {}
    tiers = [t for t in model.get("cost") or [] if isinstance(t, dict)]
    if not tiers:
        return None
    chosen = next((t for t in tiers if not t.get("tier")), tiers[0] if tiers else {})
    for tier in tiers:
        size = (tier.get("tier") or {}).get("size")
        if size and prompt_tokens > int(size):
            chosen = tier
    cache = chosen.get("cache") or {}
    number = lambda value: float(value or 0)  # noqa: E731
    return (number(chosen.get("input")), number(chosen.get("output")),
            number(cache.get("read")), number(cache.get("write")))


def variant(model_id: str, effort: str | None) -> str | None:
    """The model's variant for dex's effort: the same one, else the nearest
    below it, else the least above it. None when it has no variants."""
    offered = [v for v in (_model(model_id) or {}).get("variants") or [] if v in EFFORTS]
    if not offered or not effort or effort not in EFFORTS:
        return None
    if effort in offered:
        return effort
    level = EFFORTS.index(effort)
    below = [v for v in offered if EFFORTS.index(v) < level]
    return max(below, key=EFFORTS.index) if below else min(offered, key=EFFORTS.index)


# ---------------------------------------------------------------- install


def install() -> None:
    """Write dex's OpenCode config directory: the `dex-task` agent and its gate.

    Rewritten only when they differ, and by rename, so a running session never
    reads a file half written. The operator's own config is never read by a dex
    session, nor this one by theirs.
    """
    files = {
        HOME / "modes" / f"{AGENT}.md": "\n".join([
            "---",
            "description: A task started by dex. Every tool call is decided by dex.",
            "mode: primary",
            "permission:",
            # dex's gate decides each call; OpenCode's own approval stays open,
            # since nobody could answer it.
            '  "*": allow',
            "  read: allow",
            "  external_directory: allow",
            # Asking goes through dex's ask_user; the rest are not task work.
            "  question: deny",
            "  subagent: deny",
            "  skill: deny",
            "  browser: deny",
            "---",
            "",
        ]),
        HOME / "plugins" / "dex-gate.js": (Path(__file__).parent / "tools" / "opencode_gate.js").read_text(),
    }
    changed = False
    for path, text in files.items():
        if path.exists() and path.read_text() == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f".{path.name}.{os.getpid()}")
        staged.write_text(text)
        staged.replace(path)
        changed = True
    if changed:
        _warm()


def _warm() -> None:
    """Load the config once after it changes. OpenCode's first start after a
    plugin file changes can hang before its server answers; a run that
    follows a load does not."""
    binary = cli()
    if binary is None:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run([str(binary), "debug", "agents", "--standalone"], capture_output=True, timeout=30,
                       env={**_env(), "OPENCODE_CONFIG_DIR": str(HOME), "PWD": str(Path.home())}, cwd=str(Path.home()))


# ------------------------------------------------------------- tool shapes


def _path(args: dict[str, Any], *keys: str, cwd: Path) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return str(value if Path(value).is_absolute() else cwd / value)
    return None


def claude_shape(name: str | None, args: Any, cwd: Path, servers: tuple[str, ...] = ("dex",)
                 ) -> tuple[str, dict[str, Any]]:
    """An OpenCode tool call as (Claude tool name, Claude-shaped input).

    An MCP tool is `<server>_<tool>` to OpenCode, so the servers dex serves
    are what tell one from a built-in tool.
    """
    args = args if isinstance(args, dict) else {}
    if name == "shell":
        shaped: dict[str, Any] = {"command": str(args.get("command") or "")}
        if args.get("workdir"):
            shaped["cwd"] = _path(args, "workdir", cwd=cwd)
        if args.get("description"):
            shaped["description"] = str(args["description"])
        return "Bash", shaped
    if name == "write":
        return "Write", {"file_path": _path(args, "path", "filePath", cwd=cwd), "content": args.get("content", "")}
    if name == "edit":
        return "Edit", {"file_path": _path(args, "path", "filePath", cwd=cwd),
                        "old_string": args.get("oldString", ""), "new_string": args.get("newString", ""),
                        **({"replace_all": True} if args.get("replaceAll") else {})}
    if name == "read":
        return "Read", {"file_path": _path(args, "path", "filePath", cwd=cwd) or ""}
    if name == "glob":
        return "Glob", {"pattern": str(args.get("pattern") or "*"), "path": _path(args, "path", cwd=cwd) or str(cwd)}
    if name == "grep":
        return "Grep", {"pattern": str(args.get("pattern") or ""), "path": _path(args, "path", cwd=cwd) or str(cwd),
                        **({"glob": args["include"]} if args.get("include") else {})}
    if name == "webfetch":
        return "WebFetch", {"url": str(args.get("url") or ""), "prompt": ""}
    if name == "websearch":
        return "WebSearch", {"query": str(args.get("query") or "")}
    for server in servers:
        if name and name.startswith(server + "_"):
            return f"mcp__{server}__{name[len(server) + 1:]}", dict(args)
    # Something dex has no Claude name for: the policy asks the operator.
    return f"oc:{name or 'tool'}", dict(args)


def _claude_usage(tokens: Any) -> dict[str, Any] | None:
    """OpenCode's token counts under the names dex's pricing reads."""
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    count = lambda value: int(value or 0)  # noqa: E731
    return {
        "input_tokens": count(tokens.get("input")),
        "output_tokens": count(tokens.get("output")) + count(tokens.get("reasoning")),
        "cache_read_input_tokens": count(cache.get("read")),
        "cache_creation_input_tokens": count(cache.get("write")),
        "output_tokens_details": {"thinking_tokens": count(tokens.get("reasoning"))},
    }


def _add_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, dict):
            _add_usage(total.setdefault(key, {}), value)
        else:
            total[key] = total.get(key, 0) + value


# ----------------------------------------------------------------- client


class OpencodeError(RuntimeError):
    pass


class OpencodeClient:
    """`ClaudeSDKClient`'s interface, over `opencode run --format json`.

    Takes the `ClaudeAgentOptions` the Claude run is built with and reads what
    applies: the directory, the model and effort, the appended system prompt,
    the permission callback, dex's MCP servers, the session to resume and the
    environment. Yields Claude SDK messages: text as `StreamEvent`s, reasoning
    as a `ThinkingBlock`, each tool call as an `AssistantMessage` with its
    `ToolUseBlock` and its outcome as a `UserMessage`, each model call's tokens
    as an `AssistantMessage` with usage, and one `ResultMessage` per turn.

    A turn is one process, so an interrupt ends it and the session stays.
    """

    def __init__(self, options: Any, *, notice: Callable[[str], None] | None = None) -> None:
        self.options = options
        self.cwd = Path(options.cwd or ".")
        self.model = str(options.model)[len(PREFIX):] if is_oc(options.model) else str(options.model)
        self.effort = getattr(options, "effort", None)
        self.decide = options.can_use_tool
        self.notice = notice or (lambda _text: None)
        system = options.system_prompt
        self.system = system.get("append", "") if isinstance(system, dict) else (system or "")
        self.session: str | None = options.resume
        servers = options.mcp_servers if isinstance(options.mcp_servers, dict) else {}
        self._bridge = Bridge(servers, self._gate)
        self._servers = tuple(sorted(self._bridge.servers, key=len, reverse=True))
        self._proc: asyncio.subprocess.Process | None = None
        self._runner: asyncio.Task[Any] | None = None
        self._messages: asyncio.Queue[Any] = asyncio.Queue()
        self._first_prompt = not options.resume
        self._in_turn = False
        self._interrupted = False
        self._turn_started = 0.0
        self._steps = 0
        self._index = 0
        self._error: str | None = None
        self._response: list[str] = []
        self._turn_usage: dict[str, Any] = {}
        #: Model calls whose tokens have been counted, by message id.
        self._counted: set[str] = set()
        #: Calls shown when the gate saw them, waiting for their outcome; every
        #: call shown; and how many calls each code tool's run has made.
        self._open_tools: set[str] = set()
        self._shown: set[str] = set()
        self._calls: dict[str, int] = {}
        self._announced = False

    @property
    def model_ref(self) -> str:
        chosen = variant(self.model, self.effort)
        return f"{PROVIDER}/{self.model}" + (f"#{chosen}" if chosen else "")

    # -------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> "OpencodeClient":
        if cli() is None:
            raise OpencodeError("OpenCode is not installed: brew install opencode")
        await asyncio.to_thread(install)
        await self._bridge.start()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self._end_process()
        await self._bridge.stop()

    def _config(self) -> str:
        """The session's inline config: dex's MCP servers, at this run's address."""
        return json.dumps({"mcp": {"servers": {
            name: {"type": "remote", "url": self._bridge.url(f"mcp/{name}"), "oauth": False}
            for name in self._bridge.servers}}})

    def _args(self, prompt: str) -> list[str]:
        return [str(cli()), "run", "--standalone", "--format", "json", "--thinking", "--agent", AGENT,
                "-m", self.model_ref, *(["--session", self.session] if self.session else []),
                # The prompt is the message, never a flag, whatever it starts with.
                "--", prompt]

    async def _end_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGINT)
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        runner, self._runner = self._runner, None
        if runner is not None and runner is not asyncio.current_task():
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await runner

    # ---------------------------------------------------- the SDK's interface

    async def query(self, prompt: str) -> None:
        """Start a turn. Its messages, and its result, arrive on the stream."""
        await self._end_process()
        if self._first_prompt and self.system:
            # OpenCode keeps its own system prompt, which is what teaches the
            # model its tools; dex's part goes in front of the first message.
            prompt = f"<dex-instructions>\n{self.system}\n</dex-instructions>\n\n{prompt}"
        self._first_prompt = False
        self._in_turn, self._interrupted = True, False
        self._turn_started, self._steps = time.time(), 0
        self._error, self._response, self._turn_usage = None, [], {}
        env = _env()
        env.update({k: v for k, v in (self.options.env or {}).items() if not API_KEY_PATTERN.search(k)})
        env.update({"OPENCODE_CONFIG_DIR": str(HOME), "OPENCODE_CONFIG_CONTENT": self._config(),
                    "DEX_OC_GATE": self._bridge.url("gate"),
                    # OpenCode takes its directory from PWD, not the process's.
                    "PWD": str(self.cwd)})
        self._proc = await asyncio.create_subprocess_exec(
            *self._args(prompt), cwd=str(self.cwd), env=env, start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024,
        )
        self._runner = asyncio.create_task(self._run(self._proc), name="dex-opencode-run")

    async def interrupt(self) -> None:
        """Stop the turn under way: the process ends, the session stays."""
        if not self._in_turn:
            return
        self._interrupted = True
        await self._end_process()
        self._finish_turn(error="interrupted")

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
        """The plugin's question: may this call run? The policy answers.

        The stream shows a call only once it has finished, so the call is
        shown from here, as it starts.
        """
        tool = str(payload.get("tool") or "")
        call = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        tool_id = str(payload.get("id") or f"oc-{time.time()}")
        if tool == CODE_TOOL:
            return {"decision": "allow", "reason": "dex policy: each call it makes is asked about"}
        if tool_id in self._open_tools or tool_id in self._shown:
            # A call from inside the code tool carries that tool's id.
            self._calls[tool_id] = self._calls.get(tool_id, 0) + 1
            tool_id = f"{tool_id}.{self._calls[tool_id]}"
        if REFUSED.match(tool):
            return {"decision": "deny", "reason": f"dex tasks do not use {tool}"}
        if tool == "patch":
            # One call for many files, which the policy judges one at a time.
            return {"decision": "deny", "reason": "use the edit or write tool instead of patch"}
        name, shaped = claude_shape(tool, call, self.cwd, self._servers)
        self._tool_use(tool_id, name, shaped)
        context = ToolPermissionContext(signal=None, suggestions=[], tool_use_id=tool_id, title=name)
        decision = await self.decide(name, shaped, context)
        if not isinstance(decision, PermissionResultAllow):
            return {"decision": "deny", "reason": getattr(decision, "message", "") or "dex does not allow this"}
        updated = decision.updated_input
        if updated not in (None, shaped):
            if name == "Bash" and updated.get("command") and updated["command"] != shaped.get("command"):
                # Confined, rewritten: what runs is what the policy allowed.
                return {"decision": "allow", "reason": "dex policy", "input": {**call, "command": updated["command"]}}
            if {k: v for k, v in updated.items() if k in shaped} != shaped:
                return {"decision": "deny", "reason": "dex would only allow this changed, which it cannot do here"}
        return {"decision": "allow", "reason": "dex policy"}

    # ------------------------------------------------------------ the stream

    def _event(self, event: dict[str, Any]) -> None:
        self._messages.put_nowait(StreamEvent(
            uuid=f"oc-{self.session}-{self._turn_started}", session_id=self.session or "", event=event))

    def _text(self, text: str) -> None:
        """A finished text part, as one block: OpenCode's run prints whole parts."""
        self._index += 1
        self._event({"type": "content_block_start", "index": self._index, "content_block": {"type": "text"}})
        self._event({"type": "content_block_delta", "index": self._index, "delta": {"type": "text_delta", "text": text}})
        self._event({"type": "content_block_stop", "index": self._index})
        self._response.append(text)

    def _tool_use(self, tool_id: str, name: str, shaped: dict[str, Any]) -> None:
        if tool_id in self._open_tools:
            return
        self._open_tools.add(tool_id)
        self._shown.add(tool_id)
        self._messages.put_nowait(AssistantMessage(
            content=[ToolUseBlock(id=tool_id, name=name, input=shaped)], model=PREFIX + self.model))

    def _tool_result(self, tool_id: str, output: str, *, ok: bool) -> None:
        if tool_id not in self._open_tools:
            return
        self._open_tools.discard(tool_id)
        self._messages.put_nowait(UserMessage(content=[ToolResultBlock(
            tool_use_id=tool_id, content=output, is_error=not ok)]))

    def _usage(self, message_id: str, tokens: Any) -> None:
        """One model call's tokens, as Claude reports them per message: the
        runner files them by hour and skill (task_usage) and prices them."""
        usage = _claude_usage(tokens)
        if not message_id or message_id in self._counted or not usage:
            return
        self._counted.add(message_id)
        self._steps += 1
        _add_usage(self._turn_usage, usage)
        self._messages.put_nowait(AssistantMessage(
            content=[], model=PREFIX + self.model, usage=usage, message_id=f"oc-{message_id}"))

    def _handle(self, event: dict[str, Any]) -> None:
        if event.get("sessionID") and not self.session:
            self.session = str(event["sessionID"])
        if not self._announced and self.session:
            self._announced = True
            self._messages.put_nowait(SystemMessage(subtype="init", data={
                "session_id": self.session, "model": PREFIX + self.model, "provider": "opencode"}))
        kind = event.get("type")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "text" and part.get("text"):
            self._text(str(part["text"]))
        elif kind == "reasoning" and str(part.get("text") or "").strip():
            self._messages.put_nowait(AssistantMessage(
                content=[ThinkingBlock(thinking=str(part["text"]).strip(), signature="")], model=PREFIX + self.model))
        elif kind == "tool_use":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool_id = str(part.get("id") or part.get("callID") or "")
            ok = state.get("status") == "completed"
            output = str(state.get("output") if ok else state.get("error") or "") or str(state.get("output") or "")
            if part.get("tool") == CODE_TOOL:
                # Its calls were shown as they were asked about; they end with it.
                for inner in [t for t in self._open_tools if t.startswith(tool_id + ".") or t == tool_id]:
                    self._tool_result(inner, output, ok=ok)
                return
            if tool_id not in self._open_tools and tool_id not in self._shown:
                # Never gated (refused before it reached the gate, or a tool
                # the gate is not asked about): shown now, as it was called.
                name, shaped = claude_shape(part.get("tool"), state.get("input"), self.cwd, self._servers)
                self._tool_use(tool_id, name, shaped)
            self._tool_result(tool_id, output, ok=ok)
        elif kind == "step_finish":
            self._usage(str(part.get("messageID") or ""), part.get("tokens"))
        elif kind == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            data = error.get("data") if isinstance(error.get("data"), dict) else {}
            self._error = str(data.get("message") or error.get("message") or error.get("name") or event)

    async def _run(self, proc: asyncio.subprocess.Process) -> None:
        stderr: list[str] = []

        async def read_stderr() -> None:
            assert proc.stderr is not None
            while line := await proc.stderr.readline():
                text = line.decode(errors="replace").rstrip()
                if text:
                    stderr.append(text)
                    if self.options.stderr is not None and not text.startswith("timestamp="):
                        with contextlib.suppress(Exception):
                            self.options.stderr(text)

        reading = asyncio.create_task(read_stderr())
        assert proc.stdout is not None
        while line := await proc.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            try:
                self._handle(event)
            except Exception:
                log.exception("could not translate an OpenCode event")
        code = await proc.wait()
        with contextlib.suppress(Exception):
            await reading
        if self._interrupted or proc is not self._proc:
            return
        await self._count_the_rest()
        if code != 0 and not self._error:
            self._error = (stderr[-1] if stderr else "") or f"OpenCode exited ({code})"
        self._finish_turn(error=self._error)

    async def _count_the_rest(self) -> None:
        """The tokens the stream did not print: its last model call's, which
        `opencode run` exits before reporting. Read from the session's export."""
        if not self.session:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                str(cli()), "session", "export", "--standalone", self.session, cwd=str(self.cwd),
                env={**_env(), "OPENCODE_CONFIG_DIR": str(HOME), "PWD": str(self.cwd)},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=256 * 1024 * 1024)
            out, _ = await asyncio.wait_for(proc.communicate(), 60)
            exported = json.loads(out)
        except (OSError, TimeoutError, ValueError):
            log.warning("could not read the usage of OpenCode session %s", self.session)
            return
        started = self._turn_started * 1000
        for message in exported.get("messages") or []:
            info = message.get("info") if isinstance(message.get("info"), dict) else message
            created = ((info.get("time") or {}).get("created") or 0) if isinstance(info, dict) else 0
            if isinstance(info, dict) and info.get("tokens") and created >= started - 1000:
                self._usage(str(info.get("id") or ""), info["tokens"])

    def _finish_turn(self, *, error: str | None) -> None:
        if not self._in_turn:
            return
        self._in_turn = False
        for tool_id in [*self._open_tools]:
            self._tool_result(tool_id, "", ok=error is None)
        took = int((time.time() - self._turn_started) * 1000)
        self._messages.put_nowait(ResultMessage(
            subtype="error" if error else "success", duration_ms=took, duration_api_ms=took,
            is_error=error is not None, num_turns=self._steps, session_id=self.session or "",
            # OpenCode prices a Go call at nothing; the runner's estimate at
            # the plan's own rates (pricing.estimate) stands instead.
            total_cost_usd=None, usage=self._turn_usage or None,
            result=error if error and error != "interrupted" else "\n\n".join(self._response[-1:]),
        ))
