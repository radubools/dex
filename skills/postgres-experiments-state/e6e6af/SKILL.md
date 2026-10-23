---
name: postgres-experiments-state
description: Read and write shared project state in the dex Postgres — a named row with a free-form JSON body and an append-only history beside it, so a criterion that gets run repeatedly accumulates a track record instead of a single latest number.
---

# Postgres experiments state

One module, `utils/experiments.py`. It talks to the dex database and gives
every task the same two tables in an `experiments` schema: `state`, one row per
named thing, and `history`, one append-only entry per execution of it.

A task's findings die with its package. This is where they go instead — not the
deliverable a person reads, but the numbers a later task needs in order to know
what has already been tried and whether it worked.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import experiments

with experiments.connect() as conn:
    experiments.ensure_schema(conn)
    experiments.upsert_criterion(conn, "is_draft", question, owner="pdf-qa")
    experiments.record_run(conn, "is_draft", task="pdf-qa",
                           metrics={"youden_j": 0.62}, detail=laya.detail(result)["is_draft"])
    past = experiments.track_record(conn, "is_draft")
```

`example.py` is that walkthrough end to end, runnable, against the real schema:

```
/Users/raduparadovschi/dex/.venv/bin/python3 skills/postgres-experiments-state/<version>/example.py
```

It creates the schema if it is missing, registers a criterion, logs a run,
reads the track record back, does the same query in raw SQL, and deletes its
demo row on the way out. Pass `--keep` to leave the row in place.

## The two tables

```
experiments.state                        experiments.history
  id          bigserial                    id           bigserial
  kind        text     ─┐ unique            state_id    bigint → state(id) ON DELETE CASCADE
  name        text     ─┘ together          recorded_at timestamptz  default now()
  body        jsonb    free-form            task        text         who ran it
  created_at  timestamptz                   metrics     jsonb        what it scored
  updated_at  timestamptz (trigger)         detail      jsonb        the full answer
                                            note        text
```

- **`kind` is the namespace.** `laya_criterion` for a criterion; any other
  string for any other shared state. Two rows may share a `name` if their
  `kind` differs, and `list_rows(conn, kind)` never sees across the boundary.
- **`body` is free-form JSON.** For a criterion it is `{"question": <the laya
  question>, ...whatever else you passed}`. Nothing else is imposed.
- **`history` is append-only, enforced by the database.** A `BEFORE UPDATE`
  trigger raises on any attempt to edit an entry, including from psql. Deletes
  are left alone so a row can still be dropped with its parent — `delete_row`
  cascades.
- **The history reads as an array too.** The brief for this schema asked for the
  history as an array on the row; it is stored as child rows because that is
  what makes "every run of every criterion since June" a plain query, and the
  view `experiments.state_with_history` hands it back as a `jsonb` array with a
  `runs` count for anyone who wants the array shape:

  ```sql
  SELECT name, runs, history FROM experiments.state_with_history WHERE kind = 'laya_criterion';
  ```

`ensure_schema(conn)` creates all of it and is idempotent — run it at the top
of any task that writes, not once by hand. `schema_sql()` returns the same
statements without running them, if you would rather read the DDL first.

**The schema is shared, and it is not scoped per project.** Every project with
this skill enabled writes into the same `experiments.state`. `kind` is what
keeps them apart, so name it for the thing, not for the task — and remember
that a row you delete is a row another project may have been reading.

## Where the Laya integration goes

**This skill is the integration point for `laya-decisions`.** That skill answers
questions; it does not remember. Nothing here imports it — the dependency runs
the other way, and it is lazy.

**The wiring exists: `utils/ledger.py`, in `laya-decisions`.** If you are
iterating a question set with `sweep`, use that rather than the calls below —
`ledger.publish(conn, run, task=...)` writes one entry per criterion the run
actually asked, and `ledger.worth_improving(ledger.standing(conn))` reads the
pooled record back. Its SKILL.md has the detail. What follows is the same thing
by hand, for a task that is not using `sweep`:

**After running a criterion, log it.** One history entry per execution, carrying
both the metric you scored and the answer itself:

```python
for chunk, result in laya.ask_chunks(agent, text, questions):
    detail = laya.detail(result)                       # verdict + confidence + probabilities
    experiments.record_run(conn, "is_draft", task="pdf-qa/summarise.py",
                           metrics={"youden_j": scores["j"], "chunks": len(chunks)},
                           detail=detail["is_draft"])
