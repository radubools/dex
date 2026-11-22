"""OpenCode on the Go plan as a task's agent (design/28): its calls as Claude
tools, the gate's decisions, its events as the Claude SDK messages the runner
reads, and its tokens priced at the plan's rates."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    StreamEvent,
    SystemMessage,
    ThinkingBlock,
    ToolUseBlock,
    UserMessage,
)

from dex import opencode, pricing
from dex.opencode import OpencodeClient, claude_shape
from dex.usage import counts_for

CWD = Path("/work/task")

MODELS = [
    {"id": "qwen3.8-flash", "name": "Qwen3.8 Flash", "variants": ["none", "low", "medium", "xhigh"],
     "cost": [{"input": 0.15, "output": 0.47, "cache": {"read": 0.016, "write": 0.2}}]},
    {"id": "qwen3.7-plus", "name": "Qwen3.7 Plus", "variants": ["none", "high", "max"],
     "cost": [{"input": 0.4, "output": 1.6, "cache": {"read": 0.04, "write": 0.5}},
              {"tier": {"type": "context", "size": 256000}, "input": 1.2, "output": 4.8,
               "cache": {"read": 0.12, "write": 1.5}}]},
    {"id": "mimo-v2.5", "name": "MiMo V2.5", "variants": [], "cost": []},
    {"id": "beam-free", "name": "Beam Free", "variants": [],
     "cost": [{"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}]},
]


@pytest.fixture(autouse=True)
def _models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(opencode, "_models_cache", (opencode.time.time(), MODELS))
    # Never the operator's own list of local models.
    monkeypatch.setattr(opencode, "LOCAL_FILE", tmp_path / "no-local-models.json")


def test_calls_become_the_claude_tools_the_policy_knows() -> None:
    assert claude_shape("shell", {"command": "ls", "workdir": "src"}, CWD) == (
        "Bash", {"command": "ls", "cwd": "/work/task/src"})
    assert claude_shape("write", {"path": "a.md", "content": "hi"}, CWD) == (
        "Write", {"file_path": "/work/task/a.md", "content": "hi"})
    assert claude_shape("edit", {"path": "/w/a", "oldString": "x", "newString": "y", "replaceAll": True}, CWD) == (
        "Edit", {"file_path": "/w/a", "old_string": "x", "new_string": "y", "replace_all": True})
    assert claude_shape("read", {"path": "/etc/hosts"}, CWD) == ("Read", {"file_path": "/etc/hosts"})
    assert claude_shape("grep", {"pattern": "def", "include": "*.py"}, CWD) == (
        "Grep", {"pattern": "def", "path": "/work/task", "glob": "*.py"})
    assert claude_shape("webfetch", {"url": "https://x.dev"}, CWD) == ("WebFetch", {"url": "https://x.dev", "prompt": ""})
    # An MCP tool is <server>_<tool>, told apart by the servers dex serves.
    assert claude_shape("dex_ask_user", {"question": "?"}, CWD, ("dex",)) == ("mcp__dex__ask_user", {"question": "?"})
    assert claude_shape("lsp_hover", {}, CWD, ("dex",))[0] == "oc:lsp_hover"


def test_only_the_go_plan_is_offered_preferred_first() -> None:
    picked = opencode.choices()
    assert [c["id"] for c in picked][:1] == ["oc:qwen3.8-flash"]
    assert {c["provider"] for c in picked} == {"opencode"}


def test_effort_picks_the_nearest_variant() -> None:
    assert opencode.variant("oc:qwen3.8-flash", "medium") == "medium"
    assert opencode.variant("oc:qwen3.8-flash", "high") == "medium"
    assert opencode.variant("oc:qwen3.8-flash", "max") == "xhigh"
    assert opencode.variant("oc:qwen3.7-plus", "low") == "none"
    assert opencode.variant("oc:mimo-v2.5", "high") is None


def test_tokens_are_priced_at_the_plans_rates() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
    assert pricing.estimate("oc:qwen3.8-flash", usage) == pytest.approx(0.15 + 0.47 + 0.016 + 0.2)
    # A prompt past a tier's size is charged at that tier.
    assert pricing.estimate("oc:qwen3.7-plus", usage) == pytest.approx(1.2 + 4.8 + 0.12 + 1.5)
    small = {"input_tokens": 1000, "output_tokens": 1000}
    assert pricing.estimate("oc:qwen3.7-plus", small) == pytest.approx((0.4 + 1.6) * 1000 / 1e6)
    assert not counts_for("five_hour", "oc:qwen3.8-flash")


def test_without_a_price_of_their_own_models_take_the_public_list_price() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    # Antigravity reports no dollars: its family's price, effort dropped,
    # with the date a price changes.
    assert pricing.subscription_rates("agy:gemini-4-argon-high", "2026-11-03") == (2.0, 10.0, 0.10, 0.0)
    assert pricing.subscription_rates("agy:gemini-4-argon-high", "2026-12-01") == (4.0, 20.0, 0.20, 0.0)
    assert pricing.subscription_rates("agy:claude-sonnet-5-5-medium")[:2] == (2.0, 10.0)
    assert pricing.subscription_rates("agy:mystery-1") is None
    assert pricing.estimate("agy:gpt-oss-120b-medium", usage) == pytest.approx(0.09 + 0.36)
    # OpenCode gives no price for this one: the family's.
    assert pricing.estimate("oc:gemini-4-argon", usage) > 0
    # Free on the plan, and no public price: free.
    assert pricing.estimate("oc:beam-free", usage) == 0
    # No price anywhere: dex's default, rather than nothing.
    assert pricing.estimate("oc:mimo-v2.5", usage) == pytest.approx(5.0 + 25.0)



def test_api_keys_never_reach_opencode(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HF_TOKEN", "OPENCODE_CONFIG"):
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("HOME_THING", "kept")
    env = opencode._env()
    assert "HOME_THING" in env
    assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HF_TOKEN", "OPENCODE_CONFIG"} & env.keys()


def test_install_writes_a_primary_agent_and_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opencode, "HOME", tmp_path)
    warmed = []
    monkeypatch.setattr(opencode, "_warm", lambda: warmed.append(1))
    opencode.install()
    opencode.install()
    agent = (tmp_path / "modes" / "dex-task.md").read_text()
    assert "mode: primary" in agent and "question: deny" in agent and "patch" not in agent
    assert "DEX_OC_GATE" in (tmp_path / "plugins" / "dex-gate.js").read_text()
    assert warmed == [1]  # loaded once after it changed, not on every run


def _client(decide) -> OpencodeClient:
    options = SimpleNamespace(cwd=str(CWD), model="oc:qwen3.8-flash", effort="high", can_use_tool=decide,
                              system_prompt={"append": "be brief"}, resume=None,
                              mcp_servers={"dex": {"type": "sdk", "instance": object()}}, env={}, stderr=None)
    return OpencodeClient(options)


def _drain(client: OpencodeClient) -> list:
    out = []
    while not client._messages.empty():
        out.append(client._messages.get_nowait())
    return out


def test_the_gate_gets_the_policys_answer_and_shows_the_call() -> None:
    seen = []

    async def decide(name, data, _ctx):
        seen.append((name, data))
        if "rm" in data.get("command", ""):
            return PermissionResultDeny(message="not here")
        if data.get("command") == "make":
            return PermissionResultAllow(updated_input={"command": "sandbox make"})
        return PermissionResultAllow(updated_input=data)

    client = _client(decide)
    run = asyncio.run
    assert run(client._gate({"tool": "shell", "id": "t1", "input": {"command": "ls"}}))["decision"] == "allow"
    assert run(client._gate({"tool": "shell", "id": "t2", "input": {"command": "rm -rf /"}})) == {
        "decision": "deny", "reason": "not here"}
    rewritten = run(client._gate({"tool": "shell", "id": "t3", "input": {"command": "make", "timeout": 5}}))
    assert rewritten["input"] == {"command": "sandbox make", "timeout": 5}
    # Not task work: refused without asking anybody.
    assert run(client._gate({"tool": "subagent", "id": "t4", "input": {}}))["decision"] == "deny"
    assert run(client._gate({"tool": "patch", "id": "t5", "input": {}}))["decision"] == "deny"
    # The code tool runs unasked: each tool it calls comes to the gate.
    assert run(client._gate({"tool": "execute", "id": "t6", "input": {"code": "..."}}))["decision"] == "allow"
    assert run(client._gate({"tool": "dex_log_idea", "id": "t6", "input": {"idea": "a"}}))["decision"] == "allow"
    assert run(client._gate({"tool": "dex_log_idea", "id": "t6", "input": {"idea": "b"}}))["decision"] == "allow"
    assert [name for name, _ in seen] == ["Bash", "Bash", "Bash", "mcp__dex__log_idea", "mcp__dex__log_idea"]
    shown = [b for m in _drain(client) if isinstance(m, AssistantMessage) for b in m.content]
    assert [(b.id, b.name) for b in shown] == [
        ("t1", "Bash"), ("t2", "Bash"), ("t3", "Bash"), ("t6", "mcp__dex__log_idea"), ("t6.1", "mcp__dex__log_idea")]


def test_events_become_sdk_messages() -> None:
    async def decide(_name, data, _ctx):
        return PermissionResultAllow(updated_input=data)

    client = _client(decide)
    client._in_turn = True
    asyncio.run(client._gate({"tool": "shell", "id": "toolu_1", "input": {"command": "ls"}}))
    asyncio.run(client._gate({"tool": "execute", "id": "toolu_2", "input": {}}))
    asyncio.run(client._gate({"tool": "dex_log_idea", "id": "toolu_2", "input": {"idea": "x"}}))
    events = [
        {"type": "step_start", "sessionID": "ses_1", "part": {}},
        {"type": "reasoning", "sessionID": "ses_1", "part": {"text": "Count it. "}},
        {"type": "tool_use", "sessionID": "ses_1", "part": {"id": "toolu_1", "tool": "shell", "state": {
            "status": "completed", "input": {"command": "ls"}, "output": "a.txt"}}},
        {"type": "tool_use", "sessionID": "ses_1", "part": {"id": "toolu_2", "tool": "execute", "state": {
            "status": "completed", "input": {}, "output": "logged"}}},
        {"type": "tool_use", "sessionID": "ses_1", "part": {"id": "toolu_3", "tool": "read", "state": {
            "status": "error", "input": {"path": "x"}, "error": "no such file"}}},
        {"type": "step_finish", "sessionID": "ses_1", "part": {"messageID": "msg_1", "tokens": {
            "input": 6, "output": 50, "reasoning": 10, "cache": {"read": 100, "write": 20}}}},
        {"type": "step_finish", "sessionID": "ses_1", "part": {"messageID": "msg_1", "tokens": {"input": 6}}},
        {"type": "text", "sessionID": "ses_1", "part": {"text": "Done."}},
    ]
    for event in events:
        client._handle(event)
    client._finish_turn(error=None)
    out = _drain(client)
    assert client.session == "ses_1"
    assert [m.data["provider"] for m in out if isinstance(m, SystemMessage)] == ["opencode"]
    thinking = [b.thinking for m in out if isinstance(m, AssistantMessage) for b in m.content
                if isinstance(b, ThinkingBlock)]
    assert thinking == ["Count it."]
    results = {b.tool_use_id: (b.content, b.is_error) for m in out if isinstance(m, UserMessage) for b in m.content}
    assert results == {"toolu_1": ("a.txt", False), "toolu_2": ("logged", False), "toolu_3": ("no such file", True)}
    # A call the gate never saw is still shown, with its outcome.
    assert any(isinstance(b, ToolUseBlock) and b.id == "toolu_3" and b.name == "Read"
               for m in out if isinstance(m, AssistantMessage) for b in m.content)
    usages = [m for m in out if isinstance(m, AssistantMessage) and m.usage]
    assert len(usages) == 1 and usages[0].message_id == "oc-msg_1"  # counted once
    assert usages[0].usage == {"input_tokens": 6, "output_tokens": 60, "cache_read_input_tokens": 100,
                               "cache_creation_input_tokens": 20, "output_tokens_details": {"thinking_tokens": 10}}
    text = "".join(m.event["delta"]["text"] for m in out
                   if isinstance(m, StreamEvent) and m.event.get("type") == "content_block_delta")
    assert text == "Done."
    result = out[-1]
    assert (result.result, result.num_turns, result.total_cost_usd, result.is_error) == ("Done.", 1, None, False)


def test_a_turn_runs_the_model_at_its_effort_and_resumes_the_session() -> None:
    client = _client(None)
    args = client._args("hello")
    assert args[args.index("-m") + 1] == "opencode-go/qwen3.8-flash#medium"
    # The message goes in on stdin, never as an argument (OpenCode 2.2 recorded
    # an argument message twice).
    assert "hello" not in args and "--" not in args and "--session" not in args
    client.session = "ses_9"
    assert args != client._args("again") and "ses_9" in client._args("again")


def test_a_run_that_prints_nothing_is_started_once_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opencode, "STARTUP_TIMEOUT_S", 0.5)
    marker = tmp_path / "started"
    # Stalls the first time; the second time it says one thing and ends.
    script = (f'if [ -e {marker} ]; then echo \'{{"type":"text","sessionID":"ses_2","part":{{"text":"ok"}}}}\'; '
              f"else touch {marker}; sleep 30; fi")
    notices = []

    async def run() -> list:
        client = _client(None)
        client.cwd = tmp_path
        client.notice = notices.append
        client._in_turn = True
        client._launch = (["sh", "-c", script], dict(opencode.os.environ))

        async def nothing() -> None:
            return None

        client._count_the_rest = nothing
        client._proc = await client._start()
        await client._run(client._proc)
        return _drain(client)

    out = asyncio.run(run())
    assert notices == ["OpenCode did not start; starting it again."]
    assert out[-1].result == "ok" and not out[-1].is_error


def test_a_session_resumes_only_on_the_agent_that_kept_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dex import antigravity
    from dex.runner import TaskRunner

    monkeypatch.setattr(antigravity, "gemini_home", lambda: tmp_path)
    (tmp_path / "antigravity-cli" / "brain" / "agy-conv").mkdir(parents=True)
    runner = TaskRunner.__new__(TaskRunner)
    runner.task = SimpleNamespace(is_survey=False)
    claude_session = "d37466bc-0203-4bc6-8a9d-c0563a3e67b8"
    assert runner._resumable("oc:qwen3.8-flash", "ses_abc")
    assert not runner._resumable("oc:qwen3.8-flash", claude_session)
    assert runner._resumable("agy:gemini-4-argon-high", "agy-conv")
    assert not runner._resumable("agy:gemini-4-argon-high", claude_session)
    assert runner._resumable("claude-opus-5-5", claude_session)
    assert not runner._resumable("claude-opus-5-5", "ses_abc")
    assert not runner._resumable("claude-opus-5-5", "agy-conv")



# ------------------------------------------------------------- local models

LOCAL_27B = {"id": "qwen3.8-27b", "name": "Local Qwen3.8 27B", "url": "http://127.0.0.1:8100/v1",
             "served": "qwen3.8-27b", "context": 65536, "output": 16384, "priced_as": "qwen3.8-flash"}


@pytest.fixture
def local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import json as _json
    f = tmp_path / "local-models.json"
    f.write_text(_json.dumps([LOCAL_27B]))
    monkeypatch.setattr(opencode, "LOCAL_FILE", f)
    monkeypatch.setattr(opencode, "cli", lambda: Path("/opt/homebrew/bin/opencode"))
    up = {"now": True}
    monkeypatch.setattr(opencode, "local_up", lambda entry: up["now"])
    return up


def test_a_local_model_is_offered_in_the_opencode_group_while_its_server_answers(local) -> None:
    first = opencode.choices()[0]
    assert first["id"] == "oc:local/qwen3.8-27b" and first["provider"] == "opencode" and first["available"]
    local["now"] = False
    first = opencode.choices()[0]
    assert not first["available"] and "8100" in first["unavailable"]
    assert opencode.is_local("oc:local/qwen3.8-27b") and not opencode.is_local("oc:qwen3.8-flash")


def test_a_local_model_runs_on_the_local_provider_at_its_efforts_variant(local) -> None:
    options = SimpleNamespace(cwd=str(CWD), model="oc:local/qwen3.8-27b", effort="high", can_use_tool=None,
                              system_prompt="", resume=None, mcp_servers={}, env={}, stderr=None)
    client = OpencodeClient(options)
    args = client._args("hi")
    assert args[args.index("-m") + 1] == "local/qwen3.8-27b#high"
    import json as _json
    provider = _json.loads(client._config())["provider"]["local"]
    assert provider["options"]["baseURL"] == "http://127.0.0.1:8100/v1"
    model = provider["models"]["qwen3.8-27b"]
    assert model["tool_call"] and model["temperature"] is False and model["limit"]["context"] == 65536
    # Effort is a thinking budget and a reply cap, sent in every request's body.
    high = model["variants"]["high"]
    assert high["thinking_budget_tokens"] == 4096 and high["max_completion_tokens"] == 4096 + 8192
    assert "act now" in high["reasoning_budget_message"]
    assert model["variants"]["none"]["thinking_budget_tokens"] == 0
    assert model["limit"]["output"] == 12288 + 8192
    # A Go model's run declares no local provider.
    assert "provider" not in _json.loads(_client(None)._config())


def _local_client(system: str = "the brief") -> OpencodeClient:
    return OpencodeClient(SimpleNamespace(cwd=str(CWD), model="oc:local/qwen3.8-27b", effort="medium",
                                          can_use_tool=None, system_prompt={"append": system}, resume=None,
                                          mcp_servers={}, env={}, stderr=None))


def test_a_local_model_is_asked_to_work_in_small_steps_after_dex_s_instructions(local) -> None:
    client = _local_client()
    assert client.system.startswith("the brief") and client.system.endswith(opencode.LOCAL_WORKING)
    assert _local_client("").system == opencode.LOCAL_WORKING
    # A Go model is not.
    assert opencode.LOCAL_WORKING not in _client(None).system


def test_an_entry_can_leave_the_small_steps_guidance_out(local, tmp_path: Path) -> None:
    import json as _json
    opencode.LOCAL_FILE.write_text(_json.dumps([{**LOCAL_27B, "working": False}]))
    assert _local_client().system == "the brief"


def test_a_local_models_run_caps_tool_output_and_keeps_a_tail_through_compaction(local) -> None:
    import json as _json
    config = _json.loads(_local_client()._config())
    assert config["tool_output"] == opencode.LOCAL_TOOL_OUTPUT
    assert config["compaction"] == {"keep": {"tokens": 30000}}
    opencode.LOCAL_FILE.write_text(_json.dumps([{**LOCAL_27B, "tool_output": {"max_bytes": 1000},
                                                 "compaction": {"auto": False}}]))
    config = _json.loads(_local_client()._config())
    assert config["tool_output"] == {"max_lines": 800, "max_bytes": 1000}
    assert config["compaction"] == {"keep": {"tokens": 30000}, "auto": False}
    # A Go model's run leaves OpenCode's own limits.
    plain = _json.loads(_client(None)._config())
    assert "tool_output" not in plain and "compaction" not in plain


def test_a_local_model_is_priced_at_its_go_models_rates(local) -> None:
    assert opencode.rates("oc:local/qwen3.8-27b") == opencode.rates("oc:qwen3.8-flash")


def test_a_go_quota_never_holds_a_local_model() -> None:
    from dex.usage import provider_window
    assert provider_window("oc:local/qwen3.8-27b") == "local"
    assert provider_window("oc:qwen3.8-flash") == "opencode"
    assert not counts_for("opencode", "oc:local/qwen3.8-27b")
    assert not counts_for("seven_day", "oc:local/qwen3.8-27b")


def test_a_local_model_gets_longer_to_print_its_first_line(local) -> None:
    options = SimpleNamespace(cwd=str(CWD), model="oc:local/qwen3.8-27b", effort=None, can_use_tool=None,
                              system_prompt="", resume=None, mcp_servers={}, env={}, stderr=None)
    assert OpencodeClient(options)._startup_timeout == opencode.LOCAL_STARTUP_TIMEOUT_S
    assert _client(None)._startup_timeout == opencode.STARTUP_TIMEOUT_S


def test_a_local_model_that_takes_images_says_so_to_opencode() -> None:
    plain = opencode.local_provider(LOCAL_27B)["models"]["qwen3.8-27b"]
    assert plain["attachment"] is False and plain["modalities"]["input"] == ["text"]
    seeing = opencode.local_provider({**LOCAL_27B, "images": True})["models"]["qwen3.8-27b"]
    assert seeing["attachment"] is True and seeing["modalities"] == {"input": ["text", "image"], "output": ["text"]}


def test_a_local_models_effort_defaults_to_medium_and_its_entry_can_tune_the_budgets(local) -> None:
    assert opencode.local_variant(None) == "medium" and opencode.local_variant("bogus") == "medium"
    assert opencode.local_variant("xhigh") == "xhigh"
    tuned = opencode.local_budgets({**LOCAL_27B, "thinking": {"medium": 1000}, "answer_tokens": 4000})
    assert tuned["medium"] == {"thinking_budget_tokens": 1000, "reasoning_budget_message": opencode.LOCAL_BUDGET_MESSAGE,
                               "max_completion_tokens": 5000}
    assert tuned["high"]["max_completion_tokens"] == 4096 + 4000


def test_a_local_model_with_effort_fields_also_sends_the_effort_and_budget_by_their_openai_names(local) -> None:
    # Splash reads reasoning_effort and thinking_token_budget, not llama-server's thinking_budget_tokens.
    plain = opencode.local_budgets(LOCAL_27B)
    named = opencode.local_budgets({**LOCAL_27B, "effort_fields": True})
    assert "reasoning_effort" not in plain["medium"] and "thinking_token_budget" not in plain["medium"]
    assert named["medium"]["reasoning_effort"] == "medium" and named["medium"]["thinking_token_budget"] == 2048
    assert named["none"]["reasoning_effort"] == "none" and named["none"]["thinking_token_budget"] == 0
    assert named["medium"]["thinking_budget_tokens"] == 2048
    variants = opencode.local_provider({**LOCAL_27B, "effort_fields": True})["models"]["qwen3.8-27b"]["variants"]
    assert variants["high"]["reasoning_effort"] == "high"


@pytest.mark.parametrize("outcome, failed", [("succeeded", False), (None, True), ("failed", True)])
def test_an_exit_code_after_a_session_that_succeeded_is_not_a_failure(tmp_path: Path, outcome, failed) -> None:
    # Says its last word, then exits 1 with nothing on stderr: OpenCode's shutdown.
    script = 'echo \'{"type":"text","sessionID":"ses_3","part":{"text":"Done: package validated."}}\'; exit 1'

    async def run() -> list:
        client = _client(None)
        client.cwd = tmp_path
        client._in_turn = True
        client._launch = (["sh", "-c", script], dict(opencode.os.environ))

        async def export() -> str | None:
            return outcome  # what the session's own record says

        client._count_the_rest = export
        client._proc = await client._start()
        await client._run(client._proc)
        return _drain(client)

    last = asyncio.run(run())[-1]
    assert last.is_error is failed
    assert (last.result == "OpenCode exited (1)") if failed else (last.result == "Done: package validated.")


def test_dex_instructions_say_dex_tools_stay_even_when_the_first_catalog_lacks_them() -> None:
    options = SimpleNamespace(cwd=str(CWD), model="oc:qwen3.8-flash", effort=None, can_use_tool=None,
                              system_prompt={"type": "preset", "append": "Be dex."}, resume=None,
                              mcp_servers={"dex": object()}, env={}, stderr=None)
    client = OpencodeClient(options)
    assert client.system.startswith("Be dex.") and "tools.dex.<name>" in client.system
    assert "start_power_task" in client.system and "`read` tool" in client.system
    bare = OpencodeClient(SimpleNamespace(**{**vars(options), "mcp_servers": {}}))
    assert bare.system == "Be dex."
