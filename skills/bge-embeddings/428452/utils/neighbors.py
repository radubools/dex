"""Store chunk embeddings in pgvector and materialise a k-nearest-neighbour graph.

Two tables beside `corpus.chunks` (from the `edil-corpus` skill):

- `corpus.embeddings` — one BGE-M3 vector per chunk, with an HNSW cosine index
  for ad-hoc "what reads like this" queries.
- `corpus.neighbors` — each chunk's `k` nearest other chunks, ranked, with the
  cosine distance and whether the neighbour is from the same document. This is
  the graph the criteria loop walks: a criterion that keeps firing as you step
  from a chunk to its neighbours is about something the corpus says in many
  places; one that fires once and nowhere nearby is noise.

    import asyncio
    from utils import corpus, neighbors

    async def main():
        conn = await corpus.connect()
        await neighbors.ensure_schema(conn)
        print(await neighbors.nearest_chunks(conn, "a chunk id", k=5))
        print(await neighbors.search(conn, "lender has not approved the change order", k=5))
    asyncio.run(main())

The graph is exact (a sequential scan per chunk), not HNSW-approximate: at a few
thousand chunks the exact build takes seconds and the walk should not depend on
index recall.
"""

from __future__ import annotations

from . import bge

SCHEMA = "corpus"
K = 10

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS {SCHEMA}.embeddings (
    chunk_id    text PRIMARY KEY REFERENCES {SCHEMA}.chunks(id) ON DELETE CASCADE,
    model       text NOT NULL,
    embedding   vector({bge.DIM}) NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS embeddings_hnsw ON {SCHEMA}.embeddings
    USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS {SCHEMA}.neighbors (
    chunk_id      text NOT NULL REFERENCES {SCHEMA}.chunks(id) ON DELETE CASCADE,
    rank          int NOT NULL,
    neighbor_id   text NOT NULL REFERENCES {SCHEMA}.chunks(id) ON DELETE CASCADE,
    distance      real NOT NULL,
    same_document boolean NOT NULL,
    PRIMARY KEY (chunk_id, rank)
);
CREATE INDEX IF NOT EXISTS neighbors_neighbor ON {SCHEMA}.neighbors (neighbor_id);
"""


async def ensure_schema(conn) -> None:
    await conn.execute(DDL)


async def missing(conn, *, model: str = bge.MODEL, usable_only: bool = True,
                  document_ids: list[str] | None = None) -> list[tuple[str, str]]:
    """`(chunk_id, text)` for chunks with no embedding from `model` yet."""
    rows = await conn.fetch(
        f"""SELECT c.id, c.text FROM {SCHEMA}.chunks c
             LEFT JOIN {SCHEMA}.embeddings e ON e.chunk_id = c.id AND e.model = $1
             WHERE e.chunk_id IS NULL AND (NOT $2 OR c.usable)
               AND ($3::text[] IS NULL OR c.document_id = ANY($3))
             ORDER BY c.document_id, c.ord""",
        model, usable_only, document_ids,
    )
    return [(r["id"], r["text"]) for r in rows]


async def write(conn, ids: list[str], vectors: list[list[float]], *, model: str = bge.MODEL) -> None:
    await conn.executemany(
        f"""INSERT INTO {SCHEMA}.embeddings (chunk_id, model, embedding) VALUES ($1, $2, $3::vector)
            ON CONFLICT (chunk_id) DO UPDATE SET model = EXCLUDED.model,
                embedding = EXCLUDED.embedding, embedded_at = now()""",
        [(cid, model, bge.vector_literal(v)) for cid, v in zip(ids, vectors)],
    )


async def build_graph(conn, *, k: int = K) -> int:
    """Rebuild `corpus.neighbors` from every stored embedding. Returns the edge count."""
    async with conn.transaction():
        await conn.execute(f"TRUNCATE {SCHEMA}.neighbors")
        await conn.execute("SET LOCAL enable_indexscan = off")  # exact, not HNSW-approximate
        await conn.execute(
            f"""INSERT INTO {SCHEMA}.neighbors (chunk_id, rank, neighbor_id, distance, same_document)
                SELECT e.chunk_id, n.rank, n.chunk_id, n.distance, n.document_id = c.document_id
                  FROM {SCHEMA}.embeddings e
                  JOIN {SCHEMA}.chunks c ON c.id = e.chunk_id
                  CROSS JOIN LATERAL (
                      SELECT e2.chunk_id, c2.document_id,
                             (e.embedding <=> e2.embedding)::real AS distance,
                             row_number() OVER (ORDER BY e.embedding <=> e2.embedding)::int AS rank
                        FROM {SCHEMA}.embeddings e2
                        JOIN {SCHEMA}.chunks c2 ON c2.id = e2.chunk_id
                       WHERE e2.chunk_id <> e.chunk_id
                       ORDER BY e.embedding <=> e2.embedding
                       LIMIT $1) n""",
            k,
        )
    return await conn.fetchval(f"SELECT count(*) FROM {SCHEMA}.neighbors")


async def graph_stats(conn) -> dict:
    row = await conn.fetchrow(
        f"""SELECT count(DISTINCT chunk_id) AS nodes, count(*) AS edges,
                   avg(distance)::real AS mean_distance,
                   avg(same_document::int)::real AS same_document_share
              FROM {SCHEMA}.neighbors"""
    )
    return dict(row)


async def nearest_chunks(conn, chunk_id: str, *, k: int = K) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT n.rank, n.neighbor_id, n.distance, n.same_document, c.text
              FROM {SCHEMA}.neighbors n JOIN {SCHEMA}.chunks c ON c.id = n.neighbor_id
             WHERE n.chunk_id = $1 AND n.rank <= $2 ORDER BY n.rank""",
        chunk_id, k,
    )
    return [dict(r) for r in rows]


async def search(conn, text: str, *, k: int = K, model: str = bge.MODEL) -> list[dict]:
    """The `k` chunks nearest to an arbitrary piece of text (HNSW-approximate)."""
    vector = bge.vector_literal(bge.embed([text], model=model)[0])
    rows = await conn.fetch(
        f"""SELECT e.chunk_id, (e.embedding <=> $1::vector)::real AS distance, c.text,
                   d.building_type, d.project_folder
              FROM {SCHEMA}.embeddings e
              JOIN {SCHEMA}.chunks c ON c.id = e.chunk_id
              JOIN {SCHEMA}.documents d ON d.id = c.document_id
             ORDER BY e.embedding <=> $1::vector LIMIT $2""",
        vector, k,
    )
    return [dict(r) for r in rows]
