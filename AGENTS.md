# Working in this repository

## Do not touch the user's running servers

The user runs dex themselves, persistently:

| Port | Process |
|---|---|
| `4317` | the Python server (`.venv/bin/dex`) |
| `4318` | the Vite dev server (`npm run dev:ui`) |

**Never kill, restart, or bind to those ports.** No `pkill -f dex`, no
`lsof -t -i:4317 | xargs kill`, no `.venv/bin/dex` on the default port. Those
processes belong to the user; stopping one interrupts whatever they are doing
and, because the queue is shared through Postgres, can strand a task mid-run.

**dex-api's environment is clean by construction.** pm2 runs it through
`scripts/run_api.py`, which keeps only `DEX_*` and a few basics and takes
`CLAUDE_CODE_OAUTH_TOKEN` from `~/.zshrc`: nothing from the shell that runs
`pm2` (a Claude app session's `ANTHROPIC_BASE_URL` and `CLAUDE_CODE_*` once
failed every task with "Invalid API key") reaches dex or its tasks. Any
restart, and any edit under `src/dex` (the server reloads itself), stops what
is running; dex then marks it paused, or sometimes failed.

To exercise the server yourself, start your own on a different port and against
a different database:

```bash
DEX_PORT=4417 DEX_DATABASE_URL=postgresql://127.0.0.1/dex_test \
  DEX_FAKE_AGENT=1 .venv/bin/dex
```

and stop only that one, by the PID you started:

```bash
kill "$MY_PID"        # never a pattern that could match the user's process
```

For the web UI, start a second dev server on your port, pointed at your API:

```bash
cd web && DEX_WEB_PORT=4418 DEX_API_ORIGIN=http://localhost:4417 npx vite
```

(`DEX_API_ORIGIN` is the dev server's default API. The page itself can pick
another from Settings → dex server, kept in the `dex_api` cookie; see
`web/apiServer.ts` and the list in `web/api-servers.json`.
`/__dex/api-server/reset` forgets the choice.) Ports **4400-4499** are yours;
4317 and 4318 are the user's.

Before starting anything, check the port is actually free — a bind failure is
silent in the background and you will end up reading another process's
responses:

```bash
lsof -nP -iTCP:4417 -sTCP:LISTEN
```

## Never spend the user's tokens by accident

`DEX_FAKE_AGENT=1` replays a scripted run instead of calling the model. Set it
for anything that exercises the task pipeline. The real agent authenticates with
the user's Claude subscription, so a run you did not intend costs them usage.
If you change how the queue picks a runner, check `_runner_for` still honours
the flag — there is a test for it, keep it passing.

## The environment

- Postgres runs via Postgres.app; its binaries are **not on PATH**. Use
  `/Applications/Postgres.app/Contents/Versions/latest/bin/psql`.
- Databases: `dex` (the user's) and `dex_test` (tests, truncated freely). Do not
  truncate or delete from `dex`.
- `manim` on PATH is a pyenv shim for a *different* Python and a different
  version. Always run it as `.venv/bin/python -m manim`, or through
  `dex.tools.render_manim`.
- The venv is not activated by `.venv/bin/dex`, so anything relying on PATH to
  find a tool installed in the venv will not find it. Prefer
  `sys.executable -m <module>` and `importlib.util.find_spec` over
  `shutil.which`.

## Do not leave test output in the project

Running the server against `dex_test` still writes generated packages into the
real `assets/` tree — only the database is separate. Anything a test run
creates there is yours to delete afterwards; cross-check with

```bash
psql -h 127.0.0.1 -d dex -tAc "select distinct coalesce(output_slug, slug) from tasks;"
```

and remove any directory that has no task behind it.

## Checks

```bash
.venv/bin/python -m pytest tests -q          # needs dex_test
cd web && npx tsc --noEmit -p tsconfig.json
npx playwright test --config web/playwright.config.ts   # UI navigation, against the dev UI on :4318
```

The browser tests (`web/e2e`) answer the API from fixtures inside the
browser: no sign-in, nothing on the server touched. Navigation is one
history (`web/src/navigation.ts`): a handler the reader triggers calls `go()`
before it changes what is shown, so the place becomes a step that back (the
browser's, or a pane's ‹ on a phone) returns to; changes the app makes by
itself replace the current step instead.
