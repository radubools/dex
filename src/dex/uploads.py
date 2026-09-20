"""Source material a task reads and writes: `datasets/<project>/`.

Separate from `assets/<project>/`, which is what tasks *produce*. A PDF to
translate, a corpus to index, a scratch database built from one — none of that
is a package, and putting it in the assets tree would land it in the Library,
the feed and the asset backup as though dex had generated it.

Tasks have read *and* write access here: a task that downloads a source, or
builds an index beside it, is doing the work it was asked to do.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

#: Anything larger is almost certainly not a source for a study package, and
#: the whole file is held in memory while it is written.
MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Upload:
    """One stored file, and where it ended up."""

    name: str
    path: Path
    bytes: int

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "path": str(self.path), "bytes": self.bytes}


def safe_name(raw: str) -> str:
    """A filename that cannot escape its directory or surprise a shell.

    Browsers send whatever the filesystem had, including `../`, NUL, and names
    that are all punctuation. The extension is kept because it is how both the
    agent and the viewer decide what a file is.
    """
    # Only the last component: a browser may send a path, and directory
    # traversal is the whole risk here.
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFKD", base)
    base = re.sub(r"[^\w.\- ]+", "_", base).strip(" .")
    base = re.sub(r"\s+", "-", base)
    # Leading dots would hide the file from every listing dex does.
    base = base.lstrip(".")
    if not base:
        base = "attachment"
    return base[:120]


def store(project_dir: Path, name: str, data: bytes) -> Upload:
    """Write one attachment into a project's dataset directory."""
    project_dir.mkdir(parents=True, exist_ok=True)
    target = project_dir / safe_name(name)
    # A name already taken: keep both, because the operator chose both and
    # silently overwriting the earlier one loses data with no way to notice.
    stem, suffix, n = target.stem, target.suffix, 2
    while target.exists():
        target = project_dir / f"{stem}-{n}{suffix}"
        n += 1
    target.write_bytes(data)
    return Upload(name=target.name, path=target, bytes=len(data))


def resolve(project_dir: Path, names: list[str]) -> list[Upload]:
    """The named files, for the ones that really are in this project's data.

    The names come back from a browser, so each is re-checked rather than
    trusted: `../` in a name, or a path pointing at somebody else's project,
    resolves outside and is dropped. Nothing is raised — a message naming a
    file that has since been deleted should still send.
    """
    root = project_dir.resolve()
    found: dict[str, Upload] = {}
    for raw in names:
        candidate = (root / raw).resolve()
        if candidate == root or root not in candidate.parents:
            continue
        if not candidate.is_file():
            continue
        found.setdefault(
            str(candidate),
            Upload(name=candidate.name, path=candidate, bytes=candidate.stat().st_size),
        )
    return list(found.values())


def sources_note(uploads: list[Upload], project_dir: Path | None = None) -> str:
    """What a brief says about the files attached to it.

    Read where they are rather than copied: they are the operator's source, and
    a task that moved them into its package would both duplicate them and lose
    the line between what was given and what was produced. The paths are
    absolute because the agent's working directory is its own package.
    """
    if not uploads:
        return ""
    listed = "\n".join(f"- `{u.path}` ({u.bytes:,} bytes)" for u in uploads)
    where = (
        f"\n\nThey are in `{project_dir}`, this project's data directory, which "
        "you may read from and write to."
        if project_dir is not None
        else ""
    )
    return (
        "## Sources\n\n"
        "The operator attached these files for this work. Read them — they are "
        "inputs, and you may read them even though they sit outside your own "
        "package directory. Do not copy them into your package unless the brief "
        f"asks you to.{where}\n\n"
        f"{listed}"
    )


def with_sources(
    problem: str, uploads: list[Upload], project_dir: Path | None = None
) -> str:
    """A brief with its attachments named at the end."""
    note = sources_note(uploads, project_dir)
    return f"{problem}\n\n{note}" if note else problem
