-- dex persistence. Applied idempotently at every startup; there is no
-- migration tool, so changes here must stay additive.

CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,
    title       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    thread_id  TEXT        NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role       TEXT        NOT NULL,
    kind       TEXT        NOT NULL,
    body       TEXT        NOT NULL DEFAULT '',
    data       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_thread_ts ON messages (thread_id, ts);

-- Threads are hidden, never deleted: deleting one cascaded its messages away
-- and orphaned its tasks, which lost real work when a tap landed wrong.
ALTER TABLE threads ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    thread_id    TEXT REFERENCES threads(id) ON DELETE SET NULL,
    title        TEXT        NOT NULL,
    slug         TEXT        NOT NULL UNIQUE,
    problem      TEXT        NOT NULL,
    state        TEXT        NOT NULL,
    activity     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    error        TEXT,
    cost_usd     DOUBLE PRECISION,
    turns        INTEGER,
    -- The Agent SDK session, captured so a stopped run can be resumed
    -- rather than restarted from nothing.
    session_id   TEXT,
    -- Which attempt this is, and what it descends from.
    attempt      INTEGER     NOT NULL DEFAULT 1,
    parent_id    TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    resumed_from TEXT,
    -- Directory this attempt writes into. NULL means "same as slug"; a resumed
    -- attempt points at its parent's directory so it finishes that package
    -- instead of starting an empty one beside it.
    output_slug  TEXT,
    -- 'package' for the usual one-package task, 'project' for a small uniform
    -- edit across the packages a project already has, 'design' for a turn of
    -- the project design chat -- which is a task so that it inherits the whole
    -- activity view rather than needing one of its own.
    scope        TEXT        NOT NULL DEFAULT 'package',
    -- Paused because the operator said so, rather than because dex made room.
    -- Both look like 'paused'; only dex's own are picked up again on their own.
    held         BOOLEAN     NOT NULL DEFAULT false,
    -- Set while a worker holds the task, so a crashed worker's task can be
    -- told apart from one that is genuinely running.
    claimed_by   TEXT,
    claimed_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks (state, created_at);
CREATE INDEX IF NOT EXISTS tasks_thread ON tasks (thread_id, created_at);

