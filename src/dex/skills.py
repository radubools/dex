"""Skills: a project's capability as one directory that can be copied.

What makes a project work — the helpers its tasks import, the widgets that open
its files, the guidance explaining both — used to be spread across four
directories, plus a set of Python packages declared nowhere. Copying "what
makes yoga work" to another install meant finding all of it by hand.

A skill gathers it:

    skills/<name>/
        SKILL.md          what it is, appended to the brief of every task in a
                          project that has it enabled
        skill.json        {name, version, updated, description, requires}
        requirements.txt  the Python packages its utils need
        utils/            modules materialised into a project's utils/
        widgets/<w>/      widget.json + src + a committed dist/index.js
        bind.json         the widgets.json rules this skill contributes

**The skill is the source of truth.** A task that promotes a helper writes it
here, and the skill's version changes; `assets/<project>/utils/` is then
materialised from every skill the project has enabled. It has to work that way
round: 147 generated packages already do `sys.path.insert(0, HERE.parent)` and
`import utils.rig`, so that path is a runtime contract for work on disk and the
modules cannot simply move.

Versions are a short content hash. Two installs that both edit a skill diverge,
and reconciling them is git's job rather than something reimplemented here.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("dex.skills")

#: Where skills live, under the workspace.
SKILLS_DIR = "skills"

#: A skill's own generated index of what its modules offer. Regenerated when
#: the skill is published, so it always describes the source it ships with —
#: which is what lets a skill arrive at another install already describing
#: itself instead of waiting for a task to run there.
INDEX_NAME = "API.md"

#: The manifest each skill carries. Excluded from its own hash, because it
#: holds the hash.
MANIFEST_NAME = "skill.json"

#: A version is a *subdirectory* of the skill: `skills/yoga-figure/1fc7b2/`.
#:
#: Two versions can then sit side by side, which is the whole of migration — a
#: newer one arrives from another install, both are on disk, and a project
#: moves across when somebody ticks it. A version kept only inside `skill.json`
#: would mean the arriving copy overwrote the one in use.
#:
#: Nested rather than `yoga-figure@1fc7b2` at the top level: `skills/` then
#: lists the capabilities an install has, one entry each, instead of one entry
#: per version of each. Ten versions of three skills is three directories here
#: and thirty in the flat layout.

#: How many hex characters of the digest a version shows. Short enough to read
#: aloud, long enough that two versions of one skill will not collide.
VERSION_CHARS = 6

#: Never hashed. This is the wrong set to *copy* with — a copy that dropped
#: the manifest would not be a skill at all — which is what `JUNK` is for.
#:
#: `skill.json` is here because it carries the hash, so hashing it would never
#: settle. `dist/` is here for a subtler version of the same problem: a bundle
#: embeds the path of the source it was built from, that path contains the
#: version, and the version is this hash. Publishing renamed the directory,
#: which changed the bundle, which changed the hash, which demanded another
#: publish — a skill with a widget could never reach a stable version.
#:
#: So a version describes a skill's **source**. The bundle is derived from it
#: and ships beside it; `available()` reports whether it is built, and its URL
#: carries its own mtime, so nothing needs the hash to notice a rebuild.
IGNORED = {"__pycache__", ".DS_Store", ".pytest_cache", "dist", MANIFEST_NAME}

#: Never copied and never shipped: build artefacts belonging to whoever last
#: imported or installed something, not to the skill.
JUNK = ("__pycache__", ".DS_Store", ".pytest_cache", "node_modules")


@dataclass
class Skill:
    """One skill on disk."""

    name: str
    path: Path
    description: str = ""
    version: str = ""
    updated: float = 0.0
    #: Python packages its utils import, beyond the standard library.
    requires: list[str] = field(default_factory=list)

    @property
    def utils_dir(self) -> Path:
        return self.path / "utils"

    @property
    def widgets_dir(self) -> Path:
        return self.path / "widgets"

    def modules(self) -> list[Path]:
        """The util modules this skill contributes, `__init__.py` aside.

        The package marker belongs to the materialised directory rather than to
        any one skill — several skills share it, and whichever was copied last
        would otherwise own it.
        """
        if not self.utils_dir.is_dir():
            return []
        return sorted(
            p for p in self.utils_dir.rglob("*")
            if p.is_file()
            and p.name not in ("__init__.py", INDEX_NAME)
            and not any(part in IGNORED for part in p.relative_to(self.utils_dir).parts)
        )

    @property
    def index(self) -> Path:
        """This skill's own `API.md`."""
        return self.utils_dir / INDEX_NAME

    def widgets(self) -> list[str]:
        """Widget names this skill provides."""
        if not self.widgets_dir.is_dir():
            return []
        return sorted(
            d.name for d in self.widgets_dir.iterdir()
            if d.is_dir() and (d / "widget.json").is_file()
        )

    def rules(self) -> list[dict[str, Any]]:
        """The `widgets.json` rules this skill contributes."""
        bind = self.path / "bind.json"
        if not bind.is_file():
            return []
        try:
            loaded = json.loads(bind.read_text())
        except ValueError:
            log.warning("%s has an unreadable bind.json", self.name)
            return []
        rules = loaded.get("rules") if isinstance(loaded, dict) else loaded
        return [r for r in (rules or []) if isinstance(r, dict)]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "updated": self.updated,
            "requires": self.requires,
            "modules": [p.name for p in self.modules()],
            "widgets": self.widgets(),
        }


