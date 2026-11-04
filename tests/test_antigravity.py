"""Antigravity as a task's agent (design/27): its calls as Claude tools, the
hook's decisions, and its stream as the Claude SDK messages the runner reads."""

from __future__ import annotations

import asyncio
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    ThinkingBlock,
    UserMessage,
)

from dex import antigravity
from dex.antigravity import AgyClient, claude_shape
from dex.usage import counts_for

CWD = Path("/work/task")


def test_calls_become_the_claude_tools_the_policy_knows() -> None:
    assert claude_shape("run_command", {"CommandLine": "ls -la", "toolSummary": "List"}, CWD) == (
        "Bash", {"command": "ls -la", "description": "List"})
    assert claude_shape("write_to_file", {"TargetFile": "/work/task/a.md", "CodeContent": "hi"}, CWD) == (
        "Write", {"file_path": "/work/task/a.md", "content": "hi"})
    # The stream names it write_file and gives a path only.
    assert claude_shape("write_file", {"Path": "b.md"}, CWD)[1]["file_path"] == "/work/task/b.md"
    assert claude_shape("replace_file_content", {
        "TargetFile": "/work/task/a.md", "TargetContent": "x", "ReplacementContent": "y"}, CWD) == (
        "Edit", {"file_path": "/work/task/a.md", "old_string": "x", "new_string": "y"})
    name, shaped = claude_shape("multi_replace_file_content", {"TargetFile": "/w/a", "ReplacementChunks": [
        {"TargetContent": "a", "ReplacementContent": "b"}]}, CWD)
    assert (name, shaped["edits"]) == ("MultiEdit", [{"old_string": "a", "new_string": "b"}])
    assert claude_shape("view_file", {"AbsolutePath": "/etc/hosts"}, CWD) == ("Read", {"file_path": "/etc/hosts"})
    assert claude_shape("grep_search", {"Query": "def", "SearchPath": "src"}, CWD) == (
        "Grep", {"pattern": "def", "path": "/work/task/src"})
    assert claude_shape("call_mcp_tool", {"ServerName": "dex-plugin_dex", "ToolName": "ask_user",
                                          "Arguments": {"question": "?"}}, CWD) == (
        "mcp__dex__ask_user", {"question": "?"})
    # Unknown to dex: a name no rule approves, so the operator is asked.
    assert claude_shape("browser_click_element", {}, CWD)[0] == "agy:browser_click_element"


def _client(decide) -> AgyClient:
    options = SimpleNamespace(cwd=str(CWD), model="agy:gemini-4-argon-high", can_use_tool=decide,
                              system_prompt={"append": "be brief"}, resume=None, mcp_servers={}, env={}, stderr=None)
    return AgyClient(options)


def test_the_hook_gets_the_policys_answer() -> None:
    seen = []

    async def decide(name, data, context):
        seen.append((name, data, context.tool_use_id))
        if "rm" in data.get("command", ""):
            return PermissionResultDeny(message="no deleting")
        if data.get("command") == "make":
            return PermissionResultAllow(updated_input={"command": "sandbox-exec -p … make"})
        return PermissionResultAllow()

    client = _client(decide)
    client.conversation = "c1"
    ask = lambda name, args: asyncio.run(client._gate({"toolCall": {"name": name, "args": args}, "stepIdx": 4}))  # noqa: E731
    assert ask("run_command", {"CommandLine": "ls"}) == {"decision": "allow", "reason": "dex policy"}
    assert ask("run_command", {"CommandLine": "rm -rf x"}) == {"decision": "deny", "reason": "no deleting"}
    # A command the policy confines runs confined.
    assert ask("run_command", {"CommandLine": "make"})["overwrite"] == {"CommandLine": "sandbox-exec -p … make"}
    # Following up on a running command is not a new decision.
    assert ask("command_status", {"CommandId": "1"})["decision"] == "allow"
    assert len(seen) == 3 and seen[0][2] == "agy-c1-4"


