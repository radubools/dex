# 05 · Projects and the design chat

## Summary

A **project** is a named body of work: a directory under `assets/` holding a
standing brief, `AGENTS.md`, and one directory per package the project has
produced. The guide is what makes one dex serve unrelated kinds of work — algorithm
study packages, 3D yoga poses, music scores — without dex itself knowing about
any of them.

Each project has exactly one **design thread**: a conversation that shapes the
project rather than producing work in it. A turn in that thread runs as an
agentic task that can read the repository, edit the guide, author UI widgets,
build and test them, and report what it verified.

| Module | Responsibility |
|---|---|
| `src/dex/projects.py` | `ProjectStore`, seeding a guide from `AGENTS.template` |
| `src/dex/migrate.py` | `ensure_design_thread`, adopting a pre-project install |
| `src/dex/api.py` | Project routes, `_design_turn` |
| `src/dex/prompts.py` | `DESIGN_SYSTEM`, `design_prompt` |
| `src/dex/designer.py` | `history_text` for the design brief |
| `AGENTS.template` | The starter guide copied into every new project |

---

## What a project is

```mermaid
flowchart TD
    subgraph disk ["assets/yoga/"]
        G["AGENTS.md<br/>standing brief"]
        WJ["widgets.json<br/>viewer rules"]
        U["utils/<br/>shared tooling + API.md"]
        P1["warrior-ii/"]
        P2["crow-pose/"]
    end
    subgraph db [Postgres]
        PR[("projects")]
        TH[("threads")]
        TK[("tasks")]
        UP[("user_projects")]
    end
    PR --- TH
    PR --- TK
    PR --- UP
    DT["design thread"] -->|edits| G
    DT -->|edits| WJ
    CT["chat threads"] -->|plan and run| P1
    G -->|prepended to every brief| P1
    G -->|read by the planner| CT
```

Switching project switches everything the UI shows — threads, tasks, feed,
library, guide — and the project is also the unit of access control
([01](01_auth.md)). Project directories live under the gitignored `assets/`, so
a clone starts with none.

---

## Creating a project

```mermaid
sequenceDiagram
    autonumber
    actor A as Admin
    participant API as POST /projects
    participant PS as ProjectStore
    participant FS as assets/
    participant T as AGENTS.template
    participant DB as Postgres

    A->>API: {name: "Music", description}
    API->>API: require manage_projects
    API->>PS: create(name, description)
    PS->>DB: slugs taken
    PS->>FS: directory names taken
    PS->>PS: slugify(name, taken) → "music"
    PS->>DB: INSERT projects ON CONFLICT DO NOTHING
    PS->>FS: mkdir assets/music
    PS->>T: read
    alt template present
        T-->>PS: body
    else missing
        PS->>PS: FALLBACK_GUIDE (loud stub)
    end
    PS->>PS: replace {project_name} {project_description}
    PS->>FS: write AGENTS.md
    API-->>A: project
```

### Placeholders filled in two passes

```mermaid
flowchart LR
    TPL["AGENTS.template"] -->|"project creation<br/>str.replace"| AG["assets/p/AGENTS.md<br/>{project_name} filled<br/>{python} {task_dir} {workspace} kept"]
    AG -->|"every task start<br/>project_instructions()"| BR["the brief<br/>{python} → interpreter<br/>{task_dir} → package dir<br/>{workspace} → repo root"]
```

Both passes use `str.replace`, not `str.format`. The template was once a
Python string passed through `.format()`, where every per-task placeholder had
to be written `{{python}}` to survive — and an edit that forgot was a crash at
project creation time.

The fallback guide is **deliberately loud**. A project with no guide makes every
task stop and ask what to build, so an empty file would be worse than a stub
that says why it is empty.

### What the template gives a new project

| Section | Purpose |
|---|---|
| The environment is ready | Tools already installed, so tasks do not spend turns checking |
| What to produce | Left for the design chat to fill in |
| Viewers for this project's files | How widgets are authored, registered, built, tested |
| Shared utilities | The promote-at-the-end protocol ([08](08_shared_utilities.md)) |
| Conventions | Shell rules, paths, and asking over guessing |