def skills_dir(workspace: Path) -> Path:
    return workspace / SKILLS_DIR


def version_dir(workspace: Path, name: str, version: str) -> Path:
    """Where one version of one skill lives."""
    return skills_dir(workspace) / name / version


def label(name: str, version: str) -> str:
    """How a name and version read in a message. Not a path."""
    return f"{name}@{version}" if version else name


def find(workspace: Path, name: str, version: str = "") -> Skill | None:
    """One skill by name, at a version, or the only one of that name."""
    matching = [s for s in all_skills(workspace) if s.name == name]
    if version:
        return next((s for s in matching if s.version == version), None)
    return matching[0] if len(matching) == 1 else None


def fingerprint(path: Path) -> str:
    """A short content hash of everything in a skill.

    Over the file *names* as well as their bytes, so renaming a module is a
    change. `skill.json` is excluded because it carries the answer, and
    `__pycache__` because it is a build artefact of whoever last imported it.
    """
    digest = hashlib.sha256()
    for file in sorted(_hashable(path)):
        digest.update(str(file.relative_to(path)).encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:VERSION_CHARS]


def _hashable(path: Path) -> Iterable[Path]:
    for file in path.rglob("*"):
        if not file.is_file():
            continue
        if any(part in IGNORED for part in file.relative_to(path).parts):
            continue
        yield file


def third_party_imports(module: Path) -> set[str]:
    """What a module imports from outside the standard library.

    Parsed rather than grepped: these modules explain themselves with usage
    examples in their docstrings, and `from utils import rig` inside a
    docstring is not a dependency.
    """
    try:
        tree = ast.parse(module.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    # `utils` is the materialised package itself, not a dependency of it.
    return {m for m in found if m not in sys.stdlib_module_names and m != "utils"}


def read(path: Path) -> Skill | None:
    """One skill from its directory, or `None` if it is not one."""
    manifest = path / MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text())
    except ValueError:
        log.warning("%s has an unreadable %s", path.name, MANIFEST_NAME)
        return None
    # The directory is the identity: `skills/<name>/<version>/`. `skill.json`
    # still records both so a copied directory can be checked against what it
    # says it is, but a rename is what changes a version, not an edit to the
    # manifest.
    return Skill(
        name=str(data.get("name") or path.parent.name),
        path=path,
        description=str(data.get("description", "")),
        # No fallback to the manifest: the directory *is* the version.
        version=path.name,
        updated=float(data.get("updated") or 0.0),
        requires=[str(r) for r in data.get("requires") or []],
    )


def all_skills(workspace: Path) -> list[Skill]:
    """Every version of every skill, by name then version.

    Two levels: `skills/<name>/<version>/`. A subdirectory with no
    `skill.json` is not a version, which is what keeps a stray directory from
    being read as one.
    """
    root = skills_dir(workspace)
    if not root.is_dir():
        return []
    found = [
        read(version)
        for named in sorted(root.iterdir()) if named.is_dir()
        for version in sorted(named.iterdir()) if version.is_dir()
    ]
    return [s for s in found if s is not None]


def _write_index(skill: Skill) -> None:
    """Regenerate the skill's own `API.md` from its modules.

    Before the hash, not after: the index is a pure function of the modules,
    so generating it first means the version covers a skill that is internally
    consistent. Doing it the other way round would be the `dist/` problem —
    publish, regenerate, hash changes, publish again.
    """
    if not skill.utils_dir.is_dir():
        return
    try:
        from .tools.utils_api import render_skill

        body = render_skill(skill.utils_dir)
    except Exception:
        log.exception("could not index %s", skill.name)
        return
    if not body.strip():
        skill.index.unlink(missing_ok=True)
        return
    if not skill.index.is_file() or skill.index.read_text(encoding="utf-8") != body:
        skill.index.write_text(body, encoding="utf-8")


