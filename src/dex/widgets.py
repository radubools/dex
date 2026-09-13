"""Project widgets: which viewer opens which file.

Two halves, deliberately kept apart:

- **The code** lives at the top level in `widgets/<name>/`, is built to one ESM
  bundle, and is served statically. It is shared across projects and authored
  only by an admin.
- **The rules** live per project in `assets/<project>/widgets.json`, next to the
  work they describe, so a project decides which of the available widgets opens
  which of its files without touching anyone else's.

Nothing here is loaded into the server process. A widget is a file on disk that
the browser fetches, so adding or rebuilding one takes effect on the next page
that asks for it -- no restart, and no way for a broken widget to take dex down.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("dex.widgets")

#: The per-project rules file, beside that project's AGENTS.md.
REGISTRY_NAME = "widgets.json"

#: The manifest each widget directory carries.
MANIFEST_NAME = "widget.json"

#: What a built widget must produce. The browser imports exactly this.
ENTRY_NAME = "dist/index.js"


@dataclass
class Widget:
    """One built widget, as the browser will load it."""

    name: str
    title: str
    description: str = ""
    #: Cache key. `import()` and `<script>` both cache by URL for the life of
    #: the page, so a rebuilt widget needs a changing URL or the browser keeps
    #: running the old bundle -- an hour of debugging a file you already fixed.
    version: str = "0"
    built: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "version": self.version,
            "built": self.built,
            "url": f"/widgets/{self.name}/{ENTRY_NAME}?v={self.version}",
        }


@dataclass
class Rule:
    """One "open these files with that widget" line from a project registry."""

    widget: str
    #: Matched against the end of the filename, so multi-part suffixes work:
    #: `.pose.json` is what distinguishes a pose from every other JSON file.
    extensions: tuple[str, ...] = ()
    #: fnmatch patterns against the bare filename, for the cases where the
    #: extension is too coarse -- `manifest.json`, `narrated_*.mp4`.
    filenames: tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        name = Path(path).name.lower()
        if any(name.endswith(ext.lower()) for ext in self.extensions):
            return True
        return any(fnmatch.fnmatch(name, pattern.lower()) for pattern in self.filenames)

    def to_json(self) -> dict[str, Any]:
        return {
            "widget": self.widget,
            "extensions": list(self.extensions),
            "filenames": list(self.filenames),
        }


def widgets_dir(workspace: Path) -> Path:
    return workspace / "widgets"


def available(workspace: Path) -> list[Widget]:
    """Every widget on disk, built or not.

    Unbuilt ones are listed too rather than hidden: "I wrote it and it does not
    appear" is a much worse thing to debug than a row that says `built: false`.
    """
    root = widgets_dir(workspace)
    if not root.is_dir():
        return []
    found: list[Widget] = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = directory / MANIFEST_NAME
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("widget %s has an unreadable %s: %s", directory.name, MANIFEST_NAME, exc)
            continue
        entry = directory / ENTRY_NAME
        found.append(
            Widget(
                name=str(manifest.get("name") or directory.name),
                title=str(manifest.get("title") or directory.name),
                description=str(manifest.get("description") or ""),
                # The built file's mtime: it changes exactly when the bundle
                # does, which is the only thing the cache key has to track.
                version=str(int(entry.stat().st_mtime)) if entry.is_file() else "0",
                built=entry.is_file(),
            )
        )
    return found


def registry_path(assets_dir: Path, project: str) -> Path:
    return assets_dir / project / REGISTRY_NAME


def rules_for(assets_dir: Path, project: str) -> list[Rule]:
    """A project's rules, in the order it wrote them.

    Order is the tie-breaker: the first matching rule wins, so a specific
    pattern is placed above a general one by putting it first. That is easier to
    reason about -- and to write from a conversation -- than a priority number
    nobody can see the effect of.
    """
    path = registry_path(assets_dir, project)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("project %s has an unreadable %s: %s", project, REGISTRY_NAME, exc)
        return []

    entries = raw.get("rules") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        log.warning("project %s: %s has no `rules` list", project, REGISTRY_NAME)
        return []

    rules: list[Rule] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("widget"):
            continue
        rules.append(
            Rule(
                widget=str(entry["widget"]),
                extensions=tuple(str(e) for e in entry.get("extensions") or ()),
                filenames=tuple(str(f) for f in entry.get("filenames") or ()),
            )
        )
    return rules


def resolve(assets_dir: Path, workspace: Path, project: str, path: str) -> Widget | None:
    """The widget that should open `path`, or None to fall back.

    None is the ordinary case, not a failure: most files are markdown or code
    and the built-in viewers handle them. A rule naming a widget that is missing
    or unbuilt also returns None, so a half-finished widget degrades to the code
    viewer instead of showing an empty frame.
    """
    built = {w.name: w for w in available(workspace) if w.built}
    for rule in rules_for(assets_dir, project):
        if rule.matches(path):
            widget = built.get(rule.widget)
            if widget is None:
                log.info(
                    "project %s maps %s to widget %r, which is not built",
                    project, path, rule.widget,
                )
                return None
            return widget
    return None
