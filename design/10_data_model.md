# 10 · Data model

## Summary

dex keeps its durable state in **Postgres** and its work product on **disk**.
The database holds everything about the work — projects, conversations, tasks
and their full event history, settings, users — while `assets/` holds the
packages the work produced.

The schema lives in one file, `src/dex/schema.sql`, applied on **every
startup**. There is no migration tool: every statement is `IF NOT EXISTS` or an
idempotent `UPDATE`, so the same file creates a fresh database and upgrades an
old one. The consequence is a hard rule — **changes must stay additive**.

| Area | Tables |
|---|---|
| Work | `projects`, `threads`, `messages`, `tasks`, `task_messages` |
| Activity | `events` |
| Configuration | `settings` |
| Library and feed | `package_tags`, `topic_reviews` |
| Identity | `users`, `user_projects`, `sessions`, `oauth_states` |

---

## Everything at once

```mermaid
erDiagram
    projects ||--o{ threads : scopes
    projects ||--o{ tasks : scopes
    projects ||--o{ user_projects : "granted in"
    threads ||--o{ messages : conversation
    threads ||--o{ tasks : started
    threads ||--o{ events : "thread events"
    tasks ||--o{ events : activity
    tasks ||--o{ task_messages : "follow-ups"
    tasks |o--o{ tasks : "parent attempt"
    users ||--o{ user_projects : granted
    users ||--o{ sessions : has
    users |o--o{ user_projects : "granted_by"

    projects { text slug PK }
    threads { text id PK }
    messages { text id PK }
    tasks { text id PK }
    task_messages { text id PK }
    events { bigserial seq PK }
    users { text id PK }
    user_projects { text user_id PK }
    sessions { text id PK }
```

Four tables relate to nothing by foreign key: `settings`, `oauth_states`,
`package_tags` and `topic_reviews`. The last two are keyed by
`(project, slug)` — the name of a package directory — because a package exists
on disk whether or not a task row still does.

---

## Work

```mermaid
erDiagram
    projects ||--o{ threads : scopes
    projects ||--o{ tasks : scopes
    threads ||--o{ messages : holds
    threads ||--o{ tasks : started
    tasks ||--o{ task_messages : "queued notes"
    tasks |o--o{ tasks : parent_id

    projects {
        text slug PK "directory under assets/"
        text name
        text description
    }
    threads {
        text id PK
        text title
        text project FK "CASCADE"
        text kind "chat or project_design"
        bool hidden "never deleted"
    }
    messages {
        text id PK
        text thread_id FK "CASCADE"
        text role "user or dex"
        text kind "text plan tasks error"
        text body
        jsonb data "plan rows, task payloads"
    }
    tasks {
        text id PK
        bigserial seq "claim order"
        text slug UK "identity"
        text output_slug "directory, when not slug"
        text project FK "SET NULL"
        text thread_id FK "SET NULL"
        text title "what a chip shows"
        text problem
        text scope "package project design survey"
        text state
        text activity
        text error "why it failed, in full"
        bool held "operator pause"
        jsonb anchor "where in the source material"
        text session_id "agent session"
        text resumed_from
        int attempt
        text parent_id FK
        text model "resolved at submit"
        text claimed_by "host:pid"
        timestamptz claimed_at "heartbeat"
        int turns "how many the agent took"
        float cost_usd
        bool cost_is_estimate
        bigint input_tokens
        bigint cache_read_tokens "billed at a tenth"
        bigint cache_write_tokens "billed at 1.25x"
        bigint output_tokens
        bigint thinking_tokens "subset of output"
    }
    task_messages {
        text id PK
        text task_id FK "CASCADE"
        text body
        timestamptz delivered_at "NULL while queued"
    }
```

### `slug` is identity; `output_slug` is a directory

This is the distinction most worth remembering.

```mermaid
flowchart LR
    subgraph rows [tasks]
        T1["attempt 1<br/>slug: two-sum<br/>output_slug: NULL"]
        T2["attempt 2 — resume<br/>slug: two-sum-2<br/>output_slug: two-sum"]
        T3["rewrite from a plan<br/>slug: two-sum-narrate<br/>output_slug: two-sum"]
    end
    subgraph disk ["assets/algorithms/"]
        D["two-sum/"]
    end
    T1 -->|writes into| D
    T2 -->|writes into| D
    T3 -->|writes into| D
    T2 -. parent_id .-> T1
```

- `slug` is **unique** and names the task.
- `output_slug` names the directory it writes into; `NULL` means "same as
  `slug`". A resumed attempt, or a plan row that `updates` an existing package,
  gets a slug of its own but continues the existing directory instead of opening
  an empty one beside it.
- **Anything keyed on the wrong one looks in the wrong place.** The watcher, for
  example, attributes a file to a task by `COALESCE(output_slug, slug)`, never by
  slug alone.

### Why the columns are shaped this way

| Column | Instead of | Because |
|---|---|---|
| `seq BIGSERIAL` for ordering | `created_at` | Tasks submitted together share the transaction clock and could be claimed out of order |
| `threads.hidden` | `DELETE` | Deleting cascaded messages away and orphaned tasks; a mistaken tap lost real work |
| `tasks.model` stored at submit | Reading the current default | Cost breakdowns stay correct after the default changes |
| `held` | A second paused state | Both are paused; only dex's own pauses resume themselves |
| `claimed_by`, `claimed_at` | In-memory ownership | A crashed worker's task is distinguishable from a live one, across processes |
| Design turn packed into `problem` as JSON | Three nullable columns | Only one scope has them |
| Survey brief packed into `problem` as JSON | Four nullable columns | Same reason; see [14](14_survey_and_anchors.md) |
| `tasks.anchor` as `JSONB` | Columns per position | The shape differs by source: a PDF has pages, a workbook a sheet, a link neither |
| `thinking_tokens` beside `output_tokens` | Adding them | Thinking is a subset of output; summing double-counts |

