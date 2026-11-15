"""OpenAI's Codex as a task's agent, on the operator's ChatGPT subscription (design/29).

A task whose model is a Codex one (`codex:<model>#<effort>`, e.g.
`codex:gpt-5.6-sol#medium`) runs the Codex CLI, signed in with the operator's
ChatGPT account (`codex login`): the work is paid for by the subscription, as
Claude's, Google's and OpenCode Go's are. Every API-key variable is removed from
the environment Codex runs in, and a model is only offered as runnable when
Codex is signed in with ChatGPT rather than a key.

**The protocol.** Each run is one `codex app-server` over stdio: JSON-RPC, the
same protocol Codex's IDE extensions and desktop app speak. A task's session is
a Codex *thread* (`thread/start`, or `thread/resume` for a follow-up), and each
dex turn is a `turn/start`. Unlike `codex exec`, the process outlives a turn,
an interrupt (`turn/interrupt`) keeps it, and it streams reasoning summaries,
token counts and the plan's rate-limit readings as it goes. `CodexClient` turns
those notifications into the Claude Agent SDK's own messages, as
`opencode.OpencodeClient` does, so the runner drives it with the code it has
for Claude.

**Permissions.** dex decides every call. Codex's own approval and sandbox are
opened for the session (`approvalPolicy: never`, `danger-full-access`) and a
PreToolUse hook (`tools/codex_hook.py`), named in the session's flags, hands
each call (shell commands, `apply_patch`, MCP tools) to this task before it
runs; the task's policy answers, given the call as the Claude tool it amounts
to (`claude_calls`). Codex trusts a hook only once it has been reviewed, by
the hash of how it is named: `install` records that trust in the operator's
Codex config, through Codex's own config API, for dex's hook alone.

**The operator's own Codex setup** stays out of a dex session: its plugins,
apps, MCP servers, notifier and AGENTS.md discovery are switched off by flags
for dex's process only (`_flags`). Codex keeps its home (`~/.codex`), which is
where its sign-in lives; dex's threads are listed among the operator's own.

**dex's tools** are served over HTTP on the loopback interface from the same
in-process MCP servers a Claude run gets (`antigravity.Bridge`), and named to
Codex as streamable-HTTP MCP servers in the session's flags.

**Usage.** Each model call's tokens arrive as `thread/tokenUsage/updated`
(input, cached input, output, reasoning). Codex reports no dollars: each call
is priced at OpenAI's list price for the model (`pricing.SUBSCRIPTION_PRICES`).
The plan's rate-limit windows arrive with every call
(`account/rateLimits/updated`) and can be read for free at any time
(`rate_limits`), so dex holds Codex work before a window runs out, as it does
for Claude's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
import tomllib
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    RateLimitEvent,
    RateLimitInfo,
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

log = logging.getLogger("dex.codex")

#: How a model id says it runs on Codex: `codex:gpt-5.6-sol#medium`.
PREFIX = "codex:"

#: What switching the picker to OpenAI chooses: Sol, thinking at medium.
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_EFFORT = "medium"

#: Reasoning effort as Codex names it, least to most.
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

#: Models Codex lists that are not agents to run a task on: its own reviewer
#: of approval requests.
NOT_AGENTS = {"codex-auto-review"}

#: Where dex keeps its copy of the hook; the operator's Codex home is never
#: written to, except for the one trust entry `install` records there.
HOME = Path(os.environ.get("DEX_CODEX_HOME", Path.home() / ".dex" / "codex"))
HOOK = HOME / "hook.py"

#: The hook runs on the system Python, named by a path that never changes:
#: Codex trusts a hook by the hash of how it is named (command, timeout,
#: matcher), so a name that moved with dex's checkout would need trusting again.
HOOK_PYTHON = "/usr/bin/python3"

#: As long as the operator may take to answer a question about a call.
HOOK_TIMEOUT_S = 86400

#: What signs a provider in per call. A ChatGPT session never needs one, and a
#: key in the environment would bill the API instead of the subscription.
API_KEY_PATTERN = re.compile(r"(_API_KEY|_AUTH_TOKEN|^HF_TOKEN)$")

#: Codex's features that are nothing a dex task does, or that the policy has
#: no way to judge (a subagent, its browser, a scheduled goal): off for dex's
#: process only.
FEATURES_OFF = ("plugins", "apps", "browser_use", "browser_use_external", "in_app_browser", "computer_use",
                "image_generation", "multi_agent", "multi_agent_v2", "goals", "memories", "remote_plugin")

#: Local tools that only keep Codex's own books (its plan): run unasked, and
#: shown from their own events.
QUIET_TOOLS = {"update_plan"}

#: How long a call to the app-server may take before it counts as stuck.
RPC_TIMEOUT_S = 120.0

#: Offered before Codex's list has been read, or when it cannot be.
FALLBACK_MODELS = [
    {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
     "default": "medium", "hidden": False},
    {"id": "gpt-5.6-terra", "name": "GPT-5.6-Terra", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
     "default": "medium", "hidden": False},
    {"id": "gpt-5.6-luna", "name": "GPT-5.6-Luna", "efforts": ["low", "medium", "high", "xhigh", "max"],
     "default": "medium", "hidden": False},
]


def is_codex(model: str | None) -> bool:
    return bool(model) and str(model).startswith(PREFIX)


def split(model: str) -> tuple[str, str | None]:
    """`codex:gpt-5.6-sol#high` -> (`gpt-5.6-sol`, `high`); no effort -> None."""
    bare = model[len(PREFIX):] if is_codex(model) else model
    name, _, effort = bare.partition("#")
    return name, effort or None


# ------------------------------------------------------------------ the CLI


def cli() -> Path | None:
    """Codex's native binary: DEX_CODEX_BIN, else the one behind `codex` on the
    PATH or where its installers put it.

    `codex` from npm is a Node launcher for a native binary in the package; dex
    runs that binary itself, so a run does not depend on `node` being on the
    PATH of dex's clean environment.
    """
    named = os.environ.get("DEX_CODEX_BIN")
    if named:
        return Path(named) if Path(named).exists() else None
    found = shutil.which("codex") or next(
        (str(p) for p in (Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"),
                          Path("/Applications/Codex.app/Contents/Resources/codex")) if p.exists()), None)
    if not found:
        return None
    return _native(Path(found).resolve())


def _native(path: Path) -> Path:
    """The native binary an npm launcher (`bin/codex.js`) would start, else `path`."""
    if path.suffix != ".js":
        return path
    package = path.parent.parent
    for candidate in sorted(package.glob("node_modules/@openai/codex-*/vendor/*/bin/codex")):
        if os.access(candidate, os.X_OK):
            return candidate
    return path


def _env() -> dict[str, str]:
    """dex's environment, less anything that would sign Codex in another way or
    point it at another home: a run is always on the subscription."""
    return {k: v for k, v in os.environ.items()
            if not API_KEY_PATTERN.search(k) and not k.startswith(("OPENAI_", "CODEX_", "DEX_CODEX_"))}


def codex_home() -> Path:
    """Codex's own home, where its sign-in, sessions and config live."""
    return Path(os.environ.get("DEX_CODEX_USER_HOME", Path.home() / ".codex"))