def _sweep(path: Path) -> None:
    """Remove build artefacts a rename carried along."""
    import shutil

    for junk in path.rglob("*"):
        if junk.is_dir() and junk.name in JUNK:
            shutil.rmtree(junk, ignore_errors=True)


def publish(skill: Skill) -> Skill:
    """Give a changed skill its new version, and the directory to match.

    The version is a content hash, and it lives in the directory name — so a
    skill whose contents moved on has to *become* a new directory. Renaming
    rather than editing a field is what lets two versions coexist: the copy
    that arrives from another install does not overwrite the one in use, and a
    project moves between them by being told to.

    Returns the skill at its new path. A skill whose contents already match its
    name is returned untouched, so this is safe to call on anything.
    """
    _write_index(skill)
    version = fingerprint(skill.path)
    skill.requires = sorted(
        {m for module in skill.modules() if module.suffix == ".py"
         for m in third_party_imports(module)}
    )

    if version != skill.version:
        target = skill.path.parent / version
        if target.exists() and target != skill.path:
            # Somebody already has this exact content under this exact name.
            # Two directories would then differ only by which was written
            # first, which is not a distinction anybody can act on.
            raise Collision(
                f"{target.name} already exists with the same contents. "
                "Nothing to publish."
            )
        if target != skill.path:
            skill.path.rename(target)
            skill.path = target
        skill.version = version
        # A rename takes the whole directory, `__pycache__` included — and
        # that is a build artefact of whoever last imported the module, not
        # part of the skill. It is already out of the hash and out of
        # `modules()`; sweeping it here keeps it out of what gets committed.
        _sweep(skill.path)

    skill.updated = time.time()
    (skill.path / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "name": skill.name,
                "description": skill.description,
                "version": skill.version,
                "updated": skill.updated,
                "requires": skill.requires,
            },
            indent=2,
        )
        + "\n"
    )
    return skill



# ------------------------------------------------------------- enable/disable


#: What a project records about the skills it uses, beside its guide.
ENABLED_NAME = "skills.json"

#: Files in a project's `utils/` that no skill owns and materialising must not
#: touch. `API.md` is regenerated from the modules on every run; `__init__.py`
#: is the package marker, and it belongs to the project rather than to whichever
#: skill happened to be copied last.
PROJECT_OWNED = {"__init__.py", "API.md"}

#: The marker put on a rule that came from a skill, so re-materialising can
#: replace those and leave a project's own rules alone.
RULE_OWNER = "skill"


class Collision(Exception):
    """Two skills claiming the same name. Refused rather than resolved."""


@dataclass
class Enabled:
    """Which skills a project uses, and what was written for them."""

    skills: dict[str, str] = field(default_factory=dict)
    #: Module filenames materialised into `utils/`, so disabling can take back
    #: exactly what enabling put there instead of guessing from the directory.
    modules: list[str] = field(default_factory=list)
    #: Widget directories copied into the workspace's `widgets/`, for the same
    #: reason.
    widgets: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "skills": self.skills,
            "modules": sorted(self.modules),
            "widgets": sorted(self.widgets),
        }


def enabled_path(project_dir: Path) -> Path:
    return project_dir / ENABLED_NAME


def read_enabled(project_dir: Path) -> Enabled:
    """What this project has on. An install with no file has nothing on."""
    path = enabled_path(project_dir)
    if not path.is_file():
        return Enabled()
    try:
        data = json.loads(path.read_text())
    except ValueError:
        log.warning("%s has an unreadable %s", project_dir.name, ENABLED_NAME)
        return Enabled()
    return Enabled(
        skills={str(k): str(v) for k, v in (data.get("skills") or {}).items()},
        modules=[str(m) for m in data.get("modules") or []],
        widgets=[str(w) for w in data.get("widgets") or []],
    )