The `projects` foreign keys differ on purpose: deleting a project **cascades**
its threads but **nulls** its tasks, so spend history survives the project.

---

## Activity

```mermaid
erDiagram
    tasks ||--o{ events : "task_id"
    threads ||--o{ events : "thread_id"
    events {
        bigserial seq PK "the stream cursor"
        text task_id FK "CASCADE"
        text thread_id FK "CASCADE"
        text project "denormalised for filtering"
        text type
        jsonb data
        timestamptz ts
    }
```

Every event the UI renders is a row, written before it is delivered, so `seq`
is a stable cursor across reconnects and restarts. `project` is **denormalised**
onto the row because live fan-out happens in-process without a join; a one-time
backfill from `tasks` means historical rows filter the same way. This is by far
the largest table — streamed text arrives a token delta per row, so a busy day
has run to about 300,000 rows. See [07](07_event_stream.md).

Indexes serve the two ways it is read: `(task_id, seq)` for one task's history,
and `(project, seq)` / `(seq)` for the stream.

---

## Configuration

```mermaid
erDiagram
    settings {
        text key PK
        jsonb value
        timestamptz updated_at
    }
```

A single key-value table, because every entry is read and written independently
and is re-read continuously rather than at startup. That is what lets limits,
toggles and models change from the UI without a restart.

| Key | Holds |
|---|---|
| `task_concurrency`, `chat_concurrency` | Capacity limits |
| `paused` | The operator's global hold |
| `limit_paused`, `limit_snapshot`, `limit_override`, `limit_probe` | Usage-limit state ([09](09_costs_and_limits.md)) |
| `auto_approve` | Grant tool approvals without asking |
| `utility_proposals` | Shared-utility promotion ([08](08_shared_utilities.md)) |
| `model`, `model:<project>`, `effort` | What agents run on |
| `animation_speed` | Default playback speed ([11](11_animations.md)) |

---

## Library and feed

```mermaid
erDiagram
    package_tags {
        text project PK
        text slug PK
        jsonb tags "mirrored from manifest.json"
    }
    topic_reviews {
        text project PK
        text slug PK
        int seen_count
        timestamptz last_seen_at
        float interval_days
        float ease
        timestamptz due_at
    }
```

`package_tags` is a **derived index**: the truth is each package's
`manifest.json`, and the watcher mirrors it here so the Library can build its
filters from one query instead of opening every manifest. It is reconciled
against disk at startup and refreshed when a manifest changes.

`topic_reviews` is spaced-repetition state per package, per project. It has no
user column — **the schedule is shared by everyone who reviews that project**.
See [12](12_review_feed.md).

---

## Identity

Documented in full in [01](01_auth.md#sessions). In short: `users.role` is
nullable and `NULL` means no role; grants live in `user_projects`; sessions
store the SHA-256 of the cookie, never the cookie itself; `oauth_states` rows
are deleted as they are read, which makes an OAuth callback single-use.

---

## What lives on disk instead

```mermaid
flowchart LR
    subgraph pg [Postgres — about the work]
        A1["who asked, what for"]
        A2["what the agent did, event by event"]
        A3["what it cost"]
    end
    subgraph fs [assets/ — the work]
        B1["AGENTS.md · widgets.json · utils/"]
        B2["package files"]
        B3["manifest.json — tags live here"]
    end
    subgraph cache [".dex/ — rebuildable"]
        C1["animation speed and slice cache"]
    end
    pg -. "project slug · output_slug" .- fs
```

The link between the two is naming: a project's `slug` is its directory, and a
task's `COALESCE(output_slug, slug)` is its package directory. Guides and
manifests are deliberately files, not rows, because the agent reads and edits
them with ordinary file tools — and a person can too.

---

## Schema evolution

```mermaid
flowchart TD
    CH([A schema change]) --> K{"Kind of change"}
    K -- new table --> OK1["CREATE TABLE IF NOT EXISTS"]
    K -- new column --> OK2["ADD COLUMN IF NOT EXISTS<br/>with a default"]
    K -- renamed state --> OK3["idempotent UPDATE,<br/>before any constraint that needs it"]
    K -- changed constraint --> OK4["DROP CONSTRAINT IF EXISTS,<br/>then ADD"]
    K -- dropped or renamed column --> NO["Not supported —<br/>add the new one, stop using the old"]
```

Two examples already in the file. `interrupted` was folded into `paused` with an
`UPDATE` that moves rows that had run and closes rows that had not. The role
constraint was widened for the four-role model by first migrating `user` rows to
`operator` and only then recreating the `CHECK`, which would otherwise have
rejected the rows being fixed.

---

## Improvement opportunities

- **`assets/` is gitignored, so `skills.json` does not travel.** Which version
  of which skill each project is on is per-install state — a clone gets the
  skills but not the answer to which ones are enabled. That is the one hole in
  "a project is reproducible on another install", and the admin table is
  currently the only thing that closes it.
- **The ER block is hand-maintained**, and it had drifted: three token columns
  the database has and `pricing` bills were missing from it until this pass. A
  test comparing the diagram against `information_schema` would be a dozen
  lines.
- **Deleting a project nulls its tasks so spend survives** — which means a task
  row can outlive every path it names, and `output_dir` then resolves against
  the default project.

