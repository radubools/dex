# dex

Generates study material — algorithm packages and yoga poses — by driving the
Claude Agent SDK, and serves it to a browser or a phone.

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
- **`dex-api` runs with `--reload` off.** Editing anything under `src/dex/`
  while it runs is safe; the restart is yours to make when you want it.
- **Rebuild the UI** after changing `web/` if you are viewing through
  `dex-api` rather than the Vite dev server:

```bash
npm run build
```

## Where things live

| Path | |
| --- | --- |
| `src/dex/` | the server: queue, runner, API, usage watcher |
| `web/` | the React UI |
| `assets/<project>/` | generated packages, one directory each, and the project's `AGENTS.md` |
| `tests/` | pytest, against a real Postgres |
| `datasets/` | unrelated to dex — a construction-costs dataset |
