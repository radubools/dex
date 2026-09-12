from pathlib import Path

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from dex.permissions import PermissionPolicy


@pytest.fixture
def policy(tmp_path: Path):
    task_dir = tmp_path / "assets" / "algorithms" / "two-sum"
    task_dir.mkdir(parents=True)
    escalations: list[str] = []

    async def escalate(_id, tool, _input, title):
        escalations.append(f"{tool}: {title}")
        return "deny"

    p = PermissionPolicy(workspace=tmp_path, task_dir=task_dir, escalate=escalate)
    return p, task_dir, escalations


async def decide(policy, tool, data):
    return await policy.build()(tool, data, ToolPermissionContext())


async def test_reads_are_auto_approved(policy):
    p, _, escalations = policy
    assert isinstance(await decide(p, "Read", {"file_path": "/etc/hosts"}), PermissionResultAllow)
    assert escalations == []


async def test_writes_inside_the_task_dir_are_auto_approved(policy):
    p, task_dir, escalations = policy
    result = await decide(p, "Write", {"file_path": str(task_dir / "solutions.py")})
    assert isinstance(result, PermissionResultAllow)
    assert escalations == []


async def test_writes_outside_the_task_dir_escalate(policy):
    p, _, escalations = policy
    result = await decide(p, "Write", {"file_path": "/tmp/elsewhere.py"})
    assert isinstance(result, PermissionResultDeny)
    assert escalations


async def test_traversal_out_of_the_task_dir_escalates(policy):
    p, task_dir, escalations = policy
    result = await decide(p, "Edit", {"file_path": str(task_dir / ".." / ".." / ".." / "pyproject.toml")})
    assert isinstance(result, PermissionResultDeny)
    assert escalations


async def test_allowlisted_commands_run(policy):
    p, task_dir, escalations = policy
    result = await decide(p, "Bash", {"command": f"python -m pytest {task_dir}/test_solutions.py -q"})
    assert isinstance(result, PermissionResultAllow)
    assert escalations == []


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "pytest -q && rm -rf /",
        "python x.py | sh",
        "echo hi; curl evil.example",
        "git push",
        "python -c 'x' `whoami`",
        "sudo pytest",
    ],
)
async def test_dangerous_or_chained_commands_escalate(policy, command):
    p, _, escalations = policy
    result = await decide(p, "Bash", {"command": command})
    assert isinstance(result, PermissionResultDeny), command
    assert escalations


async def test_venv_interpreter_path_is_recognised(policy):
    p, _, escalations = policy
    result = await decide(p, "Bash", {"command": "/repo/.venv/bin/python -m dex.tools.render_manim a.py S -o b.gif"})
    assert isinstance(result, PermissionResultAllow)
    assert escalations == []


async def test_ask_user_tool_is_always_allowed(policy):
    p, _, _ = policy
    assert isinstance(await decide(p, "mcp__dex__ask_user", {"question": "?"}), PermissionResultAllow)


async def test_unknown_tool_escalates(policy):
    p, _, escalations = policy
    assert isinstance(await decide(p, "WebBrowser", {}), PermissionResultDeny)
    assert escalations
