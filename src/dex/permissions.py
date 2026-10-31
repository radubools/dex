"""Tool-permission policy for unattended task runs.

A task runs without a human watching, so blanket prompting would deadlock it and
blanket approval would hand the agent the whole machine. The policy below
auto-approves the work the task legitimately needs — reads anywhere in the
workspace, writes confined to the task's own output directory, and a fixed set
of build/test commands — and escalates anything else to the operator.

A **power task** is the exception: it may change code anywhere and run any
command, without asking, and it may never make content (see `_power_refusal`).

One rule does not escalate: a write into **another task's package in the same
project** is refused outright. Reading those packages is allowed and useful —
a task matching a project's conventions should be able to look at what its
neighbours did — but a peek that can turn into an edit is not read-only, and
`auto_approve` turns every escalation into an allow. A rule that must hold
whatever that toggle says cannot be expressed as a question.
"""

from __future__ import annotations

import asyncio
import re
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

from . import sandbox

READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "NotebookRead", "TodoWrite", "WebSearch", "WebFetch"}

#: Every built-in the CLI offers, for a run that is meant to have none.
#:
#: `allowed_tools=[]` does **not** mean "no tools". The SDK writes
#: `--allowedTools` only `if effective_allowed_tools:`, so an empty list is
#: falsy, the flag is never passed, and the CLI runs with its full default
#: toolset. Three single-turn calls here described themselves as tool-less and
#: had every tool available; the planner attempted one in 3 runs out of 5, and
#: because a tool call consumes the turn, `max_turns=1` then failed the whole
#: request with "Reached maximum number of turns (1)".
#:
#: `disallowed_tools` is the flag that actually removes them. Listed by name
#: because that is the interface the CLI offers — which means this goes stale
#: when the CLI gains a tool, and the symptom is the one described above rather
#: than anything that says "a tool is missing from a list".
NO_TOOLS = [
    "Task", "Bash", "BashOutput", "KillBash", "Glob", "Grep", "Read", "Edit",
    "MultiEdit", "Write", "NotebookRead", "NotebookEdit", "WebFetch",
    "WebSearch", "TodoWrite", "Skill", "SlashCommand",
]
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

#: What a run that only looks may run besides the allowlist: tests, and git's reads.
SCOUT_COMMAND = re.compile(
    r"(cd \S+ && )?(npm (test|run test\S*|run e2e\S*)|npx (vitest|playwright)|git (log|show|diff|status|blame|grep))\b"
)

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

#: Files that make a package: a chat never writes these (it asks the asset's task).
PACKAGE_FILES = {"manifest.json", "story.json", "e2e.json", "catalog.json"}
PACKAGE_SUFFIXES = {".glb", ".gltf", ".wav", ".ogg", ".mp3", ".flac", ".m4a"}
#: Shell words that build, copy or install a package. In a chat's command they mean
#: it is doing an asset task's work itself (the game's puller is the one exception).
PACKAGE_COMMAND_HINTS = ("write_story(", "export_story(", "catalog.export(", "write_package(",
                         "write_catalog(", "export_catalog(",
                         "build_story", "/public/assets/", "public/assets/")
PULLER = "pull_dex_assets.py"

DELEGATE = (
    "A chat does not make or edit assets or stories itself: each is made by its own task, in "
    "its own thread, where its history and checks live. Use `find_tasks` and `continue_task` "
    "to change one that exists, or `start_task` in the right thread for a new one; when they "
    "are done, install them with the game's pull (`{puller} … --only <task-dir>`)."
).format(puller=PULLER)


