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


# ------------------------------------------------- reading the neighbours


@pytest.fixture
def in_project(tmp_path: Path):
    """A task that knows which project it belongs to, and one neighbour."""
    project = tmp_path / "assets" / "algorithms"
    task_dir = project / "two-sum"
    task_dir.mkdir(parents=True)
    (project / "three-sum").mkdir()
    (project / "utils").mkdir()
    escalations: list[str] = []

    async def escalate(_id, tool, _input, title):
        escalations.append(f"{tool}: {title}")
        # The toggle an operator can leave on. A rule that only escalates is
        # not a rule once this answers "allow".
        return "allow"

    p = PermissionPolicy(
        workspace=tmp_path, task_dir=task_dir, project_dir=project, escalate=escalate
    )
    return p, project, task_dir, escalations


async def test_a_task_may_read_another_package_in_its_project(in_project):
    p, project, _, escalations = in_project
    result = await decide(p, "Read", {"file_path": str(project / "three-sum" / "solution.py")})
    assert isinstance(result, PermissionResultAllow)
    assert escalations == []


async def test_a_task_may_not_write_into_another_package(in_project):
    """Refused outright — not offered to the operator.

    The escalation path in this fixture answers "allow", which is what
    `auto_approve` does. A rule that only escalated would not hold.
    """
    p, project, _, escalations = in_project
    result = await decide(p, "Write", {"file_path": str(project / "three-sum" / "solution.py")})
    assert isinstance(result, PermissionResultDeny)
    assert "three-sum" in result.message
    assert escalations == []


async def test_a_relative_path_out_of_the_task_dir_is_refused_too(in_project):
    """`../three-sum/x.py` is the neighbour, not a path under the project.

    Relative paths resolve against the agent's cwd, which is its own
    directory. Resolving them against whatever root is being tested would read
    this one as `<project>/../three-sum` and miss it.
    """
    p, _, _, escalations = in_project
    result = await decide(p, "Edit", {"file_path": "../three-sum/solution.py"})
    assert isinstance(result, PermissionResultDeny)
    assert escalations == []


async def test_the_project_s_own_entries_are_not_another_task_s_package(in_project):
    """`utils/` and the guide belong to the project, so they keep escalating.

    They are not somebody else's work, and the shared-utilities protocol ends
    in a write to one of them that the operator is meant to approve.
    """
    p, project, _, escalations = in_project
    for path in (project / "utils" / "rig.py", project / "AGENTS.md"):
        escalations.clear()
        result = await decide(p, "Write", {"file_path": str(path)})
        assert isinstance(result, PermissionResultAllow), path
        assert len(escalations) == 1, path


async def test_a_project_wide_task_owns_every_package(tmp_path: Path):
    """A sweep has the project as its own directory, so it has no neighbours."""
    project = tmp_path / "assets" / "algorithms"
    (project / "three-sum").mkdir(parents=True)
    escalations: list[str] = []

    async def escalate(_id, tool, _input, title):
        escalations.append(tool)
        return "allow"

    p = PermissionPolicy(
        workspace=tmp_path, task_dir=project, project_dir=project, escalate=escalate
    )
    result = await decide(p, "Write", {"file_path": str(project / "three-sum" / "x.py")})
    assert isinstance(result, PermissionResultAllow)
    assert escalations == []


async def test_another_project_is_still_the_operator_s_call(in_project):
    """The rule is about this project. Anything further out is unchanged."""
    p, project, _, escalations = in_project
    other = project.parent / "yoga" / "tree-pose" / "pose.py"
    result = await decide(p, "Write", {"file_path": str(other)})
    assert isinstance(result, PermissionResultAllow)  # the fixture approves
    assert len(escalations) == 1


@pytest.fixture
def power(tmp_path: Path):
    assets = tmp_path / "assets"
    task_dir = assets / "ml" / "power-fix"
    task_dir.mkdir(parents=True)
    (assets / "ml" / "story-x").mkdir()
    escalations: list[str] = []

    async def escalate(_id, tool, _input, title):
        escalations.append(f"{tool}: {title}")
        return "deny"

    p = PermissionPolicy(workspace=tmp_path, task_dir=task_dir, project_dir=assets / "ml", escalate=escalate,
                         power=True, assets_dir=assets)
    return p, tmp_path, task_dir, escalations


@pytest.mark.parametrize("path", ["/elsewhere/games/engine/src/story/runner.js", "/elsewhere/tool.py", "/elsewhere/AGENTS.md"])
async def test_a_power_task_changes_code_anywhere_unasked(power, path):
    p, _, _, escalations = power
    assert isinstance(await decide(p, "Write", {"file_path": path}), PermissionResultAllow)
    assert escalations == []


@pytest.mark.parametrize("command", ["git commit -m fix", "npm test", "git push", "rm old.py", "git merge --ff-only x && npm i | tail"])
async def test_a_power_task_runs_commands_unasked(power, command):
    p, _, _, escalations = power
    assert isinstance(await decide(p, "Bash", {"command": command}), PermissionResultAllow)
    assert escalations == []


@pytest.mark.parametrize("path", [
    "/elsewhere/games/g/public/assets/index.json", "/elsewhere/lesson/story.json", "/elsewhere/manifest.json",
    "/elsewhere/a.glb", "/elsewhere/a.ogg", "/elsewhere/a.png", "/elsewhere/a.mp4",
])
async def test_a_power_task_makes_no_content(power, path):
    p, _, _, escalations = power
    assert isinstance(await decide(p, "Write", {"file_path": path}), PermissionResultDeny)
    assert escalations == []


async def test_a_power_task_leaves_packages_alone_but_may_change_a_project_brief(power):
    p, root, task_dir, _ = power
    assert isinstance(await decide(p, "Write", {"file_path": str(root / "assets/ml/story-x/notes.md")}), PermissionResultDeny)
    assert isinstance(await decide(p, "Write", {"file_path": str(root / "assets/other/thing.py")}), PermissionResultDeny)
    assert isinstance(await decide(p, "Write", {"file_path": str(root / "assets/ml/AGENTS.md")}), PermissionResultAllow)
    assert isinstance(await decide(p, "Write", {"file_path": str(task_dir / "report.md")}), PermissionResultAllow)


async def test_a_power_task_does_not_save_into_the_running_dex(power):
    p, root, _, _ = power
    out = await decide(p, "Edit", {"file_path": str(root / "src/dex/queue.py")})
    assert isinstance(out, PermissionResultDeny) and "worktree" in out.message
    assert isinstance(await decide(p, "Edit", {"file_path": str(root / "skills/x/SKILL.md")}), PermissionResultAllow)


@pytest.mark.parametrize("command", [
    "sudo make install", "pm2 restart dex-api", "/opt/homebrew/bin/pm2 list", "kill 123", "pkill -f dex",
    "lsof -t -i:4317", ".venv/bin/dex --port 9", "git push --force", "git push -f origin main",
    "git reset --hard HEAD", "git clean -fd", "rm -rf ~", "rm -rf /", "psql -c 'DROP TABLE tasks'",
    "echo hi; truncate tasks", "python -c 'write_story(p, s)'", "cp a.json x/public/assets/story/a.json",
])
async def test_what_a_power_task_may_never_run(power, command):
    p, _, _, escalations = power
    assert isinstance(await decide(p, "Bash", {"command": command}), PermissionResultDeny)
    assert escalations == []


async def test_the_game_pull_is_not_content(power):
    p, *_ = power
    out = await decide(p, "Bash", {"command": "python tools/pull_dex_assets.py game --only story-x"})
    assert isinstance(out, PermissionResultAllow)
