"""Local directories the operator lets a project read.

Some source material is too big to upload and already lives somewhere on the
machine — a mounted volume of scans, an export a colleague dropped in a share.
`datasets/<project>/` is where a project's input belongs, so an external
directory joins it as a **symlink** rather than a copy: the material stays
where it is, and nothing duplicates hundreds of gigabytes to make it readable.

The grant is deliberate and narrow:

- **The operator asks for it**, in the project designer, naming the path. The
  link is created by `mcp__dex__link_local_source`, which only a design turn
  has, and which escalates for approval — the dialog shows the *resolved*
  target, because that is the fact worth confirming.
- **It is read-only.** The permission policy refuses writes that resolve inside
  a linked target. An input is an input; a task that edited the operator's own
  archive would be changing something dex does not own and cannot restore.
- **It is verified when it is made.** A link that resolves but cannot be listed
  — a volume that is not mounted, a directory the process cannot read — is
  worse than no link, because it looks like access.

Why `datasets/` and not `assets/`: the Library lists a project by walking
`assets/<project>/*` with `rglob`, which **follows a symlinked directory**. A
link there would pull an entire external volume into every listing, the review
feed and the asset backup. See [13](../../design/13_source_material.md).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("dex.local_sources")

#: How many entries `verify` will count before it stops and says "at least".
#: An external volume can hold millions, and the question being answered is
#: "can this be read", not "how much is there".
COUNT_LIMIT = 5000

#: How many names come back as evidence.
SAMPLE = 8

#: Characters allowed in a link name. It becomes a path component inside the
#: project's data directory and travels in a URL to the preview pane.
SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")

#: Directories never linked, whatever the operator types. Matched against the
#: *resolved* target and its parents, so a symlink pointing into one of them is
#: refused too.
#:
#: Not a security boundary — a design turn is operator-driven and the operator
#: owns the machine. It is a guard against the path that was pasted by mistake
#: or produced by a model completing a plausible-looking string, where the
#: failure is silent and the blast radius is a credential store.
DENIED = (
    ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".config/gcloud",
    "Library/Keychains", "Library/Cookies", "Library/Application Support/Firefox",
)

#: Absolute directories never linked, by exact path or as an ancestor.
DENIED_ABSOLUTE = (
    "/etc", "/private/etc", "/dev", "/proc", "/sys",
    "/System", "/Library/Keychains", "/Library/Security",
    "/var/root", "/var/db", "/private/var/root", "/private/var/db",
)


class Blocked(Exception):
    """The path was refused. The message is shown to the operator verbatim."""


@dataclass
class Link:
    """One linked directory, and what listing it through the link found."""

    #: The link's own name inside `datasets/<project>/`.
    name: str
    #: Where the link points, resolved.
    target: Path
    #: The link itself.
    path: Path
    #: Immediate entries, counted through the link.
    entries: int
    #: True when the count stopped at `COUNT_LIMIT` rather than running out.
    partial: bool
    #: A few names, as evidence that reading works.
    sample: list[str]
    #: False when the link exists but cannot be listed — an unmounted volume.
    readable: bool = True

    def describe(self) -> str:
        """One line for the design turn to repeat back to the operator."""
        if not self.readable:
            return f"`{self.name}` → `{self.target}` — linked, but nothing can be read from it."
        count = f"{self.entries:,}{'+' if self.partial else ''}"
        shown = ", ".join(self.sample[:4])
        return (
            f"`{self.name}` → `{self.target}` — {count} entries"
            + (f", including {shown}" if shown else "")
        )

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "target": str(self.target),
            "entries": self.entries,
            "partial": self.partial,
            "sample": self.sample,
            "readable": self.readable,
        }


def check(raw: str, workspace: Path) -> Path:
    """Resolve a path the operator gave, or say why it cannot be linked.

    Every check runs against the **resolved** path. A symlink that points at
    `~/.ssh` is `~/.ssh`, and checking the name as typed would miss it.
    """
    text = (raw or "").strip()
    if not text:
        raise Blocked("No path given.")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise Blocked(
            f"`{text}` is relative. Give the full path, starting from `/`."
        )
    try:
        target = candidate.resolve(strict=True)
    except OSError as exc:
        raise Blocked(
            f"`{text}` cannot be reached: {exc.strerror or exc}. "
            "If it is on a removable volume, check that it is mounted."
        ) from exc
    if not target.is_dir():
        raise Blocked(f"`{target}` is not a directory. Link the directory that holds it.")

    home = Path.home().resolve()
    if target == home:
        raise Blocked(
            f"`{target}` is your whole home directory, which is too broad to "
            "link. Name the directory that actually holds the material."
        )
    if target == Path("/"):
        raise Blocked("`/` is the whole filesystem. Name a directory inside it.")

    for denied in DENIED_ABSOLUTE:
        root = Path(denied)
        if target == root or root in target.parents:
            raise Blocked(f"`{target}` is inside `{denied}`, which dex will not link.")
    for denied in DENIED:
        if (home / denied).resolve() in (target, *target.parents):
            raise Blocked(f"`{target}` is inside `~/{denied}`, which dex will not link.")

    # Linking dex's own tree at itself would put the Library inside the
    # datasets tree inside the Library, and a walker following the link would
    # not stop.
    root = workspace.resolve()
    if target == root or root in target.parents or target in root.parents:
        raise Blocked(
            f"`{target}` overlaps dex's own workspace (`{root}`). A project's "
            "own files are already reachable; link something outside it."
        )

    try:
        with os.scandir(target) as entries:
            next(iter(entries), None)
    except OSError as exc:
        raise Blocked(
            f"`{target}` exists but cannot be listed: {exc.strerror or exc}."
        ) from exc
    return target


def link_name(target: Path, taken: set[str]) -> str:
    """A readable name for the link, derived from the directory's own."""
    cleaned = "".join(c if c in SAFE else "-" for c in target.name).strip("-.")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    base = (cleaned or "source")[:60]
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def verify(path: Path) -> tuple[bool, int, bool, list[str]]:
    """List through the link. Returns `(readable, entries, partial, sample)`.

    Through the **link**, not the target: the question is whether the thing
    just created works, and resolving it first would answer a different one.
    """
    names: list[str] = []
    count = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                count += 1
                if len(names) < SAMPLE:
                    names.append(entry.name + ("/" if entry.is_dir() else ""))
                if count >= COUNT_LIMIT:
                    return True, count, True, names
    except OSError as exc:
        log.warning("linked source %s cannot be listed: %s", path, exc)
        return False, 0, False, []
    return True, count, False, names