def _user_mcp_servers() -> list[str]:
    """The MCP servers the operator's own config names: off in a dex session."""
    try:
        config = tomllib.loads((codex_home() / "config.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return []
    servers = config.get("mcp_servers")
    return sorted(servers) if isinstance(servers, dict) else []


def _toml(value: Any) -> str:
    return json.dumps(value)  # a JSON string or number is valid TOML


def hook_command() -> str:
    return f"{HOOK_PYTHON} -I {shlex.quote(str(HOOK))}"


def _flags(mcp: dict[str, str] | None = None) -> list[str]:
    """The `-c` overrides every dex process runs with: dex's gate, dex's MCP
    servers, and none of the operator's own plugins, apps, MCP servers,
    notifier or instruction files (dex hands the project's brief over itself)."""
    hook = (f'hooks.PreToolUse=[{{matcher="*",hooks=[{{type="command",'
            f'command={_toml(hook_command())},timeout={HOOK_TIMEOUT_S}}}]}}]')
    flags = ["-c", hook, "-c", "features.hooks=true", "-c", "notify=[]", "-c", "project_doc_max_bytes=0",
             "-c", "check_for_update_on_startup=false"]
    for feature in FEATURES_OFF:
        flags += ["-c", f"features.{feature}=false"]
    for server in _user_mcp_servers():
        if server not in (mcp or {}):
            flags += ["-c", f"mcp_servers.{_key(server)}.enabled=false"]
    for name, url in (mcp or {}).items():
        flags += ["-c", f"mcp_servers.{_key(name)}.url={_toml(url)}",
                  "-c", f"mcp_servers.{_key(name)}.startup_timeout_sec=30"]
    return flags


def _key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml(name)


# ------------------------------------------------ one-shot app-server calls


def _ask(calls: list[tuple[str, dict[str, Any]]], *, flags: Iterable[str] = (),
         timeout: float = 60.0) -> list[dict[str, Any]]:
    """Start `codex app-server`, make `calls` in order and return each reply
    (`{"result": …}` or `{"error": …}`). Nothing here reaches the model: it
    is how dex reads models, the account and its limits, and trusts its hook."""
    binary = cli()
    if binary is None:
        return []
    proc = subprocess.Popen([str(binary), "app-server", *flags], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=_env(), cwd=str(Path.home()), text=True)
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.time() + timeout
    replies: list[dict[str, Any]] = []
    try:
        assert proc.stdin is not None
        for number, (method, params) in enumerate(
                [("initialize", {"clientInfo": {"name": "dex", "title": "dex", "version": "1"}, "capabilities": None}),
                 *calls]):
            proc.stdin.write(json.dumps({"id": number, "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            if number == 0:
                proc.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
                proc.stdin.flush()
            while True:
                line = lines.get(timeout=max(0.1, deadline - time.time()))
                if line is None:
                    return replies
                with contextlib.suppress(ValueError):
                    message = json.loads(line)
                    if message.get("id") == number and ("result" in message or "error" in message):
                        if number:
                            replies.append(message)
                        break
    except (OSError, queue.Empty):
        log.warning("codex app-server did not answer %s", [m for m, _ in calls])
    finally:
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(5)
    return replies


# ------------------------------------------------------------------ models

_models_cache: tuple[float, list[dict[str, Any]], dict[str, Any]] = (0.0, [], {})
_MODELS_TTL = 600.0


def _read() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    replies = _ask([("model/list", {"includeHidden": True}), ("account/read", {})])
    found: list[dict[str, Any]] = []
    account: dict[str, Any] = {}
    for reply in replies:
        result = reply.get("result") or {}
        if isinstance(result.get("data"), list):
            for m in result["data"]:
                if not isinstance(m, dict) or not m.get("model"):
                    continue
                efforts = [e.get("reasoningEffort") for e in m.get("supportedReasoningEfforts") or []
                           if isinstance(e, dict) and e.get("reasoningEffort") in EFFORTS]
                found.append({"id": m["model"], "name": m.get("displayName") or m["model"],
                              "efforts": sorted(efforts, key=EFFORTS.index),
                              "default": m.get("defaultReasoningEffort"), "hidden": bool(m.get("hidden")),
                              "description": m.get("description") or ""})
        elif "account" in result:
            account = result.get("account") or {}
    return found, account


def _refresh() -> None:
    global _models_cache
    if time.time() - _models_cache[0] < _MODELS_TTL:
        return
    found, account = _read() if cli() is not None else ([], {})
    _models_cache = (time.time(), found, account)


def models() -> list[dict[str, Any]]:
    """Codex's models for this account: `id`, `name`, `efforts` (least to most),
    `default` effort and `hidden`. Sol first, then as Codex orders them."""
    _refresh()
    listed = [m for m in (_models_cache[1] or FALLBACK_MODELS) if m["id"] not in NOT_AGENTS]
    return sorted(listed, key=lambda m: (m["id"] != DEFAULT_MODEL, m.get("hidden", False)))


def signed_in() -> bool:
    """Whether Codex is signed in with ChatGPT (a subscription), not an API key."""
    _refresh()
    return (_models_cache[2] or {}).get("type") == "chatgpt"


def plan() -> str | None:
    _refresh()
    return (_models_cache[2] or {}).get("planType")


def choices() -> list[dict[str, Any]]:
    """Every Codex model at every effort it offers, for the picker: Sol at
    medium first (what choosing OpenAI picks), each saying whether it can run."""
    ready = cli() is not None and signed_in()
    why = None if ready else ("install Codex: npm install -g @openai/codex" if cli() is None
                              else "sign in: run `codex login` with your ChatGPT account")
    listed: list[dict[str, Any]] = []
    for model in models():
        efforts = model["efforts"] or [DEFAULT_EFFORT]
        first = DEFAULT_EFFORT if DEFAULT_EFFORT in efforts else efforts[0]
        for effort in [first, *[e for e in efforts if e != first]]:
            listed.append({
                "id": f"{PREFIX}{model['id']}#{effort}",
                "label": f"{model['name']} · {effort}",
                "note": "ChatGPT subscription (Codex)",
                "provider": "codex", "available": ready, **({"unavailable": why} if why else {}),
            })
    return listed


def effort_for(model_id: str) -> str:
    """The effort a model id runs at: its own, if the model offers it, else the
    nearest it offers below (above, if none is below); medium when unnamed."""
    name, wanted = split(model_id)
    wanted = wanted if wanted in EFFORTS else DEFAULT_EFFORT
    offered = next((m["efforts"] for m in models() if m["id"] == name), None) or list(EFFORTS)
    if wanted in offered:
        return wanted
    level = EFFORTS.index(wanted)
    below = [e for e in offered if EFFORTS.index(e) < level]
    return max(below, key=EFFORTS.index) if below else min(offered, key=EFFORTS.index)


def has_thread(thread_id: str) -> bool:
    """Whether Codex keeps a thread with this id (its rollout file on disk)."""
    if not thread_id:
        return False
    home = codex_home()
    pattern = f"rollout-*-{thread_id}.jsonl"
    return any(home.glob(f"sessions/*/*/*/{pattern}")) or any(home.glob(f"archived_sessions/{pattern}"))


# ------------------------------------------------------------ rate limits


def window_name(snapshot: dict[str, Any], which: str) -> str:
    """dex's name for one of a snapshot's windows: `codex_5h`, `codex_weekly`,
    else by its length; prefixed with its bucket when that is not `codex`."""
    window = snapshot.get(which) or {}
    minutes = window.get("windowDurationMins")
    span = {300: "5h", 10080: "weekly"}.get(minutes) if isinstance(minutes, int) else None
    span = span or (f"{minutes}m" if isinstance(minutes, int) else which)
    bucket = str(snapshot.get("limitId") or "codex")
    return f"{bucket if bucket.startswith('codex') else 'codex_' + bucket}_{span}"


def readings(snapshot: Any) -> list[dict[str, Any]]:
    """A Codex `RateLimitSnapshot` as dex's limit readings, one per window:
    `rateLimitType`, `utilization` (0-1), `resetsAt` (epoch s) and `status`."""
    if not isinstance(snapshot, dict):
        return []
    reached = bool(snapshot.get("rateLimitReachedType")) or snapshot.get("spendControlReached") is True
    found = []
    for which in ("primary", "secondary"):
        window = snapshot.get(which)
        if not isinstance(window, dict) or not isinstance(window.get("usedPercent"), (int, float)):
            continue
        used = float(window["usedPercent"]) / 100
        status = "rejected" if used >= 1 or (reached and which == "primary") else "allowed"
        found.append({"rateLimitType": window_name(snapshot, which), "utilization": round(used, 4),
                      "resetsAt": window.get("resetsAt"), "status": status, "provider": "codex",
                      "plan": snapshot.get("planType")})
    return found


def rate_limits() -> list[dict[str, Any]]:
    """Where the plan's windows stand now, read from Codex for free (no model call)."""
    replies = _ask([("account/rateLimits/read", {})], timeout=30)
    result = (replies[0].get("result") or {}) if replies else {}
    buckets = result.get("rateLimitsByLimitId") or {}
    snapshots = [s for s in buckets.values() if isinstance(s, dict)] if isinstance(buckets, dict) else []
    return [r for s in (snapshots or [result.get("rateLimits")]) for r in readings(s)]


# ---------------------------------------------------------------- install

_trusted: str | None = None


def install() -> None:
    """Put dex's hook in place and make sure Codex trusts it.

    The script is copied, by rename, so a running session never reads it half
    written; its contents are not part of what Codex trusts, only how it is
    named. Trust is checked once per dex process (`hooks/list`) and, when Codex
    has not recorded it, written through Codex's own config API: one
    `hooks.state` entry in the operator's config, for dex's session hook only.
    """
    global _trusted
    source = (Path(__file__).parent / "tools" / "codex_hook.py").read_text()
    if not HOOK.exists() or HOOK.read_text() != source:
        HOOK.parent.mkdir(parents=True, exist_ok=True)
        staged = HOOK.with_name(f".{HOOK.name}.{os.getpid()}")
        staged.write_text(source)
        staged.replace(HOOK)
    if _trusted == hook_command():
        return
    flags = _flags()
    replies = _ask([("hooks/list", {"cwds": [str(HOME)]})], flags=flags)
    entries = ((replies[0].get("result") or {}).get("data") or [{}]) if replies else [{}]
    ours = [h for h in entries[0].get("hooks") or []
            if h.get("source") == "sessionFlags" and h.get("command") == hook_command()]
    if not ours:
        raise CodexError("Codex did not load dex's hook; is `features.hooks` turned off by a managed config?")
    hook = ours[0]
    if hook.get("trustStatus") not in ("trusted", "managed"):
        written = _ask([("config/value/write", {
            "keyPath": f'hooks.state."{hook["key"]}".trusted_hash', "value": hook["currentHash"],
            "mergeStrategy": "upsert"})])
        if not written or "error" in written[0]:
            raise CodexError(f"could not record trust in dex's Codex hook: {written[0].get('error') if written else 'no answer'}")
        log.info("trusted dex's Codex hook (%s)", hook["currentHash"])
    _trusted = hook_command()


# ------------------------------------------------------------- tool shapes


def _path(value: Any, cwd: Path) -> str:
    text = str(value or "").strip()
    return str(Path(text) if Path(text).is_absolute() else cwd / text) if text else ""


def parse_patch(patch: str, cwd: Path) -> list[dict[str, Any]]:
    """An `apply_patch` envelope as the files it touches: `{"op": "add" |
    "update" | "delete", "path", "move_to", "content" (add), "hunks" (update:
    [(old, new)])}`, paths made absolute against `cwd`."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    old: list[str] = []
    new: list[str] = []

    def close_hunk() -> None:
        if current is not None and current["op"] == "update" and (old or new):
            current["hunks"].append(("\n".join(old), "\n".join(new)))
        old.clear()
        new.clear()

    for line in patch.splitlines():
        header = re.match(r"\*\*\* (Add|Update|Delete) File: (.+)$", line)
        if header:
            close_hunk()
            current = {"op": header.group(1).lower(), "path": _path(header.group(2), cwd), "move_to": None,
                       "content": [], "hunks": []}
            files.append(current)
            continue
        if current is None or line.startswith(("*** Begin Patch", "*** End Patch", "*** End of File")):
            continue
        moved = re.match(r"\*\*\* Move to: (.+)$", line)
        if moved:
            current["move_to"] = _path(moved.group(1), cwd)
        elif current["op"] == "add":
            current["content"].append(line[1:] if line.startswith("+") else line)
        elif current["op"] == "update":
            if line.startswith("@@"):
                close_hunk()
            elif line.startswith("-"):
                old.append(line[1:])
            elif line.startswith("+"):
                new.append(line[1:])
            else:
                text = line[1:] if line.startswith(" ") else line
                old.append(text)
                new.append(text)
    close_hunk()
    for entry in files:
        entry["content"] = "\n".join(entry["content"]) + ("\n" if entry["content"] else "")
    return files


def claude_calls(tool: str | None, args: Any, cwd: Path) -> list[tuple[str, dict[str, Any]]]:
    """A Codex tool call as the Claude tools it amounts to, each for the policy
    to judge: `Bash` for a shell command, and for `apply_patch` one call per
    file it touches (`Write` for a new one, `MultiEdit` for a change, `Bash`
    for a delete or a move). An MCP tool keeps its name (`mcp__dex__ask_user`),
    which Codex already spells as Claude does. Empty for a tool that runs
    unasked (`QUIET_TOOLS`)."""
    args = args if isinstance(args, dict) else {}
    name = str(tool or "")
    if name in QUIET_TOOLS:
        return []
    if name == "Bash":
        shaped: dict[str, Any] = {"command": str(args.get("command") or "")}
        workdir = args.get("workdir") or args.get("cwd")
        if workdir:
            shaped["cwd"] = _path(workdir, cwd)
        return [("Bash", shaped)]
    if name == "apply_patch":
        base = Path(_path(args.get("workdir"), cwd)) if args.get("workdir") else cwd
        calls: list[tuple[str, dict[str, Any]]] = []
        for entry in parse_patch(str(args.get("command") or args.get("patch") or args.get("input") or ""), base):
            if entry["op"] == "add":
                calls.append(("Write", {"file_path": entry["path"], "content": entry["content"]}))
            elif entry["op"] == "delete":
                calls.append(("Bash", {"command": f"rm -- {shlex.quote(entry['path'])}",
                                       "description": "apply_patch deletes this file"}))
            else:
                if entry["hunks"]:
                    calls.append(("MultiEdit", {"file_path": entry["path"], "edits": [
                        {"old_string": a, "new_string": b} for a, b in entry["hunks"]]}))
                if entry["move_to"]:
                    calls.append(("Bash", {"command": f"mv -- {shlex.quote(entry['path'])} "
                                                      f"{shlex.quote(entry['move_to'])}",
                                           "description": "apply_patch moves this file"}))
        return calls or [("Edit", {"file_path": "", "old_string": "", "new_string": ""})]
    if name == "view_image":
        return [("Read", {"file_path": _path(args.get("path"), cwd)})]
    if name.startswith("mcp__"):
        return [(name, dict(args))]
    # Something dex has no Claude name for: the policy asks the operator.
    return [(f"codex:{name or 'tool'}", dict(args))]


def item_shape(item: dict[str, Any], cwd: Path) -> tuple[str, dict[str, Any]] | None:
    """A tool the gate never saw (refused before it, or hosted by OpenAI, like
    web search), from its item, so it is still shown."""
    kind = item.get("type")
    if kind == "commandExecution":
        return "Bash", {"command": _unwrap(str(item.get("command") or ""))}
    if kind == "fileChange":
        changes = item.get("changes") or []
        first = changes[0] if changes and isinstance(changes[0], dict) else {}
        return "Edit", {"file_path": str(first.get("path") or ""), "old_string": "", "new_string": ""}
    if kind == "mcpToolCall":
        return f"mcp__{item.get('server')}__{item.get('tool')}", dict(item.get("arguments") or {})
    if kind == "webSearch":
        return "WebSearch", {"query": str(item.get("query") or "")}
    if kind == "imageView":
        return "Read", {"file_path": str(item.get("path") or "")}
    if kind == "dynamicToolCall":
        return f"codex:{item.get('tool')}", dict(item.get("arguments") or {})
    return None


def _unwrap(command: str) -> str:
    """`/bin/zsh -lc 'ls -la'` as the `ls -la` the model wrote."""
    match = re.fullmatch(r"\S*/(?:ba|z)?sh -l?c (.+)", command, re.S)
    if not match:
        return command
    try:
        parts = shlex.split(match.group(1))
    except ValueError:
        return command
    return parts[0] if len(parts) == 1 else command


def claude_usage(last: Any) -> dict[str, Any] | None:
    """One model call's token counts (Codex's `TokenUsageBreakdown`) under the
    names dex's pricing reads. Codex counts cached input inside input, and
    reasoning inside output, as dex does thinking."""
    if not isinstance(last, dict):
        return None
    count = lambda key: max(0, int(last.get(key) or 0))  # noqa: E731
    cached = min(count("inputTokens"), count("cachedInputTokens"))
    return {
        "input_tokens": count("inputTokens") - cached,
        "output_tokens": count("outputTokens"),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": count("cacheWriteInputTokens"),
        "output_tokens_details": {"thinking_tokens": count("reasoningOutputTokens")},
    }


def _add_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, dict):
            _add_usage(total.setdefault(key, {}), value)
        else:
            total[key] = total.get(key, 0) + value


#: Codex's error kinds that are the connection, not the work: worded so the
#: runner's `CONNECTION_ERROR` retries the turn on the same thread.
CONNECTION_KINDS = {"httpConnectionFailed", "responseStreamConnectionFailed", "responseStreamDisconnected",
                    "responseTooManyFailedAttempts"}


def error_text(error: Any, limits: list[dict[str, Any]] | None = None) -> str:
    """A failed turn's `TurnError` as the runner's words: a spent plan limit
    reads as one (`usage.is_usage_limit_error`, with the window's reset), a
    dropped connection as one."""
    if not isinstance(error, dict):
        return str(error or "Codex failed")
    message = str(error.get("message") or "Codex failed")
    info = error.get("codexErrorInfo")
    kind = next(iter(info), None) if isinstance(info, dict) else info
    if kind == "usageLimitExceeded":
        resets = [r["resetsAt"] for r in limits or [] if r.get("status") == "rejected"
                  and isinstance(r.get("resetsAt"), (int, float))]
        wait = int(max(resets) - time.time()) if resets else 0
        return f"usage limit reached: {message}" + (f" (resets in {wait}s)" if wait > 0 else "")
    if kind in CONNECTION_KINDS:
        return f"connection lost: {message}"
    return message


# ----------------------------------------------------------------- client


class CodexError(RuntimeError):
    pass


class CodexClient:
    """`ClaudeSDKClient`'s interface, over `codex app-server`.

    Takes the `ClaudeAgentOptions` the Claude run is built with and reads what
    applies: the directory, the model and effort (from the model id), the
    appended system prompt (Codex's developer instructions), the permission
    callback, dex's MCP servers, the thread to resume and the environment.
    Yields Claude SDK messages: text and reasoning summaries as
    `StreamEvent`s, each tool call as an `AssistantMessage` with its
    `ToolUseBlock` and its outcome as a `UserMessage`, the plan as a
    `TodoWrite`, each model call's tokens as an `AssistantMessage` with usage,
    each rate-limit reading as a `RateLimitEvent`, and one `ResultMessage` per
    turn. Codex's short progress notes ("commentary") also go to `status`, as
    the task's one-line activity.
    """

    def __init__(self, options: Any, *, notice: Callable[[str], None] | None = None,
                 status: Callable[[str], None] | None = None) -> None:
        self.options = options
        self.cwd = Path(options.cwd or ".")
        self.model, _ = split(str(options.model))
        self.effort = effort_for(str(options.model))
        self.decide = options.can_use_tool
        self.notice = notice or (lambda _text: None)
        self.status = status or (lambda _text: None)
        system = options.system_prompt
        self.system = system.get("append", "") if isinstance(system, dict) else (system or "")
        self.session: str | None = options.resume
        servers = options.mcp_servers if isinstance(options.mcp_servers, dict) else {}
        self._bridge = Bridge(servers, self._gate)
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[Any] | None = None
        self._stderr: asyncio.Task[Any] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._messages: asyncio.Queue[Any] = asyncio.Queue()
        self._thread_open = False
        self._announced = False
        self._turn: str | None = None
        self._in_turn = False
        self._turn_started = 0.0
        self._steps = 0
        self._error: str | None = None
        self._response: list[str] = []
        self._turn_usage: dict[str, Any] = {}
        #: Model calls whose tokens have been counted, by the thread's running total.
        self._counted: set[int] = set()
        self._limits: list[dict[str, Any]] = []
        #: Stream blocks open per item (text, reasoning), and their index.
        self._blocks: dict[str, int] = {}
        self._index = 0
        self._streamed: set[str] = set()
        #: Calls shown, waiting for their outcome, by Codex's call id: each
        #: id's dex calls (an `apply_patch` is one per file).
        self._open_tools: dict[str, list[str]] = {}
        self._shown: set[str] = set()
        self._plans = 0
        self._stderr_tail: list[str] = []

    # -------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> "CodexClient":
        if cli() is None:
            raise CodexError("Codex is not installed: npm install -g @openai/codex")
        await asyncio.to_thread(install)
        await self._bridge.start()
        await self._start()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self._end_process()
        await self._bridge.stop()

    def _mcp(self) -> dict[str, str]:
        return {name: self._bridge.url(f"mcp/{name}") for name in self._bridge.servers}

    async def _start(self) -> None:
        env = _env()
        env.update({k: v for k, v in (self.options.env or {}).items() if not API_KEY_PATTERN.search(k)})
        env["DEX_CODEX_GATE"] = self._bridge.url("gate")
        self._proc = await asyncio.create_subprocess_exec(
            str(cli()), "app-server", *_flags(self._mcp()), cwd=str(self.cwd), env=env, start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read(self._proc), name="dex-codex-read")
        self._stderr = asyncio.create_task(self._read_stderr(self._proc), name="dex-codex-stderr")
        self._thread_open = False
        await self._call("initialize", {"clientInfo": {"name": "dex", "title": "dex", "version": "1"},
                                        "capabilities": None})
        await self._notify("initialized", {})

    async def _end_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                if proc.stdin is not None:
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, 9)
                await proc.wait()
        for task in (self._reader, self._stderr):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._reader = self._stderr = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(CodexError("Codex exited"))
        self._pending.clear()

    # ------------------------------------------------------------- JSON-RPC

    async def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise CodexError("Codex is not running")
        async with self._write_lock:
            proc.stdin.write((json.dumps(message) + "\n").encode())
            await proc.stdin.drain()

    async def _call(self, method: str, params: dict[str, Any], timeout: float = RPC_TIMEOUT_S) -> Any:
        self._next_id += 1
        number = self._next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[number] = future
        await self._send({"id": number, "method": method, "params": params})
        try:
            reply = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(number, None)
        if "error" in reply:
            error = reply["error"] or {}
            raise CodexError(f"{method}: {error.get('message') or error}")
        return reply.get("result")

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"method": method, "params": params})

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while line := await proc.stdout.readline():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "id" in message and ("result" in message or "error" in message) and "method" not in message:
                future = self._pending.get(message["id"])
                if future is not None and not future.done():
                    future.set_result(message)
                continue
            try:
                if "id" in message and "method" in message:
                    asyncio.create_task(self._answer(message))
                elif "method" in message:
                    self._handle(message["method"], message.get("params") or {})
            except Exception:
                log.exception("could not translate a Codex message")
        code = await proc.wait()
        if proc is self._proc:
            # Codex went away under a turn: what it said last is the reason.
            self._proc = None
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CodexError("Codex exited"))
            if self._in_turn:
                tail = self._stderr_tail[-1] if self._stderr_tail else ""
                self._finish_turn(error=self._error or tail or f"Codex exited ({code})")

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while line := await proc.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            if not text:
                continue
            self._stderr_tail = [*self._stderr_tail[-19:], text]
            # Codex logs to stderr at info level; only its errors are the run's.
            if self.options.stderr is not None and re.search(r"\b(ERROR|error:|panicked)\b", text):
                with contextlib.suppress(Exception):
                    self.options.stderr(text)

    async def _answer(self, request: dict[str, Any]) -> None:
        """Codex asking the client something. With approvals off and the gate
        deciding, approvals should not come; if one does, the policy answers."""
        method, params = request.get("method"), request.get("params") or {}
        result: dict[str, Any] | None
        if method in ("item/commandExecution/requestApproval", "execCommandApproval"):
            command = params.get("command")
            command = " ".join(command) if isinstance(command, list) else str(command or "")
            decision = await self.decide("Bash", {"command": command}, ToolPermissionContext(
                tool_use_id=str(params.get("itemId") or params.get("callId") or ""), title="Bash"))
            allowed = isinstance(decision, PermissionResultAllow)
            result = {"decision": ("accept" if allowed else "decline") if method.startswith("item/")
                      else ("approved" if allowed else "denied")}
        elif method in ("item/fileChange/requestApproval", "applyPatchApproval"):
            # Each file of the patch was put to the policy at the gate already.
            result = {"decision": "accept" if method.startswith("item/") else "approved"}
        elif method == "item/permissions/requestApproval":
            result = {"permissions": {}, "scope": "turn"}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "decline", "content": None}
        elif method == "item/tool/requestUserInput":
            result = {"answers": {}}
        else:
            result = None
        with contextlib.suppress(Exception):
            if result is None:
                await self._send({"id": request.get("id"), "error": {"code": -32601,
                                                                     "message": f"dex does not answer {method}"}})
            else:
                await self._send({"id": request.get("id"), "result": result})

    # ---------------------------------------------------- the SDK's interface

    async def _open_thread(self) -> None:
        params: dict[str, Any] = {
            "model": self.model, "cwd": str(self.cwd), "approvalPolicy": "never",
            "sandbox": "danger-full-access", "developerInstructions": self.system or None,
        }
        if self.session:
            try:
                result = await self._call("thread/resume", {"threadId": self.session, **params})
            except CodexError as exc:
                raise CodexError(f"could not resume Codex thread {self.session}: {exc}") from exc
        else:
            result = await self._call("thread/start", {**params, "serviceName": "dex"})
        self.session = str(((result or {}).get("thread") or {}).get("id") or self.session or "")
        self._thread_open = True
        if not self._announced and self.session:
            self._announced = True
            self._messages.put_nowait(SystemMessage(subtype="init", data={
                "session_id": self.session, "model": f"{PREFIX}{self.model}#{self.effort}", "provider": "codex"}))

    async def query(self, prompt: str) -> None:
        """Start a turn. Its messages, and its result, arrive on the stream."""
        if self._proc is None or self._proc.returncode is not None:
            await self._start()
        if not self._thread_open:
            await self._open_thread()
        self._in_turn = True
        self._turn_started, self._steps = time.time(), 0
        self._error, self._response, self._turn_usage = None, [], {}
        self._plans = 0
        try:
            result = await self._call("turn/start", {
                "threadId": self.session, "effort": self.effort, "summary": "detailed",
                "input": [{"type": "text", "text": prompt, "text_elements": []}]})
        except CodexError as exc:
            self._finish_turn(error=str(exc))
            return
        self._turn = str(((result or {}).get("turn") or {}).get("id") or "")

    async def interrupt(self) -> None:
        """Stop the turn under way; the process and the thread stay. Its
        result (`interrupted`) arrives when Codex has wound it down."""
        if not self._in_turn or not self._turn:
            return
        try:
            await self._call("turn/interrupt", {"threadId": self.session, "turnId": self._turn}, timeout=20)
        except (CodexError, TimeoutError):
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
        """The hook's question: may this call run? The policy answers, for
        each Claude call it amounts to; any refusal refuses the whole call.

        A refused call never becomes an item, so every call is shown from
        here, as it starts, under Codex's own id: its item settles it."""
        tool = str(payload.get("tool_name") or "")
        args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        call_id = str(payload.get("tool_use_id") or f"codex-{time.time()}")
        calls = claude_calls(tool, args, self.cwd)
        if not calls:
            return {"decision": "allow"}
        ids = [call_id if i == 0 else f"{call_id}#{i + 1}" for i in range(len(calls))]
        for dex_id, (name, shaped) in zip(ids, calls):
            self._tool_use(call_id, dex_id, name, shaped)
        rewritten: dict[str, Any] | None = None
        for dex_id, (name, shaped) in zip(ids, calls):
            context = ToolPermissionContext(signal=None, suggestions=[], tool_use_id=dex_id, title=name)
            decision = await self.decide(name, shaped, context)
            reason = None
            if not isinstance(decision, PermissionResultAllow):
                reason = getattr(decision, "message", "") or "dex does not allow this"
            else:
                updated = decision.updated_input
                if updated not in (None, shaped):
                    if (tool == "Bash" and name == "Bash" and len(calls) == 1 and updated.get("command")
                            and updated["command"] != shaped.get("command")):
                        # Confined, rewritten: what runs is what the policy allowed.
                        rewritten = {**args, "command": updated["command"]}
                    elif {k: v for k, v in updated.items() if k in shaped} != shaped:
                        reason = "dex would only allow this changed, which it cannot do here"
            if reason is not None:
                self._settle(call_id, f"Denied by dex: {reason}", ok=False)
                return {"decision": "deny", "reason": reason}
        return {"decision": "allow", **({"input": rewritten} if rewritten else {})}

    # ------------------------------------------------------------ the stream

    def _event(self, event: dict[str, Any]) -> None:
        self._messages.put_nowait(StreamEvent(
            uuid=f"codex-{self.session}-{self._turn_started}", session_id=self.session or "", event=event))

    def _block_start(self, key: str, kind: str) -> int:
        if key in self._blocks:
            return self._blocks[key]
        self._index += 1
        self._blocks[key] = self._index
        self._event({"type": "content_block_start", "index": self._index, "content_block": {"type": kind}})
        return self._index

    def _block_delta(self, key: str, kind: str, text: str) -> None:
        index = self._block_start(key, kind)
        self._streamed.add(key)
        delta = {"type": "text_delta", "text": text} if kind == "text" else {"type": "thinking_delta", "thinking": text}
        self._event({"type": "content_block_delta", "index": index, "delta": delta})

    def _block_stop(self, key: str) -> None:
        index = self._blocks.pop(key, None)
        if index is not None:
            self._event({"type": "content_block_stop", "index": index})

    def _tool_use(self, call_id: str, dex_id: str, name: str, shaped: dict[str, Any]) -> None:
        if dex_id in self._shown:
            return
        self._shown.add(dex_id)
        self._open_tools.setdefault(call_id, []).append(dex_id)
        self._messages.put_nowait(AssistantMessage(
            content=[ToolUseBlock(id=dex_id, name=name, input=shaped)], model=PREFIX + self.model))

    def _settle(self, call_id: str, output: str, *, ok: bool) -> None:
        for dex_id in self._open_tools.pop(call_id, []):
            self._messages.put_nowait(UserMessage(content=[ToolResultBlock(
                tool_use_id=dex_id, content=output, is_error=not ok)]))

    def _usage(self, params: dict[str, Any]) -> None:
        """One model call's tokens, as Claude reports them per message: the
        runner files them by hour and skill (task_usage) and prices them."""
        token_usage = params.get("tokenUsage") or {}
        total = int((token_usage.get("total") or {}).get("totalTokens") or 0)
        usage = claude_usage(token_usage.get("last"))
        # Codex repeats a reading (the same running total) now and then.
        if not usage or not total or total in self._counted:
            return
        self._counted.add(total)
        self._steps += 1
        _add_usage(self._turn_usage, usage)
        self._messages.put_nowait(AssistantMessage(
            content=[], model=PREFIX + self.model, usage=usage, message_id=f"codex-{self.session}-{total}"))

    def _rate_limits(self, snapshot: Any) -> None:
        for reading in readings(snapshot):
            self._limits = [r for r in self._limits if r["rateLimitType"] != reading["rateLimitType"]] + [reading]
            self._messages.put_nowait(RateLimitEvent(
                rate_limit_info=RateLimitInfo(status=reading["status"], resets_at=reading.get("resetsAt"),
                                              utilization=reading["utilization"], raw=reading),
                uuid=f"codex-limit-{time.time()}", session_id=self.session or ""))

    def _plan(self, params: dict[str, Any]) -> None:
        """Codex's plan as Claude's to-do list, so the panel shows it the same way."""
        steps = [s for s in params.get("plan") or [] if isinstance(s, dict)]
        if not steps:
            return
        self._plans += 1
        status = {"inProgress": "in_progress", "in_progress": "in_progress", "completed": "completed"}
        todos = [{"content": str(s.get("step") or ""), "activeForm": str(s.get("step") or ""),
                  "status": status.get(str(s.get("status")), "pending")} for s in steps]
        plan_id = f"plan-{self._turn}-{self._plans}"
        self._tool_use(plan_id, plan_id, "TodoWrite", {"todos": todos})
        self._settle(plan_id, str(params.get("explanation") or "plan updated"), ok=True)

    def _item_started(self, item: dict[str, Any]) -> None:
        kind = item.get("type")
        item_id = str(item.get("id") or "")
        if kind == "agentMessage":
            self._block_start(item_id, "text")
        elif kind in ("commandExecution", "fileChange", "mcpToolCall", "webSearch", "imageView", "dynamicToolCall"):
            if item_id in self._open_tools or item_id in self._shown:
                return
            shape = item_shape(item, self.cwd)
            if shape is not None:
                self._tool_use(item_id, item_id, *shape)

    def _item_completed(self, item: dict[str, Any]) -> None:
        kind = item.get("type")
        item_id = str(item.get("id") or "")
        if kind == "agentMessage":
            text = str(item.get("text") or "")
            if text and item_id not in self._streamed:
                self._block_delta(item_id, "text", text)
            self._block_stop(item_id)
            if item.get("phase") == "commentary":
                line = text.strip().splitlines()[0] if text.strip() else ""
                if line:
                    self.status(line[:200])
            elif text:
                self._response.append(text)
        elif kind == "reasoning":
            summary = "\n\n".join(s for s in item.get("summary") or [] if s) or \
                "\n\n".join(s for s in item.get("content") or [] if s)
            keys = [k for k in self._blocks if k.startswith(item_id + ":")]
            if summary and not keys and not any(k.startswith(item_id + ":") for k in self._streamed):
                self._messages.put_nowait(AssistantMessage(
                    content=[ThinkingBlock(thinking=summary, signature="")], model=PREFIX + self.model))
            for key in keys:
                self._block_stop(key)
        elif kind == "commandExecution":
            output = str(item.get("aggregatedOutput") or "")
            code = item.get("exitCode")
            ok = item.get("status") == "completed" and code in (0, None)
            if code not in (0, None):
                output = f"{output}\n(exit code {code})".lstrip()
            self._settle(item_id, output, ok=ok)
        elif kind == "fileChange":
            changes = [c for c in item.get("changes") or [] if isinstance(c, dict)]
            output = "\n".join(f"{(c.get('kind') or {}).get('type', 'update')} {c.get('path')}" for c in changes)
            self._settle(item_id, output, ok=item.get("status") == "completed")
        elif kind == "mcpToolCall":
            result, error = item.get("result"), item.get("error")
            if error:
                self._settle(item_id, str(error.get("message") if isinstance(error, dict) else error), ok=False)
            else:
                content = (result or {}).get("content") if isinstance(result, dict) else result
                texts = [c.get("text", "") for c in content or [] if isinstance(c, dict)] \
                    if isinstance(content, list) else [str(content or "")]
                self._settle(item_id, "\n".join(texts), ok=item.get("status") == "completed")
        elif kind in ("webSearch", "imageView", "dynamicToolCall"):
            self._settle(item_id, json.dumps({k: v for k, v in item.items() if k not in ("id", "type")})[:4000],
                         ok=item.get("status") not in ("failed",))

    def _handle(self, method: str, params: dict[str, Any]) -> None:
        if params.get("threadId") and self.session and params["threadId"] != self.session:
            return  # another thread's (a subagent's): not this task's
        if method == "item/started":
            self._item_started(params.get("item") or {})
        elif method == "item/completed":
            self._item_completed(params.get("item") or {})
        elif method == "item/agentMessage/delta":
            if params.get("delta"):
                self._block_delta(str(params.get("itemId")), "text", str(params["delta"]))
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            if params.get("delta"):
                key = f"{params.get('itemId')}:{params.get('summaryIndex', params.get('contentIndex', 0))}"
                self._block_delta(key, "thinking", str(params["delta"]))
        elif method == "item/reasoning/summaryPartAdded":
            # A new section of the summary: the last one is complete.
            prefix = f"{params.get('itemId')}:"
            for key in [k for k in self._blocks if k.startswith(prefix)]:
                self._block_stop(key)
        elif method == "turn/plan/updated":
            self._plan(params)
        elif method == "thread/tokenUsage/updated":
            self._usage(params)
        elif method == "account/rateLimits/updated":
            self._rate_limits(params.get("rateLimits"))
        elif method == "error":
            error = params.get("error") or {}
            if params.get("willRetry"):
                self.notice(f"Codex: {error.get('message') or 'error'}; it is trying again.")
            else:
                self._error = error_text(error, self._limits)
        elif method in ("warning", "configWarning", "deprecationNotice", "guardianWarning"):
            text = params.get("message") or params.get("summary") or ""
            if text:
                log.info("codex %s: %s", method, text)
        elif method == "model/rerouted":
            self.notice(f"Codex moved this turn from {params.get('fromModel')} to {params.get('toModel')}.")
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            if self._turn and turn.get("id") and turn["id"] != self._turn:
                return
            status = turn.get("status")
            if status == "completed":
                self._finish_turn(error=None)
            elif status == "interrupted":
                self._finish_turn(error="interrupted")
            else:
                self._finish_turn(error=error_text(turn.get("error"), self._limits) if turn.get("error")
                                  else self._error or f"Codex turn {status or 'failed'}")

    def _finish_turn(self, *, error: str | None) -> None:
        if not self._in_turn:
            return
        self._in_turn = False
        for key in [*self._blocks]:
            self._block_stop(key)
        for call_id in [*self._open_tools]:
            self._settle(call_id, "" if error is None else error, ok=error is None)
        took = int((time.time() - self._turn_started) * 1000)
        self._messages.put_nowait(ResultMessage(
            subtype="error" if error else "success", duration_ms=took, duration_api_ms=took,
            is_error=error is not None, num_turns=self._steps, session_id=self.session or "",
            # Codex reports no dollars; the runner's estimate at OpenAI's list
            # prices (pricing.estimate) stands instead.
            total_cost_usd=None, usage=self._turn_usage or None,
            result=error if error and error != "interrupted" else "\n\n".join(self._response[-1:]),
        ))
