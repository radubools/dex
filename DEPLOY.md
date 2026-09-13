# Deploying dex with Docker

Two containers: Postgres, and one app container running the API and the built UI
under pm2.

```
┌─ postgres ─────────┐   ┌─ app ───────────────────────────────┐
│ postgres:17-alpine │◄──┤ pm2-runtime                         │
│ 5432 (not published)│   │  ├─ dex-api   :4317  API + SPA     │
│ volume: postgres-data   │  └─ dex-web   :4318  vite preview   │
└────────────────────┘   │        proxies /api → 127.0.0.1:4317│
                         │ volumes: /data/assets /data/state   │
                         └─────────────────────────────────────┘
```

## Run it

```bash
cp .env.docker.example .env
$EDITOR .env          # POSTGRES_PASSWORD and ANTHROPIC_API_KEY at minimum
docker compose up -d --build
```

Then open <http://localhost:4318>. The API is on 4317 if you want to curl it.

```bash
docker compose logs -f app       # both processes, interleaved
docker compose exec app pm2 list # what pm2 thinks is running
docker compose down              # stop; volumes survive
docker compose down -v           # stop and delete the data. Everything.
```

## What you must set before this faces a network

**`DEX_TOKEN`.** Unset means the API has no authentication at all. dex queues
tasks that run an agent which writes files and executes commands, on your Claude
account. On a public address that is a remote-code-execution endpoint with a
web UI. The entrypoint prints a warning at startup if it is empty.

**`DEX_BIND`.** Defaults to `127.0.0.1`, so the ports are reachable only from
the instance itself — the right default, and it means you reach the UI over an
SSH tunnel (`ssh -L 4318:127.0.0.1:4318 ec2-user@host`) until a proxy and a
certificate are in front of it. Set `0.0.0.0` only once `DEX_TOKEN` is set and a
security group limits the source.

**`DEX_ALLOWED_HOSTS`.** The UI server rejects requests whose `Host` header it
does not recognise, a DNS-rebinding guard. Unset allows any host, which is
correct behind a load balancer that has already matched the host and wrong when
4318 faces the internet directly. Set it to your hostname
(`dex.example.com`) in that case.

## Schema

There is nothing to run. `Database.connect()` executes `src/dex/schema.sql` on
every start, and that file is entirely `CREATE TABLE IF NOT EXISTS` and
`ALTER TABLE … ADD COLUMN IF NOT EXISTS` — so it creates the schema on a fresh
database and migrates an existing one, idempotently. No init container, no
migration job, no `/docker-entrypoint-initdb.d`.

The app waits for Postgres via a healthcheck rather than a sleep. The check is
`pg_isready -U dex -d dex`, not a bare `pg_isready`: the bare form answers true
while initdb is still creating the role, and the app then fails its first
connection and restart-loops for a minute.

## State that must survive a redeploy

| Volume | Holds | Losing it means |
|---|---|---|
| `postgres-data` | threads, tasks, events, settings, costs | the whole history |
| `dex-assets` | `/data/assets` — every generated package, and the project `AGENTS.md` guides | every package the agent ever produced |
| `dex-state` | `/data/state` — persisted threads, the animation cache | threads; the cache rebuilds |

On an EC2 instance these are Docker named volumes on the instance's EBS root
volume. That is fine for a single instance you do not replace, and **not** fine
if you plan to recreate instances: move `dex-assets` and `dex-state` to a
separate EBS volume mounted into the container, or to EFS, and Postgres to RDS.

`dex-assets` starts empty, so the entrypoint seeds it from the image's copy of
`assets/` on first boot only. Without the project guides a task has nothing
defining what it should produce and stops to ask — for every task. After that
first boot the volume is the truth and is never overwritten.

## Credentials

The agent needs one of `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or
`CLAUDE_CODE_OAUTH_TOKEN`. Without one the server starts and the UI loads, but
every task fails on its first turn; `/api/health` reports
`authenticated: false` and the entrypoint warns at startup.

A local subscription sign-in (`claude auth login`, stored under `~/.claude`)
does **not** come along into the container. Use an API key, or mount a
credentials directory and set `HOME` to it.

On AWS, put the key in SSM Parameter Store or Secrets Manager and inject it —
not in the `.env` on the instance, and not in the image.

## Memory

Each concurrent task spawns an agent subprocess of roughly **300 MB**. Measured
on this project with six tasks running:

| | |
|---|---|
| backend (`dex-api`) | ~110 MB, flat under load |
| 6 agent subprocesses | ~1.76 GB |
| `dex-web` (vite preview) | ~150–250 MB |

So the container ceiling has to cover `Tasks at once` × ~300 MB plus ~400 MB of
servers. `DEX_MEM_LIMIT` defaults to `6g`, which comfortably fits the default
concurrency. **Raise the limit and the concurrency together** — the *Tasks at
once* stepper in the UI goes to 16, which is ~4.7 GB of agents alone and will
be OOM-killed inside a 6 GB container.

A `t3.medium` (4 GB) is too small for more than two or three concurrent tasks.
`t3.large` (8 GB) is a sensible starting point; the work is subprocess-heavy
rather than CPU-bound.

## Image size

Two things dominate, both deliberate:

- **`node_modules` is copied into the runtime stage** (~400 MB) because
  `vite preview` needs it to serve and proxy the build. If you would rather not
  ship it, the backend already serves `web/dist` itself on 4317 — drop the
  `dex-web` app from `ecosystem.docker.config.cjs` and expose 4317 only.
- **manim and piper are not installed.** They pull in cairo, pango and a voice
  model and roughly quadruple the image. The server reports manim as
  unavailable and degrades rather than failing tasks. Build with
  `DEX_EXTRAS=manim,voice` in `.env` if a project here renders animations.

## Stopping cleanly

`docker stop` sends SIGTERM, `pm2-runtime` forwards it, and dex's own shutdown
marks running tasks preempted so they record a **pause** rather than a failure
and are picked up again on the next start. `stop_grace_period` is 30s to give
that time.

It is not perfectly reliable. When the agent's CLI subprocess dies before dex's
shutdown handler runs, the SDK reports an error and the task is recorded as
`failed` with no resume point. This is a known race in
`TaskManager.stop()` / `TaskRunner._fail()`, seen on local restarts too. Drain
first if it matters: pause work in the UI, wait for running tasks to finish,
then stop.

## AWS sketch

This part is yours, but the shape the compose file assumes:

1. One EC2 instance, Docker and the compose plugin installed.
2. Security group: no inbound except SSH (and 443 if you terminate TLS on the
   instance). Do not open 4317 or 4318 to the world.
3. `.env` on the instance with secrets injected from SSM, not committed.
4. `docker compose up -d --build`, then reach the UI through an SSH tunnel
   until a reverse proxy with a certificate is in front.
5. For anything longer-lived: Postgres → RDS (drop the `postgres` service and
   point `DEX_DATABASE_URL` at it), assets → EFS or a dedicated EBS volume.

The health endpoint for a target group or ALB check is `GET /api/health` on
4317. It answers without authentication.
