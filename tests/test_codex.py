"""Codex on the ChatGPT plan as a task's agent (design/29): its calls as Claude
tools, the gate's decisions, its app-server notifications as the Claude SDK
messages the runner reads, its tokens and rate-limit windows, and its prices."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from dex import codex, pricing
from dex.codex import CodexClient, claude_calls, claude_usage, parse_patch
from dex.usage import closest_limit, counts_for, provider_window

CWD = Path("/work/task")
THREAD = "01a1e0f1-07fd-7f31-95f2-ee7299cf249b"
TURN = "01a1e0f1-0834-7041-b9cc-b1bc90dbde88"

MODELS = [
    {"id": "gpt-reserve", "name": "GPT-Reserve", "efforts": ["low", "medium", "high"], "default": "medium",
     "hidden": True},
    {"id": "gpt-5.6-terra", "name": "GPT-5.6-Terra", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
     "default": "medium", "hidden": False},
    {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
     "default": "low", "hidden": False},
    {"id": "gpt-5.6-luna", "name": "GPT-5.6-Luna", "efforts": ["low", "medium", "high", "xhigh", "max"],
     "default": "medium", "hidden": False},
    {"id": "codex-auto-review", "name": "Codex Auto Review", "efforts": ["low"], "default": "low", "hidden": True},
]


@pytest.fixture(autouse=True)
def _models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex, "_models_cache", (time.time(), MODELS, {"type": "chatgpt", "planType": "prolite"}))
    monkeypatch.setattr(codex, "cli", lambda: Path("/bin/codex"))


# ------------------------------------------------------------------ choosing


def test_every_model_at_every_effort_is_offered_sol_at_medium_first() -> None:
    picked = codex.choices()
    assert picked[0]["id"] == "codex:gpt-5.6-sol#medium"
    assert picked[0]["label"] == "GPT-5.6-Sol · medium"
    assert {c["provider"] for c in picked} == {"codex"}
    assert all(c["available"] for c in picked)
    ids = [c["id"] for c in picked]
    # Every effort each model offers, Codex's own reviewer left out.
    assert len(ids) == 6 + 6 + 5 + 3
    assert "codex:gpt-5.6-luna#max" in ids and "codex:gpt-5.6-terra#ultra" in ids
    assert "codex:gpt-reserve#high" in ids
    assert not [i for i in ids if "auto-review" in i]


def test_a_model_that_cannot_run_says_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex, "_models_cache", (time.time(), MODELS, {"type": "apiKey"}))
    picked = codex.choices()
    assert not any(c["available"] for c in picked)
    assert "codex login" in picked[0]["unavailable"]


def test_effort_comes_from_the_model_id() -> None:
    assert codex.split("codex:gpt-5.6-sol#high") == ("gpt-5.6-sol", "high")
    assert codex.effort_for("codex:gpt-5.6-sol#high") == "high"
    # Unnamed: medium, whatever Codex's own default for the model.
    assert codex.effort_for("codex:gpt-5.6-sol") == "medium"
    # Not offered: the nearest below.
    assert codex.effort_for("codex:gpt-5.6-luna#ultra") == "max"
    assert codex.effort_for("codex:gpt-reserve#minimal") == "low"


def test_api_keys_never_reach_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME", "CODEX_API_KEY"):
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("HOME_THING", "kept")
    env = codex._env()
    assert "HOME_THING" in env
    assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME", "CODEX_API_KEY"} & env.keys()


def test_the_operators_own_setup_stays_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config.toml").write_text('[mcp_servers.node_repl]\ncommand = "x"\n[mcp_servers."odd name"]\nurl="y"\n')
    monkeypatch.setenv("DEX_CODEX_USER_HOME", str(tmp_path))
    flags = codex._flags({"dex": "http://127.0.0.1:5/t/mcp/dex"})
    joined = " ".join(flags)
    assert "mcp_servers.node_repl.enabled=false" in joined
    assert 'mcp_servers."odd name".enabled=false' in joined
    assert 'mcp_servers.dex.url="http://127.0.0.1:5/t/mcp/dex"' in joined
    assert "features.plugins=false" in joined and "notify=[]" in joined and "project_doc_max_bytes=0" in joined
    # The hook is named the same way every time: Codex trusts it by that name.
    assert codex.hook_command() in flags[1] and "timeout=86400" in flags[1]


def test_a_thread_is_resumable_when_codex_keeps_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEX_CODEX_USER_HOME", str(tmp_path))
    assert not codex.has_thread(THREAD)
    day = tmp_path / "sessions" / "2026" / "11" / "15"
    day.mkdir(parents=True)
    (day / f"rollout-2026-11-15T12-40-55-{THREAD}.jsonl").write_text("{}")
    assert codex.has_thread(THREAD)


# --------------------------------------------------------------- tool shapes

PATCH = """*** Begin Patch
*** Add File: notes/new.md
+# Title
+body
*** Update File: /work/task/a.py
@@ def f():
-    return 1
+    return 2
@@
 x = 1