```

Pass `laya.detail(result)[qid]`, not `laya.answers(result)[qid]`. A verdict of
`0.88` with no distribution behind it cannot be re-judged later, and the whole
reason to use a decision model is that you can tell a 0.95 from a 0.4.

**Before improving a criterion, read its record.** `track_record` returns each
metric as a time-ordered series with the task that produced each point:

```python
record = experiments.track_record(conn, "is_draft")
#  {'runs': 4, 'tasks': ['pdf-qa', 'pdf-qa-v4'],
#   'metrics': {'youden_j': [{'at': ..., 'task': 'pdf-qa', 'value': 0.51}, ...]}}
```

It returns **data, not a verdict**. Whether four rounds that moved J from 0.51
to 0.62 justify a fifth is a judgement, and it belongs in the task making it —
`laya-decisions` ships `sweep.convergence(history, min_gain=..., patience=...)`
for exactly that call, and `ledger.standing()` is that pairing already done,
pooled across every task rather than one criterion at a time.

**Register the question, not just its name.** `upsert_criterion` stores the
question schema itself, so a later task reads back what was actually asked
rather than guessing from an id:

```python
question = experiments.criterion(conn, "is_draft")["body"]["question"]
results = laya.ask_chunks(agent, text, {"is_draft": question})
```

The question is validated by shape — `type` and `instructions`, plus `criteria`
for a choice or a score — which catches an answer passed where a question
belongs without making this module depend on Laya being installed. The two
skills can be enabled independently, and this one needs no model.

## Free-form SQL

The helpers cover the common path; `query` and `execute` cover everything else.

```python
experiments.query(conn, """
    SELECT s.name, count(h.id) AS runs, max((h.metrics->>'youden_j')::float) AS best
      FROM experiments.state s
      LEFT JOIN experiments.history h ON h.state_id = s.id
     WHERE s.kind = $1
  GROUP BY s.name ORDER BY best DESC NULLS LAST""", ["laya_criterion"])
```

Placeholders are `$1, $2, ...` — asyncpg's, not `%s` — and values go in the
list. **Never build SQL with an f-string.** It is not only the injection: a
criterion named `O'Brien` is a syntax error the first time someone tries it, and
a parameter makes that a non-event. The one identifier that cannot be a
parameter is the schema name, and `_ident` checks it against
`[a-z_][a-z0-9_]*` and quotes it before it goes anywhere near a statement.

## Connecting

`connect()` takes the first of: its argument, `EXPERIMENTS_DATABASE_URL`,
`DEX_DATABASE_URL`, then `postgresql://127.0.0.1/dex`. On this machine
`DEX_DATABASE_URL` is already set, so `connect()` with no argument reaches the
dex database — the self-hosted pm2 + Postgres.app guest, not the EC2 stack.

- **It is synchronous.** `asyncpg` is the only driver installed, so each
  `Connection` owns a private event loop and blocks on it. Called from inside a
  running event loop it raises and says so rather than deadlocking.
- **`connect` retries, statements do not.** Three attempts by default, backing
  off 0.5s and doubling, and only for transient failures — a server still
  starting, a socket not listening, a connection limit. A bad password or a
  missing database raises at once instead of sleeping through three rounds. A
  statement is never retried: a connection that drops mid-write leaves no way
  to tell a write that never arrived from one whose acknowledgement was lost,
  and that choice belongs to the caller.
- **Close it.** Use it as a context manager, or call `close()` — it closes the
  loop too.

To look at the data by hand, use psql from Postgres.app **through Terminal.app**
— Postgres.app's approval dialog blocks the editor's own shell, and Terminal is
already allow-listed. A Python client is not gated the same way, so `asyncpg`
from the session's own shell connects without a prompt:

```
/Applications/Postgres.app/Contents/Versions/latest/bin/psql postgresql://127.0.0.1/dex -c '\d experiments.history'
```

## Looking at a task's criteria

`widgets/criteria-dashboard` opens one task's scored criteria. Everything is in
one column, top to bottom: three dropdowns — **model**, **round**, **criterion**
— then the question, the run's metrics as cards, a threshold slider, and a
scrollable listing of the chunks whose verdict clears it (30% unless the file
says otherwise). The criterion dropdown is ordered by the `hits` each run
recorded and carries the count in the option label.

The single column is deliberate: the criterion list used to be a 260px sidebar,
which on a phone in portrait took 40% of the screen and left the chunk text
unreadable beside it. A native `<select>` also hands a phone the platform picker,
which scrolls 243 options better than any list in a div — and desktop loses
nothing, since the cards and chunk rows are what wanted the width.

It reads the tables **live**, through `/api/sql`.

**Open your package's `chunks.json`.** That is the whole gesture, and there is no
extra file to write. The widget reads the chunk text out of the file it was
handed and works out the task from the directory that file sits in, by matching
`history.task` against the package name. `chunks.json` is the trigger because it
is the one file always present: 87 of this project's 104 packages have one,
including every single one of the 76 that have rows in `experiments.history`.

Two other shapes open in the same widget, for when the directory is not enough:
a **pointer** naming one task, saved as `<anything>.criteria.json` —

```json
{"format": "experiments.criteria",
 "task": "change-order-risk-criteria-doc-5/r4",
 "chunks": "chunks.json"}
```

— and the self-contained file `dashboard.write` produces, which carries its own
criteria and chunk text and queries nothing at all. Use that for a snapshot to
keep, or when `/api/sql` is not installed.

Five things to know:

- **`/api/sql` has to be installed**, and is. A widget's frame has an opaque
  origin and no session, so this is the only route it can call.
  `assets/construction/dex-sql-api/` holds the source and the re-install.
