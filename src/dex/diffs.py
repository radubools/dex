"""Unified diffs for file-editing tools, computed before the edit is applied.

The agent's own tool calls carry enough information to show what a `Write` or
`Edit` will do, so the UI can render a real diff at the moment the change
happens rather than after the fact.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A line that opens something worth naming in code: a function, a class, a type,
#: or a `const name = (…) =>` style function.
_CODE_DEFINITION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?"
    r"(?:def|class|function\*?|interface|type|enum|struct|trait|impl|fn|func)\s+([A-Za-z_$][\w$]*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(|function\b|[A-Za-z_$][\w$]*\s*=>)"
)
#: A Markdown heading: in a document the section is what a change is "in".
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_DOC_SUFFIXES = frozenset({".md", ".mdx"})
#: Past this many lines a file is not outlined: naming is a courtesy, not worth a slow write.
_MAX_OUTLINE_LINES = 10_000


@dataclass(slots=True)
class FileChange:
    path: str
    patch: str
    additions: int
    deletions: int
    #: Written where no file was: a new file, not an edit.
    created: bool = False
    #: Definitions (or a document's sections) the change added, changed inside, or removed.
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    #: The first line of the new text the change touched, 1-based.
    first_line: int | None = None

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
    change = build_patch(target, before, after, root)
    change.created = tool_name == "Write" and not target.exists()
    return change


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
    change = FileChange(path=label, patch="".join(lines), additions=additions, deletions=deletions)
    try:
        outline(change, path.suffix.lower(), before.splitlines(), after.splitlines())
    except Exception:  # naming is a courtesy: the diff stands without it
        pass
    return change


def _name(line: str, doc: bool) -> str | None:
    if doc:
        match = _HEADING.match(line)
        return f"§ {match.group(1)}" if match else None
    match = _CODE_DEFINITION.match(line)
    return (match.group(1) or match.group(2)) if match else None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _enclosing(lines: list[str], index: int, doc: bool) -> str | None:
    """The definition (or section) line `index` is part of, or that it is."""
    if not 0 <= index < len(lines):
        return None
    own = _name(lines[index], doc)
    if own:
        return own
    # The first non-blank line at or after `index` sets how deep the change sits.
    depth = next((_indent(line) for line in lines[index:index + 20] if line.strip()), 0)
    for line in reversed(lines[:index]):
        name = _name(line, doc)
        if name and (doc or _indent(line) < depth):
            return name
    return None


def outline(change: FileChange, suffix: str, before: list[str], after: list[str]) -> None:
    """Fill in what the change added, changed inside, and removed, by name.

    Read from the text alone, with no parser per language: a definition is a
    line that looks like one (`def`, `class`, `function`, …), a document's
    section is a Markdown heading. Good enough for a one-line summary.
    """
    if max(len(before), len(after)) > _MAX_OUTLINE_LINES:
        return
    doc = suffix in _DOC_SUFFIXES
    had = {n for line in before if (n := _name(line, doc))}
    has = [n for line in after if (n := _name(line, doc))]
    change.added = list(dict.fromkeys(n for n in has if n not in had))
    change.removed = list(dict.fromkeys(n for line in before if (n := _name(line, doc)) and n not in set(has)))
    changed: list[str] = []
    matcher = difflib.SequenceMatcher(None, before, after)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if change.first_line is None:
            change.first_line = min(j1 + 1, max(len(after), 1))
        # Where the new text starts in earnest: an inserted section opens with
        # a blank line that still belongs to the one before it.
        start = next((j for j in range(j1, j2) if after[j].strip()), j1)
        name = _enclosing(after, min(start, len(after) - 1), doc)
        if name and name not in change.added and name not in changed:
            changed.append(name)
    change.changed = changed


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
