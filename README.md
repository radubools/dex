# dex

Generates study material — algorithm packages and yoga poses — by driving the
Claude Agent SDK, and serves it to a browser or a phone.

## Setting it up from a clone

Needs Python 3.11+, Node 20+, Postgres, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

That creates `.venv/` with the server and its `dex` entry point. Then the
JavaScript side — one install at the repo root covers both the UI and the
widgets, which share a workspace:

```bash
npm install
```

A database, which the server creates the schema in on every start, so there is
no migration step:

```bash
createdb dex
```

Credentials for the agent, in your environment or in `.env`: one of
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or `CLAUDE_CODE_OAUTH_TOKEN`
(`claude setup-token` prints the last of these, and uses a subscription rather
than API billing). Without one the UI loads and every task fails on its first
turn; `/api/health` reports `authenticated: false`.

### Build the widgets before you open anything

Widgets are committed as source only — `dist/` is gitignored — and the server
skips one whose bundle is missing, so on a fresh clone every file falls back to
the markdown or code viewer with no error to explain why. Build the three that
ship with the repo:

```bash
node widgets/build.mjs pose-3d && node widgets/build.mjs narrated-video && node widgets/build.mjs music-score
```

Nothing is loaded into the server process: a bundle is a file the browser
fetches, so a build takes effect on the next page that asks for it, with no
restart. The same command is what a project design chat runs when it writes a
new one.

```bash
npx playwright install chromium
```

installs the browser the widget tests drive, if you intend to run them.

### The empty directories

`assets/`, `datasets/` and `widgets/<name>/dist/` hold generated material and
are not in git; each keeps a `README.md` so the directory survives a clone.
A clone therefore starts with no projects. Create the first one in the UI — it
is seeded with a copy of [`AGENTS.template`](AGENTS.template) as its
`AGENTS.md`, which is the brief every task in that project reads, and which the
project's design chat then edits with you.

## Sign-in and roles

Off by default. With neither mechanism configured dex is open to anyone who can
reach the port, which is the right setting on a private machine and the wrong
one on anything public.

Two ways in, and they can both be on at once:

```bash
DEX_PASSWORD_AUTH=1                      # username and password
DEX_GOOGLE_CLIENT_ID=… DEX_GOOGLE_CLIENT_SECRET=…   # Google
```

With password sign-in on and no admin in the database, dex creates one at
startup — `admin` / `admin` by default, overridable with
`DEX_SEED_ADMIN_USERNAME` and `DEX_SEED_ADMIN_PASSWORD`. It is logged as a
warning, and it is a **door, not a credential**: the account holds a role it
cannot use until the password is replaced, so the first sign-in goes straight to
a password form and every other request answers `428` until it is done. Seeding
happens only when there are no admins at all, and it refuses to promote an
existing account that happens to be called `admin`.

### The four roles

| Role | May |
| --- | --- |
| `admin` | Everything: create projects and users, assign roles and project access, reset passwords, change settings. Sees every project, including ones created later. |
| `author` | Design a project — its `AGENTS.md` guide and its UI widgets — **and** run tasks. Designing a project and being unable to try what you designed is not a job anybody does. |
| `operator` | Run tasks and write project utilities. |
| `viewer` | Read the library of generated material. |

A user with **no role** is signed in and not authorised: they see a holding page
and nothing else. That is the absence of a role rather than a role of its own,
so it is stored as `NULL` and is never a value you can assign — "No access" in
the admin UI clears the column.

They form a **ladder** — read, then run, then design, then administer — each
adding to the one below. Designing is the only thing that separates an author
from an operator.

Nothing in the code relies on that shape, though: a route asks for the
capability it needs (`view`, `run_tasks`, `design`, `manage_projects`,
`manage_users`) and one table says which roles have it. That is what let
`author` gain task-running by editing a single line, rather than hunting down
the checks that assumed it could not.

**A role says what; a project grant says where.** Both have to allow an action.
An author granted `music` may rewrite that project's guide and run tasks in it,
and is told a project they were not granted does not exist — 404 rather than
403, so the admin UI does not leak a list of project names. An admin needs no grants at all;
their access is the role, which is why demoting one does not leave stale rows
behind.

Roles and grants live in the database and are read on **every request**, so
removing someone's role takes effect on their next click rather than whenever a
token would have expired. Removing a role, or resetting a password, also ends
every session that person had open.

Manage all of it from **Settings → Users and access**: the role dropdown, the
per-project checkboxes, account creation, and password resets. A password an
admin types is always temporary, because two people then know it.

An install upgrading from the earlier two-role model has its `user` accounts
migrated to `operator`, which is what that role could already do.

`DEX_TOKEN` is unchanged and independent: it is the service credential for
health checks and the CLI, which have no browser, and it bypasses roles
entirely.