def check_collisions(chosen: list[Skill]) -> None:
    """Refuse two skills claiming one name, rather than picking a winner.

    An install where two skills both provide `utils/manifest.py`, or both a
    widget called `viewer`, is one where nobody can say which is in use. There
    is deliberately no precedence rule: the answer is to rename one, and saying
    so is more useful than quietly shadowing it.
    """
    for kind, names in (
        ("module", [(m.name, s.name) for s in chosen for m in s.modules()]),
        ("widget", [(w, s.name) for s in chosen for w in s.widgets()]),
    ):
        seen: dict[str, str] = {}
        for name, owner in names:
            if name in seen and seen[name] != owner:
                raise Collision(
                    f"{seen[name]} and {owner} both provide the {kind} "
                    f"{name!r}. Rename one — a {kind} is named for what it is, "
                    "and two of them under one name cannot both be reached."
                )
            seen[name] = owner


def materialise(
    workspace: Path,
    project_dir: Path,
    names: Iterable[str] | None = None,
    versions: dict[str, str] | None = None,
) -> Enabled:
    """Write a project's `utils/` and widget rules from the skills it has on.

    Idempotent by construction: it writes what the enabled skills say and
    removes only what a previous materialise wrote. Running it twice changes
    nothing, and running it against the skills yoga already had extracted
    changes nothing either — which is how the migration was checked.

    `names` sets the enabled list; `None` re-materialises whatever is recorded.
    `versions` pins each name to one — without it a name resolves only when
    exactly one version of it is on disk, which is how a project says which of
    two it is on.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    was = read_enabled(project_dir)
    wanted = list(was.skills) if names is None else list(dict.fromkeys(names))

    # A project is on one version of a skill, named in `skills.json`. Two
    # versions of one name cannot both be enabled: they provide the same
    # modules, and `check_collisions` would refuse them anyway — this says so
    # in the operator's terms instead.
    pinned = dict(versions or was.skills)
    duplicates = [n for n in wanted if wanted.count(n) > 1]
    if duplicates:
        raise Collision(
            f"{duplicates[0]} is listed twice. A project is on one version of "
            "a skill; tick the version you want and the other comes off."
        )

    chosen: list[Skill] = []
    missing: list[str] = []
    for name in wanted:
        skill = find(workspace, name, pinned.get(name, ""))
        if skill is None:
            # The pinned version is gone, or several exist and none was named.
            skill = find(workspace, name)
        if skill is None:
            missing.append(label(name, pinned.get(name, "")))
        else:
            chosen.append(skill)
    if missing:
        raise Collision(f"no such skill: {', '.join(missing)}")
    check_collisions(chosen)

    utils = project_dir / "utils"
    utils.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for skill in chosen:
        for module in skill.modules():
            target = utils / module.relative_to(skill.utils_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            body = module.read_bytes()
            # Compared before writing so an unchanged module keeps its mtime.
            # `_refresh_utils_index` regenerates API.md when a module is newer
            # than it, and rewriting every file on every run would rebuild the
            # index on every task for no reason.
            if not target.is_file() or target.read_bytes() != body:
                target.write_bytes(body)
            written.append(str(target.relative_to(utils)))

    # Take back only what a previous materialise put there. A module somebody
    # dropped in by hand is not ours to delete, and neither is API.md.
    for stale in sorted(set(was.modules) - set(written)):
        if stale in PROJECT_OWNED:
            continue
        gone = utils / stale
        if gone.is_file():
            gone.unlink()

    _ensure_package(utils, project_dir.name)
    _write_combined_index(utils, chosen)
    _merge_rules(project_dir, chosen)

    # Widgets are not copied anywhere. They are served straight out of the
    # skill, so enabling one is only a rule in `widgets.json`.
    now = Enabled(
        skills={s.name: s.version for s in chosen},
        modules=written,
        widgets=[w for s in chosen for w in s.widgets()],
    )
    enabled_path(project_dir).write_text(json.dumps(now.to_json(), indent=2) + "\n")
    return now


def _write_combined_index(utils: Path, chosen: list[Skill]) -> None:
    """The project's `utils/API.md`, from each enabled skill's own index.

    Combined rather than regenerated by scanning the directory, so the index a
    skill ships is the index that describes it — and a skill copied to another
    install arrives already documented.
    """
    try:
        from .tools.utils_api import combine

        # By name, not by the order they were enabled. A generated index is
        # read, and alphabetical is both easier to scan and stable — enabling
        # a skill should not reshuffle the whole file.
        parts = [
            (skill.name, skill.index.read_text(encoding="utf-8"))
            for skill in sorted(chosen, key=lambda s: s.name)
            if skill.index.is_file()
        ]
        text = combine(parts)
    except Exception:
        log.exception("could not combine the utils index")
        return
    target = utils / INDEX_NAME
    if not text:
        target.unlink(missing_ok=True)
    elif not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")


def _ensure_package(utils: Path, project: str) -> None:
    """Create `__init__.py` if it is missing, and never overwrite one.

    It carries the project's own words about its tooling, and several skills
    land in this one directory — a generated marker would mean whichever skill
    was copied last silently replaced that.
    """
    marker = utils / "__init__.py"
    if marker.is_file():
        return
    marker.write_text(
        f'"""Shared tooling for the {project} packages.\n\n'
        "Materialised from the skills this project has enabled; edit the skill\n"
        'rather than these files.\n"""\n'
    )