def link(datasets_dir: Path, raw: str, workspace: Path, name: str = "") -> Link:
    """Link a local directory into a project's data directory, and verify it.

    Returns the existing link when the same target is already linked — asking
    twice for the same directory is a repeat, not a second source.
    """
    target = check(raw, workspace)
    datasets_dir.mkdir(parents=True, exist_ok=True)

    for existing in links(datasets_dir):
        if existing.target == target:
            log.info("%s is already linked as %s", target, existing.name)
            return existing

    taken = {p.name for p in datasets_dir.iterdir()}
    chosen = name.strip() or link_name(target, taken)
    if chosen != link_name(Path(chosen), set()) or chosen in taken:
        raise Blocked(f"`{chosen}` is not a usable link name, or is already taken.")

    path = datasets_dir / chosen
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        raise Blocked(f"Could not create the link: {exc.strerror or exc}.") from exc

    readable, entries, partial, sample = verify(path)
    if not readable:
        # A link that cannot be read is worse than none, because it looks like
        # access and fails later, inside a task, as a missing file.
        path.unlink(missing_ok=True)
        raise Blocked(
            f"`{target}` could be linked but nothing can be read through it, so "
            "the link was removed. Check that the volume is mounted and readable."
        )
    log.info("linked %s -> %s (%d entries)", path, target, entries)
    return Link(
        name=chosen, target=target, path=path,
        entries=entries, partial=partial, sample=sample,
    )


def links(datasets_dir: Path) -> list[Link]:
    """Every linked directory in a project's data directory, by name.

    A link whose target has gone — an unmounted volume — is still listed, with
    `readable` false. Hiding it would make a task's "file not found" the first
    anybody hears of it.
    """
    if not datasets_dir.is_dir():
        return []
    found: list[Link] = []
    for entry in sorted(datasets_dir.iterdir()):
        if not entry.is_symlink():
            continue
        try:
            target = entry.resolve()
        except OSError:
            target = Path(os.readlink(entry))
        readable, entries, partial, sample = (
            verify(entry) if entry.is_dir() else (False, 0, False, [])
        )
        found.append(Link(
            name=entry.name, target=target, path=entry,
            entries=entries, partial=partial, sample=sample, readable=readable,
        ))
    return found


def targets(datasets_dir: Path) -> tuple[Path, ...]:
    """Where a project's links point, for the permission policy to refuse writes."""
    return tuple(link.target for link in links(datasets_dir))


def unlink(datasets_dir: Path, name: str) -> bool:
    """Remove one link. The directory it pointed at is untouched."""
    path = datasets_dir / name
    if name != Path(name).name or not path.is_symlink():
        return False
    path.unlink()
    log.info("removed linked source %s", path)
    return True
