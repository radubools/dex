"""Tool-permission policy for unattended task runs.

A task runs without a human watching, so blanket prompting would deadlock it and
blanket approval would hand the agent the whole machine. The policy below
auto-approves the work the task legitimately needs — reads anywhere in the
workspace, writes confined to the task's own output directory, and a fixed set
of build/test commands — and escalates anything else to the operator.

One rule does not escalate: a write into **another task's package in the same
project** is refused outright. Reading those packages is allowed and useful —
a task matching a project's conventions should be able to look at what its
neighbours did — but a peek that can turn into an edit is not read-only, and
`auto_approve` turns every escalation into an allow. A rule that must hold
whatever that toggle says cannot be expressed as a question.
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
    # Building a widget and testing it. Both are named in every project guide,
    # and neither was allowed here: a design turn stopped for the operator on
    # every build, which went unnoticed only because auto-approve was on.
    # `node` runs local code, the same class of thing as `python`. `playwright`
    # is matched by basename, so the guides name `node_modules/.bin/playwright`
    # directly rather than `npx`, which downloads a package that is missing.
    "node", "playwright",
}

#: Never auto-approved, however the command is spelled.
DANGEROUS = {"rm", "sudo", "chmod", "chown", "curl", "wget", "ssh", "scp", "git", "npm", "pip", "brew", "kill", "pkill"}

#: `mcp__dex__*` tools are auto-approved — they are dex's own, and a task
#: asking a question should not need permission to ask. This one is different:
#: it creates a standing grant that every future task in the project inherits,
#: over a directory dex does not own. The dialog shows the resolved target,
#: which is the fact worth confirming.
ESCALATING_TOOLS = {"mcp__dex__link_local_source"}

#: Entries at the top of a project directory that are not another task's work.
#: Everything else beside a task's own directory is a sibling package.
NOT_A_PACKAGE = {"utils", "AGENTS.md", "widgets.json", "skills.json"}


class PermissionPolicy:
    """Builds the `can_use_tool` callback for one task."""

    def __init__(
        self,
        *,
        workspace: Path,
        task_dir: Path,
        project_dir: Path | None = None,
        extra_writable: tuple[Path, ...] = (),
        read_only: tuple[Path, ...] = (),
        escalate: Callable[[str, str, dict[str, Any], str | None], Awaitable[str]],
    ) -> None:
        self.workspace = workspace.resolve()
        self.task_dir = task_dir.resolve()
        #: The project this task belongs to. Its other packages are readable
        #: and not writable — see `_other_package`. A project-wide task has the
        #: project *as* its directory, so nothing beside it is a sibling and
        #: the rule never fires.
        self.project_dir = project_dir.resolve() if project_dir else None
        #: Directories this run may write to besides its own. Empty for an
        #: ordinary task; a design turn adds `widgets/`, because widget code is
        #: shared across projects and so cannot live inside any one of them.
        self.extra_writable = tuple(p.resolve() for p in extra_writable)
        #: Directories linked into the project's data directory from elsewhere
        #: on the machine. Readable — that is why they were linked — and never
        #: writable: they are the operator's own material, which dex does not
        #: own and could not restore.
        self.read_only = tuple(p.resolve() for p in read_only)
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

    def _resolved(self, raw: str | None) -> Path | None:
        """A path as the agent meant it: relative ones against its cwd.

        `_within` resolves against whichever root it is testing, which is right
        for the task directory and wrong for everything else.
        """
        if not raw:
            return None
        try:
            return (
                Path(raw).resolve() if Path(raw).is_absolute()
                else (self.task_dir / raw).resolve()
            )
        except OSError:
            return None

    def _linked_source(self, raw: str | None) -> Path | None:
        """The linked directory this path is inside, if it is inside one.

        Resolved, so `datasets/p/scans/x.dcm` — a path that looks like it is
        safely inside the project — is recognised as the external volume it
        actually reaches.
        """
        candidate = self._resolved(raw)
        if candidate is None:
            return None
        for root in self.read_only:
            if candidate == root or root in candidate.parents:
                return root
        return None

    def _other_package(self, raw: str | None) -> str | None:
        """The name of another task's package this path is inside, if it is.

        A project directory holds one directory per task, plus the handful of
        project-level entries in `NOT_A_PACKAGE`. So a path under the project
        but under none of those, and not under this task's own directory, is
        inside somebody else's work.
        """
        if not raw or self.project_dir is None:
            return None
        candidate = self._resolved(raw)
        if candidate is None:
            return None
        if candidate == self.task_dir or self.task_dir in candidate.parents:
            return None
        if self.project_dir not in candidate.parents:
            return None
        first = candidate.relative_to(self.project_dir).parts[0]
        return None if first in NOT_A_PACKAGE else first

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
            refusal = self._refuse_reason(tool_name, input_data)
            if refusal is not None:
                return PermissionResultDeny(message=refusal)

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

    def _refuse_reason(self, tool_name: str, input_data: dict[str, Any]) -> str | None:
        """A string means refuse outright — the operator is never asked.

        Reserved for the one thing that has to hold regardless of the
        auto-approve toggle: another task's package is read-only. Everything
        else a task might do outside its directory is a judgement call, and
        judgement calls belong to the operator.
        """
        if tool_name not in WRITE_TOOLS:
            return None
        path = _target_path(input_data)
        linked = self._linked_source(path)
        if linked is not None:
            # Checked first: the link sits inside the data directory, which is
            # an extra writable root, so the allow below would catch it.
            return (
                f"`{linked}` is linked into this project to be read, not "
                "changed. It is the operator's own material and lives outside "
                "dex, which cannot restore it. Write what you produce into your "
                "own directory."
            )
        if self._within(path, self.task_dir) or any(
            self._within(path, root) for root in self.extra_writable
        ):
            return None
        other = self._other_package(path)
        if other is None:
            return None
        return (
            f"`{other}` is another task's package. You may read anything in this "
            "project, but only your own directory is yours to change — and that "
            "package may be being written right now by the task that owns it."
        )

    def _auto_reason(self, tool_name: str, input_data: dict[str, Any]) -> str | None:
        """None means auto-approve; a string explains why a human is needed."""
        if tool_name in READ_ONLY_TOOLS:
            return None
        if tool_name in ESCALATING_TOOLS:
            return "links a directory outside dex into this project"
        if tool_name.startswith("mcp__dex__"):
            return None
        if tool_name in WRITE_TOOLS:
            path = _target_path(input_data)
            if self._within(path, self.task_dir):
                return None
            if any(self._within(path, root) for root in self.extra_writable):
                return None
            return f"writes outside the task directory ({path})"
        if tool_name == "Bash":
            ok, why = self._bash_verdict(str(input_data.get("command", "")))
            return None if ok else why
        return f"{tool_name} has no auto-approval rule"


def _target_path(input_data: dict[str, Any]) -> str | None:
    """The file a write tool is aimed at, whichever key it used."""
    return (
        input_data.get("file_path")
        or input_data.get("path")
        or input_data.get("notebook_path")
    )


async def auto_deny(_id: str, _tool: str, _input: dict[str, Any], _title: str | None) -> str:
    """Escalation stub for runs with no operator attached."""
    await asyncio.sleep(0)
    return "deny"
