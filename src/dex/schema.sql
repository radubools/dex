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
    -- edit across the packages a project already has.
    scope        TEXT        NOT NULL DEFAULT 'package',
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