- **The API host is derived, never hard-coded.** `apiBases()` tries the origin
  that served the dex page — read from `location.ancestorOrigins` or
  `document.referrer`, both of which are legible inside the sandbox — and then
  `http://127.0.0.1:4317`. That matters because dex is routinely opened under
  another name (`web/vite.config.ts` allows `.ts.net`, `.local`,
  `macbook-pro-4`, `mac`), and from any of those a fixed `127.0.0.1:4317` is a
  different host *and* port. A browser that declines it says only
  "Load failed" — no status, indistinguishable from the server being down, which
  is what a hard-coded base cost once already. Set `"api"` in the file to force
  one; it then suppresses both.
- **A package can hold several tasks** — a Laya round and a Jev replay of it, say
  — and 30 of the 81 with rows do. The most-scored one is shown, and a dropdown
  in the header switches between them. A pointer pins one instead.
- **`history.task` names its package four different ways:** bare (`doc-5`), with
  a step after a slash (`doc-5/r4`), with the project in front
  (`construction/doc-61/rescore.py`), and with `#`
  (`construction/doc-31#v5`). `taskPattern()` builds the bounded regex that
  matches all four; a `LIKE 'pkg/%'` prefix silently misses 8 of the 156 tasks,
  and an unbounded match makes `doc-1` swallow `doc-13`.
- **`detail["answers"]` has three stored shapes and only one is an object.** A
  bare number (`0.99`) and a `[verdict, confidence]` pair are both in the live
  schema. Reading `->>'verdict'` alone returns NULL for every chunk of the other
  two — 431 criteria across 3 tasks, including the project's largest package —
  and nothing raises: the criterion lists, the pane is just empty. `_normalise`
  in `utils/dashboard.py` and `ANSWERS_SQL` in the widget both fold all three.
- **The chunk text never comes from the database.** `history.detail` keys every
  answer by a 12-hex chunk id and stores no text; it comes from the opened
  `chunks.json`, or from the sibling a pointer names — which the host resolves
  against that file's own directory and refuses to leave.

**The cards show what the run recorded, and nothing derived.** There is no ground
truth in these tables — no gold answer per chunk anywhere in the schema — so
`hits`, `lift`, `support`, `mean_confidence` and `J` are copied out of
`history.metrics` verbatim, a metric the run never recorded reads as `—`, and
dragging the threshold changes the listing only. A criterion whose task never
recorded `hits` sorts after every one that did, which for most tasks is most of
them.

One trap in the SQL, if you write your own: the per-chunk answers live in a
`jsonb` object, so expanding them needs `jsonb_each` **after** the latest history
entry has been picked. A `DISTINCT ON (s.id)` over the lateral join collapses a
whole document to one chunk and returns a plausible single row.

## Testing it

```
/Users/raduparadovschi/dex/.venv/bin/python3 skills/postgres-experiments-state/<version>/test/test_experiments.py
```

Run it by path, not `-m test.test_experiments`: `test` is a standard library
package and shadows this directory.

The widget has its own suite, and its own Playwright config — the shared
`widgets/playwright.config.ts` sets `testDir: '.'`, which resolves to the
top-level `widgets/` directory, and since widgets moved inside skills that
directory holds no specs at all:

```
node_modules/.bin/playwright test --config \
  skills/postgres-experiments-state/<version>/widgets/criteria-dashboard/playwright.config.ts
```

Fifteen checks against real rows from the live schema: some on a self-contained
export with no network, the rest driving the live `/api/sql` path, which the
config starts on port 4399 for the duration.

**The specs pass the bundle as `?src=`, never `?widget=<name>`.** Resolving by
name goes through `widgets/index.json`, which holds one path per widget name and
is rewritten by `build.mjs` from whatever it built last — with several published
versions of this skill on disk, which one wins is alphabetical accident. It
quietly pointed the suite at a *published* bundle while the source under test had
been rewritten, and fifteen checks passed against code that no longer existed.
Resolve the path from `import.meta.url` and pass it explicitly.

85 checks. Everything that touches the database happens in a schema called
`experiments_selftest`, created at the start and dropped at the end, so a green
run proves the DDL and every query without putting a row in the shared schema.
It asserts the things that would otherwise be claims: that `ensure_schema` run
twice changes nothing, that the append-only trigger really refuses an `UPDATE`,
that a name containing an apostrophe is just a value, that jsonb survives the
round trip as a dict, and that a dead server is retried while a missing database
is not. The checks that need no database — connection-string resolution, the
identifier guard, question validation — run first, so most mistakes surface even
with the server down.

## Requirements

`asyncpg` and a reachable Postgres. No model, no network beyond the database,
nothing platform-specific — unlike `laya-decisions`, this runs anywhere.

## What it will not do

It does not judge a track record, run Laya, or decide what a good criterion is —
it stores, indexes and hands back. It is not a migration tool either: the schema
is created if absent and otherwise left alone, so a column that needs to change
is a deliberate piece of SQL, not a silent upgrade on the next task's first
call.
