"""An ideation run changes nothing, whatever its commands say (design/24).

Its commands are approved unasked, and what a command writes can't be read off
it. These run real commands the way the runner would hand them to the agent.
"""

from __future__ import annotations

import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from dex import sandbox
from dex.permissions import PermissionPolicy

needs_sandbox = pytest.mark.skipif(not sandbox.available(), reason="no sandbox-exec here")


@pytest.fixture
def run(tmp_path):
    ideas = (tmp_path / "ideas" / "ml").resolve()
    ideas.mkdir(parents=True)
    (ideas / "IDEAS.md").write_text("# Ideas\n", encoding="utf-8")
    (ideas / "todo-one").mkdir()
    (ideas / "todo-one" / "SPEC.md").write_text("approved\n", encoding="utf-8")
    code = (tmp_path / "skills").resolve()
    code.mkdir()
    (code / "tool.py").write_text("x = 1\n", encoding="utf-8")

    async def allow(*_args):
        return "allow"

    policy = PermissionPolicy(
        workspace=tmp_path, task_dir=ideas, escalate=allow, scout=True, unattended=True, assets_dir=tmp_path,
    )
    decide = policy.build()

    async def bash(command: str) -> subprocess.CompletedProcess:
        result = await decide("Bash", {"command": command}, SimpleNamespace(title=None))
        assert isinstance(result, PermissionResultAllow)
        wrapped = result.updated_input["command"]
        return subprocess.run(wrapped, shell=True, cwd=ideas, capture_output=True, text=True, timeout=30)

    return SimpleNamespace(ideas=ideas, code=code, bash=bash, decide=decide)


@needs_sandbox
@pytest.mark.parametrize("attack", [
    "echo y > {f}",
    "sed -i '' s/1/2/ {f}",
    "python3 -c \"open('{f}', 'w').write('y')\"",
    "cp /etc/hosts {f}",
    "rm {f}",
    "mv {f} {f}.bak",
])
async def test_no_command_changes_code(run, attack):
    target = run.code / "tool.py"

    await run.bash(attack.format(f=target))

    assert target.read_text() == "x = 1\n"
    assert [p.name for p in run.code.iterdir()] == ["tool.py"]


@needs_sandbox
async def test_a_command_cannot_change_an_approved_spec(run):
    spec = run.ideas / "todo-one" / "SPEC.md"

    await run.bash(f"echo changed > {spec}")
    await run.bash(f"mv {run.ideas / 'todo-one'} {run.ideas / 'gone'}")

    assert spec.read_text() == "approved\n"


@needs_sandbox
async def test_a_game_can_start_but_its_code_stays_put(run):
    """Vite writes its caches beside the game: nothing runs without them."""
    game = run.code / "game" / "node_modules" / ".vite-temp"
    game.mkdir(parents=True)

    done = await run.bash(f"echo cached > {game / 'config.mjs'} && echo y > {run.code / 'game' / 'main.js'}")

    assert (game / "config.mjs").read_text() == "cached\n"
    assert done.returncode != 0 and not (run.code / "game" / "main.js").exists()


@needs_sandbox
async def test_it_keeps_its_log_and_reads_anything(run):
    done = await run.bash(f"echo seen >> IDEAS.md && cat {run.code / 'tool.py'} && echo t > $TMPDIR/scratch && cat $TMPDIR/scratch")

    assert done.returncode == 0, done.stderr
    assert "x = 1" in done.stdout and "t" in done.stdout
    assert "seen" in (run.ideas / "IDEAS.md").read_text()


class Answer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


def serve(port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port(ports) -> int:
    for port in ports:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise AssertionError("no free port")


@needs_sandbox
async def test_it_reaches_test_ports_and_no_other(run):
    allowed, other = serve(free_port(sandbox.TEST_PORTS)), serve(free_port(range(4600, 4700)))
    try:
        curl = "curl -s -m 3 -o /dev/null -w %{{http_code}} http://localhost:{}/"
        assert (await run.bash(curl.format(allowed.server_port))).stdout == "200"
        assert (await run.bash(curl.format(other.server_port))).stdout != "200"
    finally:
        allowed.shutdown()
        other.shutdown()


async def test_the_write_tools_reach_only_its_notes(run):
    async def write(path: Path):
        return await run.decide("Write", {"file_path": str(path), "content": "y"}, SimpleNamespace(title=None))

    assert isinstance(await write(run.ideas / "notes.md"), PermissionResultAllow)
    assert isinstance(await write(run.ideas / "IDEAS.md"), PermissionResultDeny)
    assert isinstance(await write(run.ideas / "todo-one" / "SPEC.md"), PermissionResultDeny)
    assert isinstance(await write(run.ideas / "script.py"), PermissionResultDeny)
    assert isinstance(await write(run.code / "tool.py"), PermissionResultDeny)


async def test_without_a_sandbox_there_is_no_shell(run, monkeypatch):
    monkeypatch.setattr(sandbox, "available", lambda: False)

    refused = await run.decide("Bash", {"command": "ls"}, SimpleNamespace(title=None))

    assert isinstance(refused, PermissionResultDeny)
    assert "nothing can confine it" in refused.message
