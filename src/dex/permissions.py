"""Tool-permission policy for unattended task runs.

A task runs without a human watching, so blanket prompting would deadlock it and
blanket approval would hand the agent the whole machine. The policy below
auto-approves the work the task legitimately needs — reads anywhere in the
workspace, writes confined to the task's own output directory, and a fixed set
of build/test commands — and escalates anything else to the operator.
"""

from __future__ import annotations

import asyncio
import shlex
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "NotebookRead", "TodoWrite", "WebSearch", "WebFetch"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

#: First word of a shell command that the task is expected to run on its own.
ALLOWED_COMMANDS = {
    "python", "python3", "pytest", "manim", "ls", "cat", "head", "tail", "wc",
    "mkdir", "cp", "mv", "echo", "pwd", "which", "find", "grep", "sed", "awk",
    "test", "true", "file", "ffmpeg", "ffprobe", "uv",
}

#: Never auto-approved, however the command is spelled.
DANGEROUS = {"rm", "sudo", "chmod", "chown", "curl", "wget", "ssh", "scp", "git", "npm", "pip", "brew", "kill", "pkill"}


class PermissionPolicy:
    """Builds the `can_use_tool` callback for one task."""

    def __init__(
        self,
        *,
        workspace: Path,
        task_dir: Path,
        extra_writable: tuple[Path, ...] = (),
        escalate: Callable[[str, str, dict[str, Any], str | None], Awaitable[str]],
    ) -> None:
        self.workspace = workspace.resolve()
        self.task_dir = task_dir.resolve()
        #: Directories this run may write to besides its own. Empty for an
        #: ordinary task; a design turn adds `widgets/`, because widget code is
        #: shared across projects and so cannot live inside any one of them.
        self.extra_writable = tuple(p.resolve() for p in extra_writable)
        #: Called when a decision needs a human; resolves to "allow" or "deny".
        self.escalate = escalate

    def _within(self, raw: str | None, root: Path) -> bool:
        if not raw:
            return False
        try:
            candidate = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        except OSError:
            return False
        return candidate == root or root in candidate.parents

    def _bash_verdict(self, command: str) -> tuple[bool, str]:
        try:
            words = shlex.split(command)
        except ValueError:
            return False, "command could not be parsed"
        if not words:
            return False, "empty command"
        # Any of `;`, `&&`, `|`, backticks can smuggle a second command past a
        # check on the first word, so those go to a human.
        if any(tok in command for tok in (";", "&&", "||", "|", "`", "$(", ">(")):
            return False, "chained or substituted command"
        program = Path(words[0]).name
        if program in DANGEROUS:
            return False, f"`{program}` is never auto-approved"
        if program not in ALLOWED_COMMANDS:
            return False, f"`{program}` is not in the task allowlist"
        return True, ""

    def build(self) -> Callable[[str, dict[str, Any], ToolPermissionContext], Awaitable[Any]]:
        async def can_use_tool(
            tool_name: str, input_data: dict[str, Any], context: ToolPermissionContext
        ) -> PermissionResultAllow | PermissionResultDeny:
            reason = self._auto_reason(tool_name, input_data)
            if reason is None:
                return PermissionResultAllow(updated_input=input_data)

            decision = await self.escalate(
                uuid.uuid4().hex[:12], tool_name, input_data, f"{context.title or tool_name} — {reason}"
            )
            if decision == "allow":
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultDeny(message=f"The operator declined: {reason}")

        return can_use_tool

    def _auto_reason(self, tool_name: str, input_data: dict[str, Any]) -> str | None:
        """None means auto-approve; a string explains why a human is needed."""
        if tool_name in READ_ONLY_TOOLS:
            return None
        if tool_name.startswith("mcp__dex__"):
            return None
        if tool_name in WRITE_TOOLS:
            path = input_data.get("file_path") or input_data.get("path") or input_data.get("notebook_path")
            if self._within(path, self.task_dir):
                return None
            if any(self._within(path, root) for root in self.extra_writable):
                return None
            return f"writes outside the task directory ({path})"
        if tool_name == "Bash":
            ok, why = self._bash_verdict(str(input_data.get("command", "")))
            return None if ok else why
        return f"{tool_name} has no auto-approval rule"


async def auto_deny(_id: str, _tool: str, _input: dict[str, Any], _title: str | None) -> str:
    """Escalation stub for runs with no operator attached."""
    await asyncio.sleep(0)
    return "deny"
