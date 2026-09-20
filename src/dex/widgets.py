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
    #: Which skill provides it, and at which version. A widget is part of a
    #: skill now, and two versions of one skill can be on disk at once — so
    #: the name alone does not say which bundle to serve.
    skill: str = ""
    skill_version: str = ""
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
            # Served straight out of the skill. There used to be a copy of
            # every widget under a top-level `widgets/`, materialised on
            # enable — which meant a bundle could be built in one place and
            # served from the other, and one silently was.
            "url": (
                f"/api/widgets/{self.skill}/{self.skill_version}/{self.name}"
                f"/index.js?v={self.version}"
            ),
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
    #: fnmatch patterns against the whole path inside the project, for the
    #: cases where the *directory* is what identifies a file. Yoga's poses are
    #: plain `<name>.json` and are told apart from a manifest only by living in
    #: `poses/`; a filename rule cannot see that, and `*.json` would claim
    #: every sidecar in the package.
    paths: tuple[str, ...] = ()
    #: fnmatch patterns on the bare filename that veto a match, checked before
    #: anything else. A path rule is a blunt instrument — `*/poses/*.json`
    #: also catches the `manifest.json` of a package that happens to be called
    #: `poses` — and a manifest is not a pose wherever it sits.
    excludes: tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        name = Path(path).name.lower()
        if any(fnmatch.fnmatch(name, pattern.lower()) for pattern in self.excludes):
            return False
        if any(name.endswith(ext.lower()) for ext in self.extensions):
            return True
        if any(fnmatch.fnmatch(name, pattern.lower()) for pattern in self.filenames):
            return True
        whole = str(path).replace("\\", "/").lower()
        return any(fnmatch.fnmatch(whole, pattern.lower()) for pattern in self.paths)

    def to_json(self) -> dict[str, Any]:
        return {
            "widget": self.widget,
            "extensions": list(self.extensions),
            "filenames": list(self.filenames),
            "paths": list(self.paths),
            "excludes": list(self.excludes),
        }


def widgets_dir(workspace: Path) -> Path:
    return workspace / "widgets"


def available(workspace: Path) -> list[Widget]:
    """Every widget every skill provides, built or not.

    Unbuilt ones are listed too rather than hidden: "I wrote it and it does not
    appear" is a much worse thing to debug than a row that says `built: false`.

    Walks the skills rather than a directory of its own. A widget belongs to a
    skill, ships inside it, and is built there; a second copy somewhere else is
    only an opportunity for the two to disagree.
    """
    from . import skills

    found: list[Widget] = []
    for skill in skills.all_skills(workspace):
        for name in skill.widgets():
            directory = skill.widgets_dir / name
            manifest_path = directory / MANIFEST_NAME
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("widget %s has an unreadable %s: %s", name, MANIFEST_NAME, exc)
                continue
            entry = directory / ENTRY_NAME
            found.append(
                Widget(
                    name=str(manifest.get("name") or name),
                    title=str(manifest.get("title") or name),
                    description=str(manifest.get("description") or ""),
                    skill=skill.name,
                    skill_version=skill.version,
                    # The built file's mtime: it changes exactly when the
                    # bundle does, which is the only thing the cache key has
                    # to track.
                    version=str(int(entry.stat().st_mtime)) if entry.is_file() else "0",
                    built=entry.is_file(),
                )
            )
    return found


def registry_path(assets_dir: Path, project: str) -> Path:
    """Where a project keeps its rules, beside its guide."""
    return assets_dir / project / REGISTRY_NAME


def bundle_path(workspace: Path, skill: str, version: str, widget: str) -> Path | None:
    """Where one widget's built bundle is, or `None` if that is not a widget.

    Every part is checked rather than joined and hoped for: these come off a
    URL, and a `..` in any of them would otherwise walk out of the workspace.
    """
    from . import skills

    found = skills.find(workspace, skill, version)
    if found is None or widget not in found.widgets():
        return None
    entry = found.widgets_dir / widget / ENTRY_NAME
    root = skills.skills_dir(workspace).resolve()
    resolved = entry.resolve()
    if root not in resolved.parents or not resolved.is_file():
        return None
    return resolved


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
                paths=tuple(str(x) for x in entry.get("paths") or ()),
                excludes=tuple(str(x) for x in entry.get("excludes") or ()),
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
