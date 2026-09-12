"""Unified diffs for file-editing tools, computed before the edit is applied.

The agent's own tool calls carry enough information to show what a `Write` or
`Edit` will do, so the UI can render a real diff at the moment the change
happens rather than after the fact.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class FileChange:
    path: str
    patch: str
    additions: int
    deletions: int

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "patch": self.patch,
            "additions": self.additions,
            "deletions": self.deletions,
        }


def preview_change(tool_name: str, data: dict[str, Any], root: Path) -> FileChange | None:
    """The change `tool_name` would make, or None if it does not touch a file."""
    raw_path = data.get("file_path") or data.get("path") or data.get("notebook_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None

    target = Path(raw_path)
    before = _read(target)

    match tool_name:
        case "Write":
            after = str(data.get("content", ""))
        case "Edit":
            old = str(data.get("old_string", ""))
            new = str(data.get("new_string", ""))
            if not old:
                return None
            after = before.replace(old, new) if data.get("replace_all") else before.replace(old, new, 1)
        case "MultiEdit":
            after = before
            for edit in data.get("edits") or []:
                if isinstance(edit, dict):
                    after = after.replace(str(edit.get("old_string", "")), str(edit.get("new_string", "")), 1)
        case _:
            return None

    if after == before:
        return None
    return build_patch(target, before, after, root)


def build_patch(path: Path, before: str, after: str, root: Path) -> FileChange:
    try:
        label = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        label = str(path)

    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
            n=3,
        )
    )
    additions = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return FileChange(path=label, patch="".join(lines), additions=additions, deletions=deletions)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
