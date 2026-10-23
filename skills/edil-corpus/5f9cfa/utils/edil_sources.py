"""Find eDIL projects and their initial-report PDFs, and pick a spread of them.

The export is three levels deep — building type, project, then `Init_Report/`,
`Mo_Updates/`, `Supplemental/` — and about thirty of those subfolder names are
misspelled (`Init_Reprot`, `Init-Report`, `Initial Report`), so a subfolder is an
initial report when its name, lower-cased and stripped of leading `_ -`, starts
with `init`. Nothing here compares against the tidy name.

    from utils import edil_sources
    projects = edil_sources.projects()                 # every project with an init PDF
    picked = edil_sources.select(projects, 30)         # spread across building types
    edil_sources.select(projects, building_type="Medical")   # or narrow it

`select` is deterministic: the same arguments return the same projects, so a
backfill can be re-run and a later run can ask for "the next 30" with `skip=`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

#: The read-only link to the eDIL PACS mass-load export. A symlink: walk it with
#: `Path.iterdir`, which follows it, rather than `find` without a trailing slash.
DATASET = Path("/Users/raduparadovschi/dex/datasets/construction/eDIL_PACS_Projects_Mass-Load")

INIT_PREFIX = "init"


@dataclass(frozen=True)
class Project:
    building_type: str
    folder: str
    path: Path
    #: Every PDF under the project's initial-report subfolder(s), recursing.
    init_pdfs: tuple[Path, ...] = field(default=())

    @property
    def key(self) -> str:
        return f"{self.building_type}/{self.folder}"


def _is_init(name: str) -> bool:
    return name.lower().lstrip("_ -").startswith(INIT_PREFIX)


def _pdfs_under(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
        key=lambda p: str(p).lower(),
    )


def init_pdfs(project: Path) -> list[Path]:
    """Every PDF under any init-report-type subfolder of one project folder."""
    found: list[Path] = []
    for sub in sorted((p for p in project.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        if _is_init(sub.name):
            found.extend(_pdfs_under(sub))
    return found


def projects(dataset: Path = DATASET, *, with_pdf: bool = True) -> list[Project]:
    """Every project folder in the export, sorted by building type then folder name."""
    out: list[Project] = []
    for bt in sorted((p for p in dataset.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        for proj in sorted((p for p in bt.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
            pdfs = tuple(init_pdfs(proj))
            if with_pdf and not pdfs:
                continue
            out.append(Project(bt.name, proj.name, proj, pdfs))
    return out


def select(
    candidates: list[Project],
    n: int | None = None,
    *,
    building_type: str | None = None,
    folders: list[str] | None = None,
    seed: int = 0,
    skip: int = 0,
) -> list[Project]:
    """A deterministic spread of projects: round-robin over building types.

    `building_type` keeps types whose folder name contains it (case-insensitive);
    `folders` keeps only the named project folders (exact, or a substring match
    when nothing matches exactly). Within each building type the projects are
    shuffled with `seed`, then taken one type at a time so 30 projects cover 30
    types before any type gets a second. `skip` drops that many from the front of
    the same ordering — `select(c, 30, skip=30)` is the next batch.
    """
    pool = candidates
    if building_type:
        needle = building_type.lower()
        pool = [p for p in pool if needle in p.building_type.lower()]
    if folders:
        # Each name matches its folder exactly, or else as a substring — per name,
        # so one exact name does not turn the others off.
        keep = set()
        for f in folders:
            exact = [p for p in pool if p.folder == f]
            keep.update(p.key for p in (exact or [p for p in pool if f.lower() in p.folder.lower()]))
        pool = [p for p in pool if p.key in keep]

    by_type: dict[str, list[Project]] = {}
    for p in pool:
        by_type.setdefault(p.building_type, []).append(p)
    rng = random.Random(seed)
    types = sorted(by_type)
    rng.shuffle(types)
    for t in types:
        rng.shuffle(by_type[t])

    ordered: list[Project] = []
    depth = 0
    while any(len(by_type[t]) > depth for t in types):
        ordered.extend(by_type[t][depth] for t in types if len(by_type[t]) > depth)
        depth += 1
    ordered = ordered[skip:]
    return ordered if n is None else ordered[:n]