## Running the servers under pm2

Both processes are defined in [`ecosystem.config.cjs`](ecosystem.config.cjs):

| Process | What it is | Port |
| --- | --- | --- |
| `dex-api` | the Python server (`.venv/bin/dex`), which also serves the built UI | 4317 |
| `dex-web` | the Vite dev server (`npm run dev:ui`), for working on the UI | 4318 |

Start both, and have pm2 bring them back after a reboot:

```bash
pm2 start ecosystem.config.cjs && pm2 save
```

Everything below acts on one or both by name.

```bash
pm2 restart dex-api
```

```bash
pm2 restart dex-web
```

```bash
pm2 restart all
```

```bash
pm2 stop dex-api dex-web
```

```bash
pm2 status
```

Follow the logs — `dex-api` is where agent runs, the queue and usage limits
report themselves:

```bash
pm2 logs dex-api
```

Recent output without following:

```bash
pm2 logs dex-api --lines 100 --nostream
```

Logs are also written to `.pm2-logs/` in the repo.

To take a process out of pm2 entirely:

```bash
pm2 delete dex-api
```

### Adding them by hand

The config file is the supported route, but if you ever need to register them
individually — the arguments match what `ecosystem.config.cjs` sets:

```bash
pm2 start .venv/bin/dex --name dex-api --interpreter none --cwd "$PWD"
```

```bash
pm2 start npm --name dex-web --interpreter none --cwd "$PWD" -- run dev:ui
```

Started this way they inherit your shell's environment. The config file sets
`DEX_DATABASE_URL`, `DEX_PORT`, and a `PATH` that includes Homebrew — a pm2
daemon does not otherwise see Postgres.app or `/opt/homebrew/bin`.

### Notes

- **Restarting kills work in flight.** The queue marks those tasks paused
  rather than failed and picks them up again, but it waits 30 seconds after a
  restart before starting anything, which is room to intervene.
- **`dex-api` runs with `--reload`.** Saving anything under `src/dex/` restarts
  the server, which is the same interruption as a manual restart — so a task in
  flight is paused and requeued. Drop the flag from `ecosystem.config.cjs` if
  you would rather edit while tasks run. pm2 does not re-read `args` on
  `pm2 restart`; changing them needs `pm2 delete dex-api` then
  `pm2 start ecosystem.config.cjs`.
- **Rebuild the UI** after changing `web/` if you are viewing through
  `dex-api` rather than the Vite dev server:

```bash
npm run build
```

## Backing up the generated material

`assets/` and `datasets/` are **not in git** — a gigabyte of video and audio
that changes with every run. They are copied to the external drive instead:

```bash
./scripts/backup-assets.sh
```

The script refuses to run when the drive is not mounted. That guard matters:
`/Volumes/external` is an ordinary empty directory when nothing is plugged in,
and a plain `rsync` would fill the boot disk while appearing to succeed. It is
additive — no `--delete` — so a local mistake cannot erase the backup, at the
cost of renamed packages leaving their old names behind. It logs to
`.pm2-logs/backup-assets.log`.

### The daily schedule

It runs **daily at 12:30**, as a LaunchAgent at
`~/Library/LaunchAgents/com.dex.backup-assets.plist`. The equivalent cron line,
if you would rather use `crontab -e`:

```
30 12 * * * /Users/raduparadovschi/dex/scripts/backup-assets.sh
```

launchd rather than cron because this is a laptop: launchd runs a job that was
missed while the Mac slept, and cron simply skips it. Midday rather than the
small hours for the same reason — a 03:00 backup on a sleeping machine never
happens.

Managing it:

```bash
launchctl list | grep dex.backup
```

```bash
launchctl kickstart gui/$(id -u)/com.dex.backup-assets
```

```bash
launchctl bootout gui/$(id -u)/com.dex.backup-assets
```

### It needs Full Disk Access first

macOS blocks background jobs from writing to external drives, so the scheduled
run fails with `Operation not permitted` until the permission is granted — even
though running the script by hand works, because a terminal already has it.

In **System Settings → Privacy & Security → Full Disk Access**, add `/bin/sh`
(press ⌘⇧G in the file picker to type the path), then:

```bash
launchctl kickstart gui/$(id -u)/com.dex.backup-assets
```

Check `.pm2-logs/backup-assets.log` for a `copied:` line rather than `FAILED:`.

## Where things live

| Path | |
| --- | --- |
| `src/dex/` | the server: queue, runner, API, usage watcher |
| `web/` | the React UI |
| `assets/<project>/` | generated packages, one directory each, and the project's `AGENTS.md` |
| `tests/` | pytest, against a real Postgres |
| `datasets/` | unrelated to dex — a construction-costs dataset |