def _drain(client: AgyClient) -> list:
    out = []
    while not client._messages.empty():
        out.append(client._messages.get_nowait())
    return out


def test_the_stream_reads_as_claude_sdk_messages() -> None:
    client = _client(None)
    client._in_turn = True
    client._handle({"event": "init", "conversation_id": "c9", "init": {}})
    client._handle({"event": "step_update", "step_update": {"step_index": 1, "state": "ACTIVE",
                    "step_type": "agent_response", "text_delta": "Hel"}})
    client._handle({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE",
                    "step_type": "agent_response", "text_delta": "lo"}})
    client._handle({"event": "step_update", "step_update": {"step_index": 2, "state": "ACTIVE", "step_type": "tool",
                    "tool_name": "run_command", "tool_info": {"parameters": {"CommandLine": "ls"}}}})
    client._handle({"event": "step_update", "step_update": {"step_index": 2, "state": "ERROR", "step_type": "tool",
                    "tool_name": "run_command", "tool_info": {"error": {"message": "denied by hook"}}}})
    client._handle({"event": "result", "result": {"conversation_id": "c9", "status": "SUCCESS", "response": "Hello",
                    "usage": {"input_tokens": 10, "output_tokens": 2, "thinking_tokens": 1}}})
    messages = _drain(client)
    assert isinstance(messages[0], SystemMessage) and messages[0].data["session_id"] == "c9"
    text = "".join(m.event["delta"]["text"] for m in messages
                   if isinstance(m, StreamEvent) and m.event.get("type") == "content_block_delta")
    assert text == "Hello"
    tool = next(m for m in messages if isinstance(m, AssistantMessage)).content[0]
    assert (tool.name, tool.input, tool.id) == ("Bash", {"command": "ls"}, "agy-c9-2")
    outcome = next(m for m in messages if isinstance(m, UserMessage)).content[0]
    assert outcome.is_error and outcome.content == "denied by hook"
    result = messages[-1]
    assert isinstance(result, ResultMessage) and not result.is_error
    assert (result.result, result.total_cost_usd, result.usage["output_tokens"]) == ("Hello", None, 2)


def test_a_failed_turn_is_an_error_result() -> None:
    client = _client(None)
    client._in_turn = True
    client._handle({"event": "result", "result": {"status": "ERROR", "error": "quota exhausted",
                                                  "finished_reason": "error"}})
    (result,) = _drain(client)
    assert result.is_error and result.result == "quota exhausted"


def test_claude_limits_never_hold_antigravity_work() -> None:
    assert not counts_for("seven_day", "agy:gemini-4-argon-high")
    assert counts_for("seven_day", "claude-opus-5-5")


def test_the_picker_says_why_a_model_cannot_run(monkeypatch) -> None:
    monkeypatch.setattr(antigravity, "cli", lambda: None)
    choice = antigravity.choices()[0]
    assert choice["id"].startswith("agy:") and choice["available"] is False
    assert "install" in choice["unavailable"]


def test_install_writes_the_agent_and_its_plugin(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(antigravity, "HOME", tmp_path / "dex")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    antigravity.install()
    agent = (tmp_path / ".gemini" / "config" / "agents" / "dex-task.md").read_text()
    assert "name: dex-task" in agent and str(tmp_path / "dex" / "dex-plugin") in agent
    hooks = (tmp_path / "dex" / "dex-plugin" / "hooks.json").read_text()
    assert "PreToolUse" in hooks and "agy_hook.py" in hooks
    assert (tmp_path / "dex" / "dex-plugin" / "agy_mcp.py").exists()


def test_each_model_call_reports_its_tokens_at_the_list_price() -> None:
    client = _client(None)
    client._in_turn = True
    client.conversation = "c3"
    client._handle({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE",
                    "step_type": "agent_response", "usage": {"input_tokens": 900, "output_tokens": 40,
                                                             "thinking_tokens": 12, "cache_read_tokens": 5000}}})
    (call,) = [m for m in _drain(client) if isinstance(m, AssistantMessage)]
    assert call.content == [] and call.message_id == "agy-c3-1"
    assert call.usage["input_tokens"] == 900 and call.usage["cache_read_input_tokens"] == 5000
    from dex import pricing
    # `agy` reports no dollars: the family's public price, cache reads included.
    input_price, output_price, read_price, _ = pricing.subscription_rates(call.model)
    assert pricing.estimate(call.model, call.usage) == pytest.approx(
        (900 * input_price + 5000 * read_price + 40 * output_price) / 1e6)