-- Every event the UI renders, so a reload replays the full history of a run
-- instead of starting blank.
CREATE TABLE IF NOT EXISTS events (
    seq       BIGSERIAL PRIMARY KEY,
    task_id   TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE,
    type      TEXT        NOT NULL,
    data      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_task_seq ON events (task_id, seq);
CREATE INDEX IF NOT EXISTS events_seq ON events (seq);

-- Global operator settings, e.g. whether approvals are granted automatically.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      JSONB       NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Follow-up messages typed into a task's activity tab. They are held until the
-- task stops, then delivered as the instruction for its next attempt, so a
-- note written mid-run does not interrupt what is already happening.
CREATE TABLE IF NOT EXISTS task_messages (
    id           TEXT PRIMARY KEY,
    task_id      TEXT        NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    body         TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS task_messages_pending
    ON task_messages (task_id) WHERE delivered_at IS NULL;

-- A project is a subdirectory of the assets root holding its own AGENTS.md and
-- one directory per task.
CREATE TABLE IF NOT EXISTS projects (
    slug        TEXT PRIMARY KEY,
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Spaced-repetition state for the review feed, one row per topic (a generated
-- package) per project.
-- The tags each package carries, mirrored out of its manifest.json by the
-- watcher. Kept here so the Library can draw its filters from one query
-- instead of opening every manifest in the project on each load, and so the
-- tags a project has are known even before anyone opens it.
CREATE TABLE IF NOT EXISTS package_tags (
    project    TEXT        NOT NULL,
    slug       TEXT        NOT NULL,
    tags       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project, slug)
);

CREATE TABLE IF NOT EXISTS topic_reviews (
    project       TEXT        NOT NULL,
    slug          TEXT        NOT NULL,
    seen_count    INTEGER     NOT NULL DEFAULT 0,
    last_seen_at  TIMESTAMPTZ,
    -- Days until this should come round again, and how sharply that grows.
    interval_days DOUBLE PRECISION NOT NULL DEFAULT 0,
    ease          DOUBLE PRECISION NOT NULL DEFAULT 2.5,
    due_at        TIMESTAMPTZ,
    PRIMARY KEY (project, slug)
);
CREATE INDEX IF NOT EXISTS topic_reviews_due ON topic_reviews (project, due_at);

-- Added after the first release; harmless when the columns already exist.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS output_slug TEXT;
-- Submission order. `created_at` is the transaction clock, so tasks submitted
-- together can share a timestamp and be claimed out of order; this makes the
-- queue properly FIFO.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS seq BIGSERIAL;
CREATE INDEX IF NOT EXISTS tasks_queue_order ON tasks (state, seq);
-- Which model ran this attempt, resolved at submit time so the cost breakdown
-- stays accurate after the global or per-project default changes.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS model TEXT;
-- Running cost estimate while a task is in flight; replaced by the
-- authoritative figure when the agent reports its result.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cost_is_estimate BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS tasks_cost ON tasks (finished_at) WHERE cost_usd IS NOT NULL;

-- Projects, added after the first release.
ALTER TABLE threads ADD COLUMN IF NOT EXISTS project TEXT REFERENCES projects(slug) ON DELETE CASCADE;
-- 'chat' plans and runs tasks; 'project_design' shapes the project's AGENTS.md.
ALTER TABLE threads ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'chat';
ALTER TABLE tasks   ADD COLUMN IF NOT EXISTS project TEXT REFERENCES projects(slug) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS threads_project ON threads (project, kind, updated_at DESC);
CREATE INDEX IF NOT EXISTS tasks_project ON tasks (project, created_at);


-- `interrupted` was a second name for `paused`: both meant "stopped mid-run,
-- start it again". Rows that had actually run become paused so the queue picks
-- them up; ones that never started were imported placeholders and are closed.
UPDATE tasks SET state = 'paused'
 WHERE state = 'interrupted' AND started_at IS NOT NULL;
UPDATE tasks SET state = 'cancelled'
 WHERE state = 'interrupted';

-- Older databases predate project-wide tasks; everything in them is a package.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'package';

-- Older databases have no notion of a pause dex must not undo.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS held BOOLEAN NOT NULL DEFAULT false;

-- Where in the operator's attached material a task's work came from, as the
-- survey anchored it: `{source, label, page, endPage, ...}` or a URL. JSONB
-- rather than columns because the shape differs per kind of source -- a PDF
-- has pages, a workbook has a sheet, a link has neither.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS anchor JSONB;

-- Tokens, as the agent's own result reported them. Only dollars were kept
-- before, which made "what did the thinking cost" unanswerable: thinking bills
-- as output and is already inside `output_tokens`, so nothing was missing from
-- the total -- it just could not be split out. `thinking_tokens` is that subset,
-- from `output_tokens_details`, NOT an addition to it: summing the two
-- double-counts every thought.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS input_tokens       BIGINT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cache_read_tokens  BIGINT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cache_write_tokens BIGINT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS output_tokens      BIGINT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS thinking_tokens    BIGINT;

-- ---------------------------------------------------------------- identity ---
-- Who may use this dex, and which projects they may see.
--
-- Three roles, and the third is the absence of one: 'admin' sees everything and
-- assigns roles, 'user' sees the projects granted in `user_projects`, and NULL
-- means signed in but not authorised -- deliberately a state rather than a
-- rejection, so the operator can see who is knocking and grant them a role.
CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    -- Google's stable subject id. The email can change; `sub` cannot, so it is
    -- what identity hangs on.
    google_sub TEXT UNIQUE,
    email      TEXT        NOT NULL UNIQUE,
    name       TEXT,
    picture    TEXT,
    role       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS users_email ON users (lower(email));

-- Username and password sign-in, for an installation with no Google client. A
-- Google-only account has neither column set and is identified by its email;
-- one account can have both, and then either way in works.
ALTER TABLE users ADD COLUMN IF NOT EXISTS username      TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
-- Set while a password somebody else chose is still in place. Every route but
-- "change my password" refuses until it clears, so a temporary password cannot
-- quietly become a permanent one.
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users (username);

-- The two-role model became four. `user` could queue work in the projects it
-- was granted, which is exactly `operator`, so that is what it becomes; nothing
-- any existing account could do is taken away. Run before the constraint below,
-- which would otherwise reject the rows this fixes.
UPDATE users SET role = 'operator' WHERE role = 'user';

-- Dropped and recreated rather than added: a CHECK has no IF NOT EXISTS, and
-- the old two-role version is still on databases created before this.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_known;
ALTER TABLE users ADD  CONSTRAINT users_role_known
    CHECK (role IS NULL OR role IN ('admin', 'author', 'operator', 'viewer'));

-- Which projects a non-admin may work in. An admin needs no rows here: their
-- access is their role, so revoking admin does not leave stale grants behind.
CREATE TABLE IF NOT EXISTS user_projects (
    user_id    TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project    TEXT        NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by TEXT        REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (user_id, project)
);
CREATE INDEX IF NOT EXISTS user_projects_project ON user_projects (project);

-- Server-side sessions rather than a self-contained token, so that removing a
-- role takes effect on the next request instead of whenever a JWT expires.
CREATE TABLE IF NOT EXISTS sessions (
    -- The cookie value is a hash of this, never the id itself, so a leaked
    -- database row cannot be replayed as a cookie.
    id         TEXT PRIMARY KEY,
    user_id    TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions (expires_at);

-- Short-lived OAuth state, so a callback cannot be replayed or forged and PKCE
-- has somewhere to keep its verifier between the two legs of the flow.
CREATE TABLE IF NOT EXISTS oauth_states (
    state         TEXT PRIMARY KEY,
    code_verifier TEXT        NOT NULL,
    redirect_to   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The project an event belongs to, denormalised. The live fan-out happens in
-- process with no database round trip, so filtering a subscriber's stream by
-- the projects they may see needs the project on the event itself.
ALTER TABLE events ADD COLUMN IF NOT EXISTS project TEXT;
CREATE INDEX IF NOT EXISTS events_project_seq ON events (project, seq);

-- Backfill from the task that produced each event, so history filters the same
-- way live events do. Cheap and idempotent: only rows that lack it.
UPDATE events e SET project = t.project
  FROM tasks t
 WHERE e.task_id = t.id AND e.project IS NULL AND t.project IS NOT NULL;
