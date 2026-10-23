"""The `criteria` schema: criteria, rounds, per-chunk decisions, and per-round usefulness.

    criteria.seeds       wordings imported from earlier work (the Laya/Jev sweeps)
    criteria.criteria    every criterion ever proposed, with its status
    criteria.rounds      one row per round of generate -> evaluate -> judge
    criteria.decisions   P(yes) per (criterion, chunk); Clef is deterministic, so a
                         pair is asked once and reused by every later round
    criteria.usefulness  what each round measured about each criterion, and the verdict
    criteria.image_decisions  P(yes) per (criterion, picture) from Clef-Flash's image input

A criterion's `status` is `candidate` until a round judges it, then `kept` or
`dropped` with the `reason`. Its `question` is the SystemOne question dict, so
`clef.ask(text, {name: question})` asks it as stored.
"""

from __future__ import annotations

import json
import re

from . import corpus

SCHEMA = "criteria"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.seeds (
    id        serial PRIMARY KEY,
    source    text NOT NULL,
    name      text NOT NULL,
    question  jsonb NOT NULL,
    best_j    real,
    runs      int,
    wording   text GENERATED ALWAYS AS (question->>'instructions') STORED,
    UNIQUE (source, name)
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.rounds (
    id          serial PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    generator   text,
    evaluator   text NOT NULL,
    params      jsonb NOT NULL DEFAULT '{{}}',
    stats       jsonb NOT NULL DEFAULT '{{}}',
    note        text
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.criteria (
    id          serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    question    jsonb NOT NULL,
    wording     text GENERATED ALWAYS AS (question->>'instructions') STORED,
    origin      text NOT NULL,              -- seed | generated | revised
    parent_id   int REFERENCES {SCHEMA}.criteria(id),
    round_born  int REFERENCES {SCHEMA}.rounds(id),
    status      text NOT NULL DEFAULT 'candidate',
    reason      text,
    judged_in   int REFERENCES {SCHEMA}.rounds(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS criteria_wording ON {SCHEMA}.criteria (lower(wording));
-- What a criterion is asked about: `text` (chunks) or `image` (corpus.images).
ALTER TABLE {SCHEMA}.criteria ADD COLUMN IF NOT EXISTS modality text NOT NULL DEFAULT 'text';
CREATE TABLE IF NOT EXISTS {SCHEMA}.decisions (
    criterion_id int NOT NULL REFERENCES {SCHEMA}.criteria(id) ON DELETE CASCADE,
    chunk_id     text NOT NULL REFERENCES corpus.chunks(id) ON DELETE CASCADE,
    p            real NOT NULL,
    how          text NOT NULL,              -- probe | walk
    round_id     int NOT NULL REFERENCES {SCHEMA}.rounds(id),
    evaluator    text NOT NULL,
    PRIMARY KEY (criterion_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS decisions_chunk ON {SCHEMA}.decisions (chunk_id);
CREATE TABLE IF NOT EXISTS {SCHEMA}.image_decisions (
    criterion_id int NOT NULL REFERENCES {SCHEMA}.criteria(id) ON DELETE CASCADE,
    image_id     text NOT NULL REFERENCES corpus.images(id) ON DELETE CASCADE,
    p            real NOT NULL,
    with_caption boolean NOT NULL,
    evaluator    text NOT NULL,
    decided_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (criterion_id, image_id)
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.usefulness (
    round_id        int NOT NULL REFERENCES {SCHEMA}.rounds(id),
    criterion_id    int NOT NULL REFERENCES {SCHEMA}.criteria(id) ON DELETE CASCADE,
    probes          int NOT NULL,
    probe_hits      int NOT NULL,
    probe_rate      real NOT NULL,
    walked          int NOT NULL,
    walk_hits       int NOT NULL,
    walk_precision  real,
    seeds           int NOT NULL,
    mean_depth      real,
    max_depth       int,
    mean_reach      real,
    hit_documents   int NOT NULL,
    doc_frequency   real NOT NULL,
    cross_doc_reach real,
    keep            boolean NOT NULL,
    reason          text NOT NULL,
    PRIMARY KEY (round_id, criterion_id)
);
"""


async def ensure_schema(conn) -> None:
    await corpus.ensure_schema(conn)  # decisions reference corpus.chunks and corpus.images
    await conn.execute(DDL)


def slug(text: str, *, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    s = re.sub(r"^(does_this_passage_say_|does_this_text_|does_the_passage_|is_there_)", "", s)
    return s[:limit].rstrip("_") or "criterion"


async def import_seeds(conn, rows: list[dict], *, source: str) -> int:
    """Load the backup's `criteria-seed.json` rows (`kind, name, question, best_j, runs`)."""
    await conn.executemany(
        f"""INSERT INTO {SCHEMA}.seeds (source, name, question, best_j, runs)
            VALUES ($1, $2, $3::jsonb, $4, $5) ON CONFLICT (source, name) DO NOTHING""",
        [(f"{source}:{r.get('kind', '')}", r["name"], json.dumps(r["question"]), r.get("best_j"),
          r.get("runs")) for r in rows if r.get("question", {}).get("instructions")],
    )
    return await conn.fetchval(f"SELECT count(*) FROM {SCHEMA}.seeds")


async def add_criteria(conn, items: list[dict], *, origin: str, round_id: int | None,
                       modality: str = "text") -> list[int]:
    """Insert criteria (`{"question", "name"?, "parent_id"?}`), skipping wordings already present.

    Returns the ids of the rows actually inserted.
    """
    ids = []
    for item in items:
        q = item["question"]
        base = item.get("name") or slug(q["instructions"])
        name, n = base, 1
        while await conn.fetchval(f"SELECT 1 FROM {SCHEMA}.criteria WHERE name = $1", name):
            n += 1
            name = f"{base}_{n}"
        cid = await conn.fetchval(
            f"""INSERT INTO {SCHEMA}.criteria (name, question, origin, parent_id, round_born, modality)
                VALUES ($1, $2::jsonb, $3, $4, $5, $6)
                ON CONFLICT (lower(wording)) DO NOTHING RETURNING id""",
            name, json.dumps(q), origin, item.get("parent_id"), round_id, modality,
        )
        if cid:
            ids.append(cid)
    return ids


async def start_round(conn, *, evaluator: str, generator: str | None, params: dict,
                      note: str | None = None) -> int:
    return await conn.fetchval(
        f"""INSERT INTO {SCHEMA}.rounds (evaluator, generator, params, note)
            VALUES ($1, $2, $3::jsonb, $4) RETURNING id""",
        evaluator, generator, json.dumps(params), note,
    )


async def finish_round(conn, round_id: int, stats: dict) -> None:
    await conn.execute(
        f"UPDATE {SCHEMA}.rounds SET finished_at = now(), stats = $2::jsonb WHERE id = $1",
        round_id, json.dumps(stats),
    )


async def criteria(conn, *, status: str | list[str] | None = None, ids: list[int] | None = None,
                   modality: str | None = None) -> list[dict]:
    """Criteria by status, id and modality (`text` — asked of chunks — or `image` — of pictures)."""
    statuses = [status] if isinstance(status, str) else status
    rows = await conn.fetch(
        f"""SELECT * FROM {SCHEMA}.criteria
             WHERE ($1::text[] IS NULL OR status = ANY($1)) AND ($2::int[] IS NULL OR id = ANY($2))
               AND ($3::text IS NULL OR modality = $3)
             ORDER BY id""",
        statuses, ids, modality,
    )
    return [{**dict(r), "question": json.loads(r["question"])} for r in rows]


async def decided(conn, criterion_ids: list[int]) -> set[tuple[int, str]]:
    rows = await conn.fetch(
        f"SELECT criterion_id, chunk_id FROM {SCHEMA}.decisions WHERE criterion_id = ANY($1)",
        criterion_ids,
    )
    return {(r["criterion_id"], r["chunk_id"]) for r in rows}


async def write_decisions(conn, rows: list[tuple[int, str, float, str]], *, round_id: int,
                          evaluator: str) -> None:
    """`rows` are `(criterion_id, chunk_id, p, how)`."""
    await conn.executemany(
        f"""INSERT INTO {SCHEMA}.decisions (criterion_id, chunk_id, p, how, round_id, evaluator)
            VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (criterion_id, chunk_id) DO NOTHING""",
        [(c, ch, float(p), how, round_id, evaluator) for c, ch, p, how in rows],
    )


async def judge(conn, round_id: int, verdicts: list[dict]) -> None:
    """Record a round's usefulness rows and move each criterion to kept/dropped."""
    cols = ["probes", "probe_hits", "probe_rate", "walked", "walk_hits", "walk_precision", "seeds",
            "mean_depth", "max_depth", "mean_reach", "hit_documents", "doc_frequency",
            "cross_doc_reach", "keep", "reason"]
    async with conn.transaction():
        await conn.executemany(
            f"""INSERT INTO {SCHEMA}.usefulness (round_id, criterion_id, {", ".join(cols)})
                VALUES ($1, $2, {", ".join(f"${i + 3}" for i in range(len(cols)))})
                ON CONFLICT (round_id, criterion_id) DO UPDATE SET
                {", ".join(f"{c} = EXCLUDED.{c}" for c in cols)}""",
            [(round_id, v["criterion_id"], *[v[c] for c in cols]) for v in verdicts],
        )
        await conn.executemany(
            f"""UPDATE {SCHEMA}.criteria SET status = $2, reason = $3, judged_in = $4 WHERE id = $1""",
            [(v["criterion_id"], "kept" if v["keep"] else "dropped", v["reason"], round_id)
             for v in verdicts],
        )
