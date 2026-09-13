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
