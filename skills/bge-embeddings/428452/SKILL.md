---
name: bge-embeddings
description: Stage 2 of the construction criteria pipeline — embed corpus chunks locally with BGE-M3 through Ollama, store the vectors in pgvector (`corpus.embeddings`, HNSW-indexed), and materialise each chunk's k nearest neighbours (`corpus.neighbors`) as the graph the criteria loop walks; also semantic search over the corpus. Needs `edil-corpus`.
---

# BGE embeddings

| Module | Does |
|---|---|
| `bge` | `embed(texts)` / `embed_batches(items)` against Ollama's `/api/embed` with `bge-m3` (1,024 dims, normalised) |
| `neighbors` | the `corpus.embeddings` and `corpus.neighbors` tables, `build_graph(k)`, `nearest_chunks(id)`, `search(text)` |
| `embed_stage` | the stage-2 command |

```bash
cd /Users/raduparadovschi/dex/assets/construction
../../.venv/bin/python -m utils.embed_stage              # usable chunks, k = 10
../../.venv/bin/python -m utils.embed_stage --k 15 --all
```

```python
from utils import corpus, neighbors
conn = await corpus.connect()
await neighbors.search(conn, "the lender has not approved the change order", k=5)
await neighbors.nearest_chunks(conn, chunk_id, k=5)
```

## Numbers

On the M4 Max, ~260-token chunks: 35/s at batch 16, **43/s at 64 and 128** — the
backfill embeds in about two minutes, and batch size beyond 64 buys nothing.
`llama-server --embedding` would serve the same GGUF; it is not used because the
binary in `/usr/local/bin` fails to load (`no LC_RPATH's found`) and Ollama is
not the bottleneck anywhere in the pipeline — Clef-Flash is.

## The graph

`build_graph` is exact (a sequential scan per chunk, the HNSW index switched off
for it): a walk's results must not depend on index recall. It is rebuilt from
every stored embedding each run, because adding documents changes old chunks'
neighbours. `same_document` is recorded per edge — a criterion whose walks never
leave the document it started in is about that report, not the corpus.

Requires the `vector` extension (Postgres.app ships pgvector 0.8.1;
`ensure_schema` creates it).