def _merge_rules(project_dir: Path, chosen: list[Skill]) -> None:
    """Put the enabled skills' widget rules into `widgets.json`.

    A project's own rules are left exactly as they are. Only rules carrying a
    `skill` marker are replaced, so a project can bind a widget itself without
    the next materialise taking it away.
    """
    path = project_dir / "widgets.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
        except ValueError:
            log.warning("%s has an unreadable widgets.json", project_dir.name)
            existing = {}

    own = [
        r for r in existing.get("rules") or []
        if isinstance(r, dict) and not r.get(RULE_OWNER)
    ]
    from_skills = [
        {**rule, RULE_OWNER: skill.name}
        for skill in chosen
        for rule in skill.rules()
    ]
    merged = own + from_skills
    if merged == (existing.get("rules") or []):
        return  # nothing to say; leave the file's mtime and comment alone

    existing["rules"] = merged
    path.write_text(json.dumps(existing, indent=2) + "\n")


def enabled_utils_dirs(workspace: Path, project_dir: Path) -> tuple[Path, ...]:
    """The `utils/` of every skill this project has on.

    What a task may write to when it promotes a helper. It writes to the skill,
    not to the project's `utils/` — that directory is materialised from these
    and an edit made there is gone at the next enable.
    """
    # Keyed by name *and* version. Keyed by name alone, a project on one
    # version was handed whichever version sorted last — so a task promoting a
    # helper would have written into a version nobody is running.
    available = {(s.name, s.version): s for s in all_skills(workspace)}
    found = (
        available.get((name, version))
        for name, version in read_enabled(project_dir).skills.items()
    )
    return tuple(s.utils_dir for s in found if s is not None and s.utils_dir.is_dir())


def snapshot(workspace: Path, project_dir: Path, into: Path) -> dict[str, Path]:
    """Copy each enabled skill aside, before a task is allowed to write to one.

    A task writes into the versioned directory it was told about, so by the
    time it finishes that directory no longer matches its own name. Publishing
    renames it to the new version — and without this copy the old version would
    simply be gone, taking every other project that is still on it with it.

    Skills are small (the largest here is 668 KB), so this is a cheap way to
    keep "two versions coexist" true even when dex itself is what changed one.
    """
    import shutil

    available = {s.name: s for s in all_skills(workspace)}
    into.mkdir(parents=True, exist_ok=True)
    kept: dict[str, Path] = {}
    for name in read_enabled(project_dir).skills:
        skill = available.get(name)
        if skill is None:
            continue
        target = into / skill.path.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill.path, target, ignore=shutil.ignore_patterns(*JUNK))
        kept[name] = target
    return kept


def resync(
    workspace: Path, project_dir: Path, kept: dict[str, Path] | None = None
) -> list[str]:
    """Publish any skill a task changed, and materialise the result.

    A task that promotes a helper writes it into the skill. Three things then
    have to happen: the skill has to become its new version, the version it was
    before has to still exist for the projects that are on it, and this project
    has to move across.

    All of it after the run rather than during, so a task that wrote a helper
    and then failed its own checks does not leave a published version behind.

    `kept` is what `snapshot` returned. Without it the old version cannot be
    put back, and publishing simply moves the skill forward — which is right
    for a skill nobody else has enabled and wrong for one they do.

    Returns the names of the skills that changed, for the log.
    """
    import shutil

    changed: list[str] = []
    recorded = dict(read_enabled(project_dir).skills)
    moved: dict[str, str] = {}

    for name, version in list(recorded.items()):
        skill = find(workspace, name, version) or find(workspace, name)
        if skill is None:
            continue
        if fingerprint(skill.path) == skill.version:
            continue

        was = skill.path
        publish(skill)
        changed.append(name)
        moved[name] = skill.version

        # Put the version this project was on back where it was, so any other
        # project still recorded against it keeps working.
        original = (kept or {}).get(name)
        if original is not None and not was.exists():
            shutil.copytree(original, was, ignore=shutil.ignore_patterns(*JUNK))

    # This project moves to what it just produced; everybody else stays where
    # they are until somebody ticks them across. A promotion is not a thing to
    # apply to projects that were not part of it.
    if moved:
        recorded.update(moved)
        materialise(workspace, project_dir, list(recorded), versions=recorded)
    return changed