---

## The design thread

`ensure_design_thread` runs whenever a project's threads are listed and creates
the thread the first time, with an opener message. It is idempotent: one
`project_design` thread per project, pinned above the chat threads.

### A design turn is a task

```mermaid
sequenceDiagram
    autonumber
    actor A as Author
    participant API as POST /threads/{id}/messages
    participant M as TaskManager
    participant R as TaskRunner
    participant FS as Filesystem
    participant B as EventBus

    A->>API: "add a 3D viewer for pose files"
    API->>API: require design
    API->>B: thread_message (user)
    API->>API: pack {message, guide, history} as JSON
    API->>M: submit(payload, scope="design", thread)
    M->>B: task_created
    API-->>A: {task}
    Note over R: claimed like any task
    R->>R: design_prompt + DESIGN_SYSTEM<br/>writable: project dir + widgets/
    R->>FS: ls, open real files, grep
    R->>FS: edit AGENTS.md · widgets.json · widgets/pose-3d/
    R->>FS: node widgets/build.mjs pose-3d
    R->>FS: playwright test pose-3d
    R->>FS: read edits back
    R->>B: text, thinking, tool, diff events
    R->>B: thread_message (dex) = result summary
```

It used to be **a single tool-less call** that returned the whole guide back.
That could not author anything, could not check the files it described, and had
nothing to show while it worked. Running it as a task gives it tools *and* the
entire activity surface — streamed text, collapsed thinking, tool calls, diffs,
questions — because that surface belongs to tasks. What makes a turn a design
turn is only its brief and a wider write mandate.

> `designer.py` still contains the old one-shot `draft()` and `parse()`. Only
> `history_text()` is used now.

### Packing the conversation into one field

A task has one `problem` string; a design turn needs three things. They travel as
JSON in `problem` and are unpacked by `Task.design_payload()`:

```mermaid
classDiagram
    class DesignPayload {
        message: what was just said
        guide: AGENTS.md as it stands
        history: last 12 messages
    }
    class Task {
        problem: str
        scope: "design"
        design_payload() dict
        is_design bool
        output_dir() project root
    }
    Task ..> DesignPayload : problem holds JSON
```

Nullable columns for fields only one scope uses would be three empty columns on
every other row. A `problem` that is not JSON — a design task created before this
format — is read as the message on its own, which is still a usable turn.

### What the designer may touch

```mermaid
flowchart TD
    W([Write or Edit]) --> A{"inside<br/>assets/project/?"}
    A -- yes --> OK([auto-allow])
    A -- no --> B{"inside widgets/?"}
    B -- yes --> OK
    B -- no --> ESC(["escalate to the operator"])
```

The brief names three things: `AGENTS.md`, `widgets.json`, and
`widgets/<name>/`. Writing anywhere else — another project, a package inside
this one, dex's own source — stops for approval, which the brief describes as a
sign the designer is somewhere it did not mean to be.

### The brief's working rules

```mermaid
flowchart LR
    L["Look before you write<br/>ls · open real files · grep"] --> C["Change what was asked<br/>leave the rest byte for byte"]
    C --> V["Check what you wrote<br/>read back · build · test"]
    V --> F["Finish<br/>report only what you saw work"]
```

"Look before you write" is the correction for the one-shot designer, which wrote
rules for a project it had never looked at. Reading is free and unrestricted, so
a couple of minutes of looking is the difference between a rule that fits and a
plausible one that does not.

### The reply lands in the thread

When the task's `ResultMessage` arrives, the runner appends its summary to the
design thread as a `dex` message. The thread reads as a conversation; the task
panel still holds the full run for anyone who wants the tool calls.

---

## Guides are gitignored

`assets/*` is gitignored, so **edits a design turn makes to a project's
`AGENTS.md` exist only on the machine that made them**. Only `AGENTS.template`
is tracked. An improvement worth giving every future project has to be made to
the template by hand.

---

## Adopting an older install

Before projects existed, packages sat directly under `assets/`.
`adopt_existing_project` runs at startup and registers the default project over
that layout, so an upgrade needs no manual step and no files move.