#: What a power task may not write: besides package files, anything that is
#: content rather than code.
POWER_CONTENT_SUFFIXES = PACKAGE_SUFFIXES | {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".pdf",
}
#: Text beside the packages in a project's directory: how its tasks work, not what they make.
POWER_PROJECT_FILES = {"AGENTS.md", "skills.json"}
#: What a power task may never run, however it is spelled. Its permission is
#: for changing code: not for the operator's machine, the running dex, its
#: database's contents or a remote's history.
POWER_FORBIDDEN = (
    (re.compile(r"(^|[\s;&|(])sudo\b"), "`sudo` changes the machine, not code"),
    (re.compile(r"(^|[\s;&|(/])pm2\b"), "pm2 runs dex and the operator's other services"),
    (re.compile(r"(^|[\s;&|(/])(kill|pkill|killall)\b"), "killing processes can take dex down"),
    (re.compile(r"\b431[78]\b"), "ports 4317 and 4318 are the running dex's"),
    (re.compile(r"\.venv/bin/dex\b"), "a second dex would fight the running one for its queue"),
    (re.compile(r"\bgit\b.*\bpush\b.*(--force|\s-f\b)"), "a force-push rewrites shared history"),
    (re.compile(r"\bgit\b.*\breset\b.*--hard"), "`git reset --hard` throws away uncommitted work"),
    (re.compile(r"\bgit\b.*\bclean\b"), "`git clean` deletes untracked work"),
    (re.compile(r"\brm\b.*\s(/|~|\$HOME)/?(\s|$)"), "that deletes a whole home or disk"),
    (re.compile(r"\b(drop\s+(table|database|schema)|truncate\s+\w)", re.I), "that deletes dex's data"),
)
POWER_NO_CONTENT = (
    "A power task changes code. It makes no content: no packages, stories, models, sounds, "
    "images or captions, and nothing in a game's installed assets. Those are made by their "
    "own tasks in their own threads; say in your report what the requester should ask them for."
)


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
        delegate_assets: bool = False,
        asset_dirs: tuple[Path, ...] = (),
        power: bool = False,
        scout: bool = False,
        unattended: bool = False,
        ideas_dir: Path | None = None,
        assets_dir: Path | None = None,
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
        #: A chat: every asset change goes to the asset's own task (deps.py), so
        #: it may not write package files, nor into `asset_dirs` (a game's
        #: installed assets, which only the game's pull writes).
        self.delegate_assets = delegate_assets
        self.asset_dirs = tuple(p.resolve() for p in asset_dirs)
        #: A power task: code anywhere, no content (`_power_refusal`). Every
        #: project's packages are under `assets_dir`.
        self.power = power
        #: A todo split or an ideation run: it looks and tests, and changes nothing
        #: outside its own directory.
        self.scout = scout
        #: Ideation: everything is approved unasked (`_escalate`), so what it must
        #: never do is refused here, as for a power task.
        self.unattended = unattended
        #: Set for every task but power and ideation: it may not touch this.
        self.ideas_dir = ideas_dir.resolve() if ideas_dir else None
        self.assets_dir = assets_dir.resolve() if assets_dir else None

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

            allowed = self._confined(tool_name, input_data)
            reason = self._auto_reason(tool_name, input_data)
            if reason is None:
                return PermissionResultAllow(updated_input=allowed)

            decision = await self.escalate(
                uuid.uuid4().hex[:12], tool_name, input_data, f"{context.title or tool_name} — {reason}"
            )
            if decision == "allow":
                return PermissionResultAllow(updated_input=allowed)
            return PermissionResultDeny(message=(
                f"Not allowed in this unattended run: {reason}" if self.scout
                else f"The operator declined: {reason}"))

        return can_use_tool

    def _refuse_reason(self, tool_name: str, input_data: dict[str, Any]) -> str | None:
        """A string means refuse outright — the operator is never asked.

        Reserved for the one thing that has to hold regardless of the
        auto-approve toggle: another task's package is read-only. Everything
        else a task might do outside its directory is a judgement call, and
        judgement calls belong to the operator.
        """
        if self.power:
            return self._power_refusal(tool_name, input_data)
        if self.ideas_dir is not None and self._touches_ideas(tool_name, input_data):
            return "Ideas are only for power tasks: this task may not read or write them."
        if self.unattended:
            why = self._unattended_refusal(tool_name, input_data)
            if why is not None:
                return why
        if self.delegate_assets:
            why = self._delegation_refusal(tool_name, input_data)
            if why is not None:
                return why
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

    def _confined(self, tool_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """An ideation run's shell command, in the sandbox that makes it read-only."""
        if not self.unattended or tool_name != "Bash":
            return input_data
        return {**input_data, "command": sandbox.confine(str(input_data.get("command", "")), self.task_dir)}

    def _unattended_refusal(self, tool_name: str, input_data: dict[str, Any]) -> str | None:
        if tool_name in WRITE_TOOLS:
            target = self._resolved(_target_path(input_data))
            # Not a todo's directory, below this one: a spec does not change
            # after it was approved.
            if target is None or target.parent != self.task_dir or target.suffix != ".md":
                return "An ideation run writes only its own notes: propose the change as a todo instead."
            if target.name == "IDEAS.md":
                return "The log takes one line at a time, through `log_idea`."
        if tool_name == "Bash":
            if not sandbox.available():
                return "An ideation run has no shell here: nothing can confine it (sandbox-exec is missing)."
            command = str(input_data.get("command", ""))
            for pattern, why in POWER_FORBIDDEN:
                if pattern.search(command):
                    return f"Not this command: {why}."
        return None

    def _touches_ideas(self, tool_name: str, input_data: dict[str, Any]) -> bool:
        if tool_name == "Bash":
            return str(self.ideas_dir) in str(input_data.get("command", ""))
        path = _target_path(input_data)
        return bool(path) and self._within(path, self.ideas_dir)

    def _power_refusal(self, tool_name: str, input_data: dict[str, Any]) -> str | None:
        """What a power task may not do: everything else it does unasked.

        Writes are checked by where they land and what they are. A shell
        command can only be checked by what it says, so a script that writes a
        package gets past this: the skill forbids it, this catches the plain cases.
        """
        if tool_name in WRITE_TOOLS:
            target = self._resolved(_target_path(input_data))
            if target is None:
                return None
            source = self.workspace / "src"
            if target == source or source in target.parents:
                return (f"`{target}` is the running dex: saving there reloads it, once per save, "
                        "so the change arrives half made. Build in a worktree and merge once at the end (see your skill).")
            if target.name in PACKAGE_FILES or target.suffix.lower() in POWER_CONTENT_SUFFIXES:
                return f"`{target.name}` is content. {POWER_NO_CONTENT}"
            if "public" in target.parts and "assets" in target.parts:
                return f"`{target}` is a game's installed assets. {POWER_NO_CONTENT}"
            inside = self.assets_dir and self.assets_dir in target.parents
            own = target == self.task_dir or self.task_dir in target.parents
            if inside and not own and target.name not in POWER_PROJECT_FILES:
                return f"`{target}` is in dex's packages. {POWER_NO_CONTENT}"
            return None
        if tool_name == "Bash":
            command = str(input_data.get("command", ""))
            for pattern, why in POWER_FORBIDDEN:
                if pattern.search(command):
                    return f"Not this command: {why}."
            if PULLER not in command:
                hint = next((h for h in PACKAGE_COMMAND_HINTS if h in command), None)
                if hint is not None:
                    return f"This command touches packages (`{hint.strip('/(')}`). {POWER_NO_CONTENT}"
        return None

    def _delegation_refusal(self, tool_name: str, input_data: dict[str, Any]) -> str | None:
        """Why a chat may not do this itself: it is an asset task's work."""
        if tool_name in WRITE_TOOLS:
            target = self._resolved(_target_path(input_data))
            if target is None:
                return None
            if any(target == d or d in target.parents for d in self.asset_dirs):
                return f"`{target}` is the game's installed assets. {DELEGATE}"
            if target.name in PACKAGE_FILES or target.suffix.lower() in PACKAGE_SUFFIXES:
                return f"`{target.name}` is part of a package. {DELEGATE}"
            return None
        if tool_name == "Bash":
            command = str(input_data.get("command", ""))
            if PULLER in command:
                return None
            hint = next((h for h in PACKAGE_COMMAND_HINTS if h in command), None)
            if hint is not None:
                return f"This command builds, copies or installs a package (`{hint.strip('/(')}`). {DELEGATE}"
        return None

    def _auto_reason(self, tool_name: str, input_data: dict[str, Any]) -> str | None:
        """None means auto-approve; a string explains why a human is needed."""
        if self.power:
            return None
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
            command = str(input_data.get("command", ""))
            if self.scout and _scouting(command):
                return None
            ok, why = self._bash_verdict(command)
            return None if ok else why
        return f"{tool_name} has no auto-approval rule"


def _scouting(command: str) -> bool:
    """A single test run or git read, at most after a `cd`."""
    matched = SCOUT_COMMAND.match(command.strip())
    if matched is None:
        return False
    rest = command.strip()[matched.end():]
    return not any(tok in rest for tok in (";", "&", "|", "`", "$(", ">"))


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