#: A directory whose version is not a real digest is a **draft** — the design
#: chat's working copy. Publishing one computes its true version, renames it,
#: and moves every project that had that skill across.
DRAFT = "draft"


def is_draft(skill: Skill) -> bool:
    """Whether this is a working copy rather than a published version."""
    return not (
        len(skill.version) == VERSION_CHARS
        and all(c in "0123456789abcdef" for c in skill.version)
    )


def fork(workspace: Path, name: str, version: str = "") -> Skill:
    """Copy a skill to a draft the design chat can edit.

    Changes are never made to a published version in place. A published
    version is what some project is running on and what another install may
    have copied; editing it would change the thing under them and leave the
    version — which is a hash of the contents — describing something that no
    longer exists. So authoring works on a copy, and publishing gives the copy
    its own identity.

    A name with nothing behind it yet gets an empty draft, which is how a new
    skill starts.
    """
    import shutil

    root = skills_dir(workspace)
    (root / name).mkdir(parents=True, exist_ok=True)
    draft = root / name / DRAFT
    if draft.exists():
        shutil.rmtree(draft)

    # `find` is strict: with several versions on disk and none named it
    # answers nothing, which is right for materialising and wrong here. An
    # author who did not say which version means the current one, so take the
    # most recently published.
    source = find(workspace, name, version)
    if source is None and not version:
        candidates = [s for s in all_skills(workspace)
                      if s.name == name and not is_draft(s)]
        source = max(candidates, key=lambda s: s.updated, default=None)
    if source is None:
        draft.mkdir(parents=True)
        (draft / "utils").mkdir()
        (draft / MANIFEST_NAME).write_text(
            json.dumps({"name": name, "description": ""}, indent=2) + "\n"
        )
    else:
        shutil.copytree(source.path, draft, ignore=shutil.ignore_patterns(*JUNK))
    published = read(draft)
    assert published is not None
    return published


def drafts(workspace: Path) -> list[Skill]:
    """Every working copy on disk."""
    return [s for s in all_skills(workspace) if is_draft(s)]


def adopt(workspace: Path, assets_dir: Path) -> list[tuple[str, str, list[str]]]:
    """Publish every draft, and move each project onto what it produced.

    This is the design chat's half of the story, and it differs from a task's
    on purpose. A task that promotes a helper is doing it in passing, so only
    its own project moves. A design turn that changes a skill is an authoring
    act — deliberate, and tested before it lands — so **every project on that
    skill moves to the new version**. Leaving them behind would mean the thing
    just designed is running nowhere.

    Returns `(name, version, projects moved)` per published draft.
    """
    import shutil

    done: list[tuple[str, str, list[str]]] = []
    for draft in drafts(workspace):
        try:
            published = publish(draft)
        except Collision:
            # The draft hashes to a version that already exists: whatever was
            # edited made no difference to the contents. There is nothing to
            # publish, and the right outcome is to move onto what is already
            # there rather than to fail the turn over it.
            existing = find(workspace, draft.name, fingerprint(draft.path))
            if existing is None:
                log.warning("%s could not be published", draft.path.name)
                continue
            shutil.rmtree(draft.path, ignore_errors=True)
            published = existing
        if is_draft(published):
            log.warning("%s could not be published", draft.path.name)
            continue

        moved: list[str] = []
        for project_dir in sorted(p for p in assets_dir.iterdir() if p.is_dir()):
            recorded = read_enabled(project_dir).skills
            if published.name not in recorded:
                continue
            if recorded[published.name] == published.version:
                continue
            recorded[published.name] = published.version
            try:
                materialise(workspace, project_dir,
                            list(recorded), versions=recorded)
            except Collision as exc:
                # One project refusing does not undo the publish, and must not
                # stop the others: the version exists either way.
                log.warning("could not move %s to %s: %s",
                            project_dir.name, published.version, exc)
                continue
            moved.append(project_dir.name)
        done.append((published.name, published.version, moved))
    return done