def test_reasoning_comes_from_the_transcript(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    client = _client(None)
    client._in_turn = True
    client.conversation = "c7"
    logs = client._transcript()
    logs.parent.mkdir(parents=True)
    logs.write_text(json.dumps({"step_index": 0, "type": "USER_INPUT"}) + "\n"
                    + json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "thinking": "Count the primes first."}) + "\n")
    client._handle({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE",
                    "step_type": "agent_response", "usage": {"output_tokens": 3, "thinking_tokens": 40}}})
    thoughts = [b.thinking for m in _drain(client) if isinstance(m, AssistantMessage)
                for b in m.content if isinstance(b, ThinkingBlock)]
    assert thoughts == ["Count the primes first."]
    # Read once: a later step does not show it again.
    client._handle({"event": "step_update", "step_update": {"step_index": 3, "state": "DONE",
                    "step_type": "agent_response", "usage": {"thinking_tokens": 0}}})
    assert not [m for m in _drain(client) if isinstance(m, AssistantMessage) and m.content]


def test_a_step_waits_for_the_gate_to_show_the_whole_call() -> None:
    async def decide(name, data, context):
        return PermissionResultAllow()

    client = _client(decide)
    client._in_turn = True
    client.conversation = "c5"
    step = lambda state, **info: {"event": "step_update", "step_update": {  # noqa: E731
        "step_index": 4, "state": state, "step_type": "tool", "tool_name": "write_file",
        "tool_info": {"parameters": {"Path": "/work/task/a.md"}, **info}}}
    # The stream announces it with the path only: nothing is shown yet.
    client._handle(step("ACTIVE"))
    assert not [m for m in _drain(client) if isinstance(m, AssistantMessage)]
    # The gate has the content and the model's summary: the step shows with both.
    asyncio.run(client._gate({"stepIdx": 4, "toolCall": {"name": "write_to_file", "args": {
        "TargetFile": "/work/task/a.md", "CodeContent": "# Hi", "Description": "Write the intro"}}}))
    client._handle(step("DONE", output="ok"))
    messages = _drain(client)
    (tool,) = [m.content[0] for m in messages if isinstance(m, AssistantMessage)]
    assert tool.input == {"file_path": "/work/task/a.md", "content": "# Hi", "description": "Write the intro"}
    assert any(isinstance(m, UserMessage) for m in messages)


def test_a_step_the_gate_never_saw_still_shows_when_it_ends() -> None:
    client = _client(None)
    client._in_turn = True
    client._handle({"event": "step_update", "step_update": {"step_index": 2, "state": "ACTIVE", "step_type": "tool",
                    "tool_name": "read_file", "tool_info": {"parameters": {"Path": "/work/task/a.md"}}}})
    client._handle({"event": "step_update", "step_update": {"step_index": 2, "state": "DONE", "step_type": "tool",
                    "tool_name": "read_file", "tool_info": {"parameters": {"Path": "/work/task/a.md"}, "output": "1 line"}}})
    kinds = [type(m).__name__ for m in _drain(client)]
    assert kinds == ["AssistantMessage", "UserMessage"]


def test_edit_file_and_summaries() -> None:
    assert claude_shape("edit_file", {"Path": "b.txt", "Target": "WORLD", "Replacement": "THERE"}, CWD) == (
        "Edit", {"file_path": "/work/task/b.txt", "old_string": "WORLD", "new_string": "THERE"})
    assert claude_shape("run_command", {"CommandLine": "wc -c a", "toolSummary": "Count bytes"}, CWD)[1][
        "description"] == "Count bytes"
