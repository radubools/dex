"""Projects: a named body of work with its own guide and its own threads.

On disk a project is a subdirectory of the assets root holding `AGENTS.md` and
one directory per task. In the database it owns its threads and tasks, so
switching project switches everything the UI shows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database

log = logging.getLogger("dex.projects")

#: The guide a new project starts from, so the design chat has something to edit
#: rather than a blank page.
STARTER_GUIDE = """# Project: {name}

{description}

## The environment is ready — do not go looking

Everything below is installed and working. **Do not check whether a tool exists,
do not try to install anything, and do not add a fallback for a missing
dependency.**

| Need | Use |
|---|---|
| Anything Python | `{{python}}` — the interpreter named in your brief |

Your shell runs one command at a time: no `&&`, no pipes, no `cd`. Pass absolute
paths.

## What to produce

Describe the deliverable for a task in this project here: the files, what each
one is for, and how to tell when it is done.

## Shared utilities — prove it first, then ask at the end

Sometimes the useful thing you write is not part of the package at all: a
helper that validates the deliverable, a build step, a check every package
needs. That is **tooling**,
and the next task will want it too. Copying it from package to package is how
six subtly different versions of one function end up in the project.

Shared tooling lives in `utils/` in the project directory — one level above your
package — as a plain Python package that any package can import:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import helpers  # noqa: E402
```

Keep the deliverable itself self-contained — a person reads it. `utils/` is for
the build side: checks, conversions, generation steps.

### Before you write a helper, read the index

Two tables at the end of this section are the whole search: *Shared utilities*,
a line per module already in the project's `utils/`, and *Kept local*, the
record of what the operator has already decided does **not** belong there. Both
are in this guide, which you have been given — so finding out what exists costs
you nothing. You do not have to list directories and you do not have to read
the modules.

**When a row looks like it might cover your case, read `utils/API.md`** — one
level above your package. It is generated from the modules themselves: every
public signature, constant, and one-line description, and none of the bodies.
It is about a tenth of the source and it is normally all you need. Reach for a
module's own source only when a signature and its line genuinely do not settle
whether it fits — and then read that one module, not the directory.

What you find lands in one of three places:

- **It does the job.** Import it and carry on — no question, no new module,
  nothing to propose. Writing a second version of a function already sitting
  there is the exact thing this section exists to prevent.
- **It nearly does the job** — right idea, wrong signature, a case it does not
  handle. **Do not edit it now.** Call it for the part it does cover and do the
  rest inside your package. Often that is the end of it; if a real gap remains,
  the change gets proposed at the end, like anything else.
- **Nothing covers it.** Write your own, locally, and carry on.

Then read *Kept local*. **If what you are about to write is listed there, the
operator has ruled it out**: write it inside your package and leave it there.
That table is their veto, not a suggestion — a row in it is closed.

### Keep them general, or they will never stop changing

A shared utility earns its place by outliving the package that wrote it. The
ones that do are **mechanism**: they compute a value or answer a question and
hand it back. The ones that do not are **policy** — they settle what counts as
correct for the package that happened to need them first, and then every
package after that needs them changed.

So what you write, and what you propose, should:

- **Return data, not verdicts or prose.** A list of problems the caller can
  read, count, ignore, or print. The moment a helper formats a message or
  decides that a package has failed, the next caller wants it to decide
  differently, and the only way to get that is to edit it.
- **Take a default, not a rule.** A threshold belongs in a keyword argument
  with a sensible default, so a package that needs a different one passes it
  and nobody touches the function.
- **Do one thing.** Two small functions the caller composes beat one that takes
  a mode flag: a caller who wants half of it can then take half.

And the tripwire: **if you find yourself adding a second or third keyword
argument for one caller, what you want is not this function.** Write yours
locally. A utility that grows a knob per package is one that every task will
keep editing, and a shared thing that changes every week is worse than no
shared thing at all — nobody can rely on what it does.

The same instinct settles the "nearly does the job" case above. **Prefer
composing over changing.** If you can call the utility and do the remaining bit
yourself inside your package, that is not a change worth proposing — that is
the utility working as intended. Propose a change only when the gap is one
every future caller would hit too.

### While you work, keep it local

Whatever you write stays **inside your task directory** and is used there. Do
not reach for `utils/` mid-task. An untested module promoted into shared space
is worse than no shared module at all, and stopping to ask halfway through
interrupts work that has not yet shown the helper is worth sharing.

### At the end, once everything is green, ask

As the **last step before your summary** — after the deliverable is built and
every check you can run has passed, so the code has actually run and done its
job — look back at what you wrote. Two things are worth raising, and both use
the same single question at the same moment:

**A local module that should be shared.** It is **generic** — nothing in it is
specific to this package — and **reusable**: you can name another package that
would want it.

**A shared module that should change.** You read one during the task, it nearly
fit, and composing around it did not cover the gap. Raise it now, but only as an
**additive, backward-compatible** change: a new function beside the existing
one, or a new keyword argument whose default preserves today's behaviour.
Packages you cannot see already import that module and you cannot test them, so
a changed signature or an altered return value is not yours to propose. If what
you need cannot be done additively, keep yours local and say so in your summary.

Before you ask about either, two checks.

**Is it vetoed?** Read the *Kept local* table below. It is the operator's list of
helpers that are deliberately not shared. A row there settles it: leave yours in
your package and say nothing more about it.

**Is sharing switched on?** Call `mcp__dex__utility_proposals_enabled`. It is a
global operator setting, read at the moment you call it. If it answers
`disabled`, **ask nothing**: leave the helper where it is, change nothing
outside your directory, and note in one line of your summary what you would have
shared. Do not work around the setting, and do not argue the case at length in
the summary instead — off means not asked.

If nothing vetoes it and sharing is on, call `mcp__dex__ask_user` **once**, with
`kind` set to `"utility"` and exactly these two options, **in this order**:

- **"Put it in the project `utils/`"**
- **"Keep it in the task directory"**

The `kind` and the order both matter: they are how dex recognises this question
and which option it takes as the affirmative one. Get either wrong and the
question goes to the operator as an interruption instead.

**The answer may come back instantly.** While sharing is on, dex answers this
one for the operator rather than interrupting them, and the answer is to
promote. That is not a reason to skip the question — it is how the decision gets
recorded where the operator can see it — and it is not a reason to assume the
answer either. Ask, wait, and do what comes back.

Make the question concrete — name the module, say what it does in a line, say
what you verified, and name the packages that would want it:

> `check_limb_symmetry(pose)` compares left and right limb lengths and reports
> the mismatches; run here against all 20 landmarks. Every pose package needs it
> before it is done. Put it in the project `utils/`, or keep it local to this
> package?

**On "Put it in the project `utils/`", in this order:**

1. Write the module, or the added function or argument, into the project's
   `utils/`.
2. Give whatever you add a **one-line docstring**, and the same for the module
   if it is new. That line is the interface the next task reads out of the
   generated index, so it is not a comment — write it for a stranger.
3. Change your package to import it from `utils/` and **delete the local copy**.
   Two versions is the thing this avoids.
4. **Re-run your checks.** You moved working code; you do not know it still
   works until you have seen it pass from its new home.
5. Regenerate the index:

   ```
   {{python}} -m dex.tools.utils_api <project directory>
   ```

You maintain no list by hand — `utils/API.md` and the roster in this file are
both generated from the code, and dex rewrites them before every task besides.

The writes land outside your task directory, so unless the operator has
auto-approve on, each one still stops for their approval. Wait for it rather
than abandoning the step. **If a write is declined, that is the answer** — put
the helper back in your package, leave `utils/` as you found it, re-run your
checks, and say so in your summary.

**On "Keep it in the task directory"**, it stays where it is and `utils/` is
untouched. Do not edit the *Kept local* table yourself; it is the operator's.

Ask **once**, at the end, and only about code that has actually run. If several
things belong together, name them all in that one question. Do not ask about a
three-line helper only your package will ever use — that is not a shared
utility. If no answer comes back at all, keep it local and say so in your
summary.

A project-scoped task already has the project directory as its own, so its
writes need no approval — it still asks, at the end, for the same reason.

### Shared utilities

Every module in the project's `utils/`, a line each. Both this roster and the
fuller `utils/API.md` beside the modules are generated from the source and
rewritten before your task starts, so neither can fall behind the code and
neither is yours to edit by hand. To change what a row says, change the
module's docstring.

<!-- utils:begin -->
<!-- Rows come from each module's first docstring line. Change the docstring, not the table. -->

| Module | What it does |
|---|---|
| _(none yet)_ | |

<!-- utils:end -->

A row is enough to tell whether a module is worth a closer look. When one is,
`utils/API.md` gives every public signature and constant at about a tenth of
the source; the module itself is the last resort.

### Kept local — the operator's veto

Helpers that are deliberately **not** shared, one row each. This table is
maintained by the operator, not by you: **never add, edit, or remove a row.**
Read it before you promote anything, and if what you have is listed, leave it in
your package.

It is empty unless someone has put something in it.

| Helper | Why it stays local |
|---|---|
| _(none yet)_ | |

## Conventions

Describe the naming, structure, and style a task in this project should follow.
"""


def slugify(name: str, taken: set[str] | None = None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    slug = (cleaned or "project")[:48].strip("-")
    if taken and slug in taken:
        suffix = 2
        while f"{slug}-{suffix}" in taken:
            suffix += 1
        slug = f"{slug}-{suffix}"
    return slug


@dataclass
class Project:
    slug: str
    name: str
    description: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    #: Counts filled in by the store for the picker.
    threads: int = 0
    tasks: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "threads": self.threads,
            "tasks": self.tasks,
        }


class ProjectStore:
    def __init__(self, db: Database, assets_root: Path) -> None:
        self.db = db
        self.assets_root = assets_root

    def directory(self, slug: str) -> Path:
        return self.assets_root / slug

    def guide_path(self, slug: str) -> Path:
        return self.directory(slug) / "AGENTS.md"

    async def list(self) -> list[Project]:
        rows = await self.db.pool.fetch(
            """SELECT p.slug, p.name, p.description,
                      extract(epoch from p.created_at)::float8 AS created_at,
                      extract(epoch from p.updated_at)::float8 AS updated_at,
                      (SELECT count(*) FROM threads t WHERE t.project = p.slug) AS threads,
                      (SELECT count(*) FROM tasks k WHERE k.project = p.slug) AS tasks
               FROM projects p ORDER BY p.name"""
        )
        return [Project(**dict(row)) for row in rows]

    async def get(self, slug: str) -> Project | None:
        rows = [p for p in await self.list() if p.slug == slug]
        return rows[0] if rows else None

    async def create(self, name: str, description: str = "", slug: str | None = None) -> Project:
        """Create a project, or return the existing one with that slug.

        An explicit `slug` is taken at its word — it names a project that
        already exists on disk or is being adopted. Only a slug derived from a
        name avoids collisions, since that is dex choosing for you.
        """
        if slug:
            chosen = slugify(slug)
        else:
            taken = {p.slug for p in await self.list()}
            if self.assets_root.exists():
                taken |= {d.name for d in self.assets_root.iterdir() if d.is_dir()}
            chosen = slugify(name, taken)
        project = Project(slug=chosen, name=name.strip()[:80], description=description.strip())
        await self.db.pool.execute(
            """INSERT INTO projects (slug, name, description) VALUES ($1, $2, $3)
               ON CONFLICT (slug) DO NOTHING""",
            project.slug, project.name, project.description,
        )
        # The directory and its guide are what tasks actually read.
        self.directory(project.slug).mkdir(parents=True, exist_ok=True)
        guide = self.guide_path(project.slug)
        if not guide.exists():
            guide.write_text(
                STARTER_GUIDE.format(name=project.name, description=project.description
                                     or "What this project is for."),
                encoding="utf-8",
            )
        return await self.get(project.slug) or project

    async def delete(self, slug: str) -> bool:
        """Removes the project and its threads. Files on disk are left alone."""
        result = await self.db.pool.execute("DELETE FROM projects WHERE slug = $1", slug)
        return result.endswith(" 1")

    def read_guide(self, slug: str) -> str:
        try:
            return self.guide_path(slug).read_text(encoding="utf-8")
        except OSError:
            return ""

    def write_guide(self, slug: str, text: str) -> None:
        path = self.guide_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename, so a crash cannot truncate the guide every task reads.
        scratch = path.with_suffix(".md.tmp")
        scratch.write_text(text, encoding="utf-8")
        scratch.replace(path)