-y = 2
+y = 3
*** Delete File: old.txt
*** Update File: b.txt
*** Move to: c.txt
@@
-b
+c
*** End Patch"""


def test_a_patch_is_read_as_the_files_it_touches() -> None:
    files = parse_patch(PATCH, CWD)
    assert [(f["op"], f["path"]) for f in files] == [
        ("add", "/work/task/notes/new.md"), ("update", "/work/task/a.py"),
        ("delete", "/work/task/old.txt"), ("update", "/work/task/b.txt")]
    assert files[0]["content"] == "# Title\nbody\n"
    assert files[1]["hunks"] == [("    return 1", "    return 2"), ("x = 1\ny = 2", "x = 1\ny = 3")]
    assert files[3]["move_to"] == "/work/task/c.txt"


def test_calls_become_the_claude_tools_the_policy_knows() -> None:
    assert claude_calls("Bash", {"command": "ls"}, CWD) == [("Bash", {"command": "ls"})]
    assert claude_calls("Bash", {"command": "ls", "workdir": "src"}, CWD) == [
        ("Bash", {"command": "ls", "cwd": "/work/task/src"})]
    patched = claude_calls("apply_patch", {"command": PATCH}, CWD)
    assert [name for name, _ in patched] == ["Write", "MultiEdit", "Bash", "MultiEdit", "Bash"]
    assert patched[0][1] == {"file_path": "/work/task/notes/new.md", "content": "# Title\nbody\n"}
    assert patched[1][1]["edits"][1] == {"old_string": "x = 1\ny = 2", "new_string": "x = 1\ny = 3"}
    assert patched[2][1]["command"] == "rm -- /work/task/old.txt"
    assert patched[4][1]["command"] == "mv -- /work/task/b.txt /work/task/c.txt"
    # Codex already names MCP tools as Claude does.
    assert claude_calls("mcp__dex__ask_user", {"question": "?"}, CWD) == [("mcp__dex__ask_user", {"question": "?"})]
    assert claude_calls("view_image", {"path": "p.png"}, CWD) == [("Read", {"file_path": "/work/task/p.png"})]
    # Its own plan runs unasked; anything else unknown goes to the operator.
    assert claude_calls("update_plan", {"plan": []}, CWD) == []
    assert claude_calls("spawn_agent", {}, CWD)[0][0] == "codex:spawn_agent"


def test_a_command_is_shown_as_the_model_wrote_it() -> None:
    assert codex._unwrap("/bin/zsh -lc 'ls -la && echo hi > a.txt'") == "ls -la && echo hi > a.txt"
    assert codex._unwrap("/bin/bash -c \"sed -n '1p' c.txt\"") == "sed -n '1p' c.txt"
    assert codex._unwrap("python x.py") == "python x.py"


# --------------------------------------------------------- usage and limits


def test_tokens_are_counted_as_claude_counts_them() -> None:
    last = {"totalTokens": 10871, "inputTokens": 10702, "cachedInputTokens": 10240, "cacheWriteInputTokens": 0,
            "outputTokens": 169, "reasoningOutputTokens": 40}
    usage = claude_usage(last)
    # Codex counts cached input inside input, reasoning inside output.
    assert usage == {"input_tokens": 462, "output_tokens": 169, "cache_read_input_tokens": 10240,
                     "cache_creation_input_tokens": 0, "output_tokens_details": {"thinking_tokens": 40}}
    assert pricing.tokens(usage) == {"input_tokens": 462, "cache_read_tokens": 10240, "cache_write_tokens": 0,
                                     "output_tokens": 169, "thinking_tokens": 40}
    assert claude_usage(None) is None


def test_tokens_are_priced_at_openais_list_prices() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    assert pricing.subscription_rates("codex:gpt-5.6-sol#medium", "2026-11-15") == (4.0, 20.0, 0.40, 0.0)
    assert pricing.subscription_rates("codex:gpt-5.6-sol#medium", "2026-12-01") == (5.0, 30.0, 0.50, 0.0)
    assert pricing.estimate("codex:gpt-5.6-luna#low", usage) == pytest.approx(0.20 + 1.20 + 0.02)
    assert pricing.estimate("codex:gpt-5.6-terra#high", usage) == pytest.approx(2.0 + 12.0 + 0.20)
    # No price known: dex's default, rather than nothing.
    assert pricing.estimate("codex:gpt-reserve#low", {"input_tokens": 1_000_000}) == pytest.approx(5.0)


SNAPSHOT = {"limitId": "codex", "limitName": None,
            "primary": {"usedPercent": 42, "windowDurationMins": 10080, "resetsAt": 1795379367},
            "secondary": {"usedPercent": 96, "windowDurationMins": 300, "resetsAt": 1794790000},
            "credits": {"hasCredits": False, "unlimited": False, "balance": "0"}, "planType": "prolite",
            "rateLimitReachedType": None}


def test_rate_limit_windows_become_dex_readings() -> None:
    found = codex.readings(SNAPSHOT)
    assert [(r["rateLimitType"], r["utilization"], r["status"]) for r in found] == [
        ("codex_weekly", 0.42, "allowed"), ("codex_5h", 0.96, "allowed")]
    spent = codex.readings({**SNAPSHOT, "rateLimitReachedType": "rate_limit_reached"})
    assert spent[0]["status"] == "rejected"
    assert codex.readings({"limitId": "codex", "primary": None}) == []
    assert codex.window_name({"limitId": "sol", "primary": {"windowDurationMins": 60}}, "primary") == "codex_sol_60m"


def test_codex_windows_hold_only_codex_work() -> None:
    model = "codex:gpt-5.6-sol#medium"
    assert provider_window(model) == "codex"
    assert counts_for("codex_weekly", model) and counts_for("codex_5h", model) and counts_for("codex", model)
    assert not counts_for("five_hour", model) and not counts_for("seven_day", model)
    assert not counts_for("codex_weekly", "claude-opus-5-5")
    assert not counts_for("codex_weekly", "oc:qwen3.8-flash")
    now = 1794780000
    snapshot = {r["rateLimitType"]: {**r, "seenAt": now} for r in codex.readings(SNAPSHOT)}
    snapshot["seven_day"] = {"utilization": 0.99, "resetsAt": now + 3600}
    assert closest_limit(snapshot, now=now, model=model) == ("codex_5h", 0.96)
    assert closest_limit(snapshot, now=now, model="claude-opus-5-5") == ("seven_day", 0.99)


def test_failed_turns_read_as_the_runner_reads_failures() -> None:
    from dex.runner import CONNECTION_ERROR
    from dex.usage import is_usage_limit_error, resets_in

    limits = [{"rateLimitType": "codex_weekly", "status": "rejected", "resetsAt": time.time() + 7200}]
    spent = codex.error_text({"message": "You've hit your usage limit.", "codexErrorInfo": "usageLimitExceeded"},
                             limits)
    assert is_usage_limit_error(spent)
    assert 7100 < (resets_in(spent) or 0) <= 7200
    dropped = codex.error_text({"message": "stream disconnected before completion",
                                "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": None}}})
    assert CONNECTION_ERROR.search(dropped)
    busy = codex.error_text({"message": "Selected model is at capacity.", "codexErrorInfo": "serverOverloaded"})
    assert busy == "Selected model is at capacity." and not CONNECTION_ERROR.search(busy)


# ------------------------------------------------------------------- the hook


def _hook():
    path = Path(codex.__file__).parent / "tools" / "codex_hook.py"
    spec = importlib.util.spec_from_file_location("codex_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_hook_answers_in_codexs_words() -> None:
    hook = _hook()
    assert hook._answer({"decision": "allow"}) == {}
    assert hook._answer({"decision": "deny", "reason": "no"}) == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "no"}}
    assert hook._answer({"decision": "allow", "input": {"command": "x"}})["hookSpecificOutput"]["updatedInput"] == {
        "command": "x"}


def test_a_hook_that_cannot_reach_dex_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _hook()
    monkeypatch.setenv("DEX_CODEX_GATE", "http://127.0.0.1:9/nobody")
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"tool_name": "Bash"}'))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(hook.os, "killpg", lambda *_: None)
    hook.main()
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------- the client


def _client(decide, *, status=None) -> CodexClient:
    options = SimpleNamespace(cwd=str(CWD), model="codex:gpt-5.6-sol#high", effort="max", can_use_tool=decide,
                              system_prompt={"append": "be brief"}, resume=None,
                              mcp_servers={"dex": {"type": "sdk", "instance": object()}}, env={}, stderr=None)
    client = CodexClient(options, status=status)
    client.session = THREAD
    client._turn = TURN
    return client


def _drain(client: CodexClient) -> list:
    out = []
    while not client._messages.empty():
        out.append(client._messages.get_nowait())
    return out


async def _allow(_name, data, _ctx):
    return PermissionResultAllow(updated_input=data)


def test_the_client_runs_at_the_models_effort_with_dexs_instructions() -> None:
    client = _client(_allow)
    assert (client.model, client.effort) == ("gpt-5.6-sol", "high")
    assert client.system == "be brief"


def test_the_gate_gets_the_policys_answer_and_shows_each_call() -> None:
    seen = []

    async def decide(name, data, _ctx):
        seen.append(name)
        if "rm" in data.get("command", ""):
            return PermissionResultDeny(message="not here")
        if data.get("command") == "make":
            return PermissionResultAllow(updated_input={"command": "sandbox make"})
        if data.get("file_path") == "/work/task/secret.txt":
            return PermissionResultDeny(message="outside the package")
        return PermissionResultAllow(updated_input=data)

    client = _client(decide)
    run = asyncio.run
    gate = lambda tool, call_id, args: run(client._gate(  # noqa: E731
        {"tool_name": tool, "tool_use_id": call_id, "tool_input": args, "session_id": THREAD}))
    assert gate("Bash", "exec-1", {"command": "ls"}) == {"decision": "allow"}
    assert gate("Bash", "exec-2", {"command": "rm -rf /"}) == {"decision": "deny", "reason": "not here"}
    assert gate("Bash", "exec-3", {"command": "make"}) == {"decision": "allow", "input": {"command": "sandbox make"}}
    # A patch is allowed only when every file it touches is.
    two = "*** Begin Patch\n*** Add File: ok.txt\n+a\n*** Add File: secret.txt\n+b\n*** End Patch"
    assert gate("apply_patch", "exec-4", {"command": two}) == {"decision": "deny", "reason": "outside the package"}
    assert gate("update_plan", "call-5", {"plan": []}) == {"decision": "allow"}
    assert seen == ["Bash", "Bash", "Bash", "Write", "Write"]
    out = _drain(client)
    shown = [b for m in out if isinstance(m, AssistantMessage) for b in m.content]
    assert [(b.id, b.name) for b in shown] == [
        ("exec-1", "Bash"), ("exec-2", "Bash"), ("exec-3", "Bash"), ("exec-4", "Write"), ("exec-4#2", "Write")]
    # A refused call never becomes an item: its refusal is its outcome.
    denied = [b for m in out if isinstance(m, UserMessage) for b in m.content]
    assert [(b.tool_use_id, b.is_error) for b in denied] == [("exec-2", True), ("exec-4", True), ("exec-4#2", True)]
    assert "outside the package" in denied[-1].content


def _note(method: str, **params) -> tuple[str, dict]:
    return method, {"threadId": THREAD, "turnId": TURN, **params}


def test_notifications_become_sdk_messages() -> None:
    lines = []
    client = _client(_allow, status=lines.append)
    client._in_turn = True
    client._turn_started = time.time()
    asyncio.run(client._gate({"tool_name": "Bash", "tool_use_id": "exec-1", "tool_input": {"command": "ls -la"}}))
    command = {"type": "commandExecution", "id": "exec-1", "command": "/bin/zsh -lc 'ls -la'", "cwd": str(CWD),
               "status": "inProgress", "commandActions": [], "aggregatedOutput": None, "exitCode": None}
    usage = {"total": {"totalTokens": 12051, "inputTokens": 11925, "cachedInputTokens": 0,
                       "cacheWriteInputTokens": 0, "outputTokens": 126, "reasoningOutputTokens": 20},
             "last": {"totalTokens": 12051, "inputTokens": 11925, "cachedInputTokens": 0,
                      "cacheWriteInputTokens": 0, "outputTokens": 126, "reasoningOutputTokens": 20}}
    notes = [
        _note("item/started", item={"type": "agentMessage", "id": "msg_1", "text": "", "phase": "commentary"}),
        _note("item/agentMessage/delta", itemId="msg_1", delta="I’ll list the files"),
        _note("item/agentMessage/delta", itemId="msg_1", delta=" first."),
        _note("item/completed", item={"type": "agentMessage", "id": "msg_1", "text": "I’ll list the files first.",
                                      "phase": "commentary"}),
        _note("item/started", item={"type": "reasoning", "id": "rs_1", "summary": [], "content": []}),
        _note("item/reasoning/summaryPartAdded", itemId="rs_1", summaryIndex=0),
        _note("item/reasoning/summaryTextDelta", itemId="rs_1", summaryIndex=0, delta="**Checking the files**"),
        _note("item/completed", item={"type": "reasoning", "id": "rs_1", "summary": ["**Checking the files**"],
                                      "content": []}),
        _note("item/started", item=command),
        _note("item/completed", item={**command, "status": "completed", "aggregatedOutput": "a.txt\n",
                                      "exitCode": 0}),
        # Never at the gate: hosted by OpenAI. Shown from its item.
        _note("item/started", item={"type": "webSearch", "id": "ws_1", "query": "codex hooks"}),
        _note("item/completed", item={"type": "webSearch", "id": "ws_1", "query": "codex hooks"}),
        _note("turn/plan/updated", explanation=None, plan=[
            {"step": "List files", "status": "completed"}, {"step": "Write notes", "status": "inProgress"}]),
        _note("thread/tokenUsage/updated", tokenUsage=usage),
        _note("thread/tokenUsage/updated", tokenUsage=usage),  # Codex repeats a reading
        ("account/rateLimits/updated", {"rateLimits": SNAPSHOT}),
        _note("item/started", item={"type": "reasoning", "id": "rs_2", "summary": [], "content": []}),
        _note("item/completed", item={"type": "reasoning", "id": "rs_2", "summary": ["Whole, not streamed"],
                                      "content": []}),
        _note("item/started", item={"type": "agentMessage", "id": "msg_2", "text": "", "phase": "final_answer"}),
        _note("item/agentMessage/delta", itemId="msg_2", delta="Done."),
        _note("item/completed", item={"type": "agentMessage", "id": "msg_2", "text": "Done.",
                                      "phase": "final_answer"}),
        # Another thread's (a subagent's): not this task's.
        ("item/agentMessage/delta", {"threadId": "other", "turnId": "t", "itemId": "x", "delta": "nope"}),
        _note("turn/completed", turn={"id": TURN, "status": "completed", "error": None, "items": []}),
    ]
    for method, params in notes:
        client._handle(method, params)
    out = _drain(client)

    # Commentary is the task's one-line status as well as text.
    assert lines == ["I’ll list the files first."]
    deltas = [m.event for m in out if isinstance(m, StreamEvent) and m.event["type"] == "content_block_delta"]
    assert [d["delta"].get("text") or d["delta"].get("thinking") for d in deltas] == [
        "I’ll list the files", " first.", "**Checking the files**", "Done."]
    starts = [m.event["content_block"]["type"] for m in out
              if isinstance(m, StreamEvent) and m.event["type"] == "content_block_start"]
    stops = [m for m in out if isinstance(m, StreamEvent) and m.event["type"] == "content_block_stop"]
    assert starts == ["text", "thinking", "text"] and len(stops) == 3
    thinking = [b.thinking for m in out if isinstance(m, AssistantMessage) for b in m.content
                if isinstance(b, ThinkingBlock)]
    assert thinking == ["Whole, not streamed"]
    tools = [b for m in out if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolUseBlock)]
    assert [(b.id, b.name) for b in tools] == [("exec-1", "Bash"), ("ws_1", "WebSearch"),
                                              (f"plan-{TURN}-1", "TodoWrite")]
    assert tools[2].input["todos"][1] == {"content": "Write notes", "activeForm": "Write notes",
                                          "status": "in_progress"}
    results = {b.tool_use_id: b for m in out if isinstance(m, UserMessage) for b in m.content
               if isinstance(b, ToolResultBlock)}
    assert results["exec-1"].content == "a.txt\n" and not results["exec-1"].is_error
    assert set(results) == {"exec-1", "ws_1", f"plan-{TURN}-1"}
    usages = [m for m in out if isinstance(m, AssistantMessage) and m.usage]
    assert len(usages) == 1 and usages[0].usage["output_tokens_details"]["thinking_tokens"] == 20
    limits = [m for m in out if isinstance(m, RateLimitEvent)]
    assert [m.rate_limit_info.raw["rateLimitType"] for m in limits] == ["codex_weekly", "codex_5h"]
    assert limits[1].rate_limit_info.utilization == 0.96
    result = out[-1]
    assert isinstance(result, ResultMessage) and not result.is_error
    assert result.result == "Done." and result.session_id == THREAD and result.num_turns == 1
    assert result.total_cost_usd is None and result.usage["input_tokens"] == 11925


def test_a_failed_command_and_a_failed_turn() -> None:
    client = _client(_allow)
    client._in_turn = True
    client._turn_started = time.time()
    command = {"type": "commandExecution", "id": "exec-9", "command": "false", "status": "inProgress",
               "aggregatedOutput": None, "exitCode": None}
    client._handle(*_note("item/started", item=command))
    client._handle(*_note("item/completed", item={**command, "status": "failed", "aggregatedOutput": "",
                                                  "exitCode": 1}))
    client._handle(*_note("error", willRetry=True, error={"message": "Reconnecting... 1/5"}))
    client._handle(*_note("turn/completed", turn={"id": TURN, "status": "failed", "error": {
        "message": "You've hit your usage limit.", "codexErrorInfo": "usageLimitExceeded"}}))
    out = _drain(client)
    failed = [b for m in out if isinstance(m, UserMessage) for b in m.content][0]
    assert failed.is_error and "exit code 1" in failed.content
    result = out[-1]
    assert result.is_error and result.result.startswith("usage limit reached")


def test_an_interrupted_turn_ends_as_the_runner_expects() -> None:
    client = _client(_allow)
    client._in_turn = True
    asyncio.run(client._gate({"tool_name": "Bash", "tool_use_id": "exec-1", "tool_input": {"command": "sleep 9"}}))
    client._handle(*_note("turn/completed", turn={"id": TURN, "status": "interrupted", "error": None}))
    out = _drain(client)
    # The call it cut short is settled, and the turn's result says so.
    assert [b.tool_use_id for m in out if isinstance(m, UserMessage) for b in m.content] == ["exec-1"]
    assert out[-1].is_error and out[-1].subtype == "error"


def test_the_session_is_announced_once() -> None:
    client = _client(_allow)
    client._announced = False

    async def opened():
        async def call(method, params, timeout=0):
            assert method == "thread/start" and params["developerInstructions"] == "be brief"
            assert params["approvalPolicy"] == "never" and params["model"] == "gpt-5.6-sol"
            return {"thread": {"id": THREAD}}
        client._call = call
        client.session = None
        await client._open_thread()

    asyncio.run(opened())
    announced = [m for m in _drain(client) if isinstance(m, SystemMessage)]
    assert [(m.data["session_id"], m.data["provider"]) for m in announced] == [(THREAD, "codex")]
