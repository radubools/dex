## `bge`

Embed text locally with BGE-M3 through Ollama's `/api/embed`.

- `HOST` — constant
- `MODEL` — constant
- `DIM` — constant
- `BATCH` — constant
- `RETRIES` — constant
- `class EmbedError` — 
- `embed(texts: list[str], *, model: str=MODEL) -> list[list[float]]` — One vector per text, in order. Ollama truncates past the model's context.
- `embed_batches(items: Iterable[tuple[str, str]], *, batch: int=BATCH, model: str=MODEL) -> Iterator[tuple[list[str], list[list[float]]]]` — `(ids, vectors)` per batch of `(id, text)` pairs, so callers can write as they go.
- `available(model: str=MODEL) -> bool` — Whether Ollama is up and has `model` pulled.
- `vector_literal(vector: list[float]) -> str` — A pgvector text literal, `[0.1,0.2,...]`, for a `$n::vector` parameter.

## `embed_stage`

Stage 2: embed every chunk that has no BGE-M3 vector yet, then rebuild the neighbour graph.

- `run(conn, *, usable_only: bool=True, k: int=neighbors.K, batch: int=bge.BATCH, document_ids: list[str] | None=None, graph: bool=True, log=print) -> dict` — 

## `neighbors`

Store chunk embeddings in pgvector and materialise a k-nearest-neighbour graph.

- `SCHEMA` — constant
- `K` — constant
- `DDL` — constant
- `ensure_schema(conn) -> None` — 
- `missing(conn, *, model: str=bge.MODEL, usable_only: bool=True, document_ids: list[str] | None=None) -> list[tuple[str, str]]` — `(chunk_id, text)` for chunks with no embedding from `model` yet.
- `write(conn, ids: list[str], vectors: list[list[float]], *, model: str=bge.MODEL) -> None` — 
- `build_graph(conn, *, k: int=K) -> int` — Rebuild `corpus.neighbors` from every stored embedding. Returns the edge count.
- `graph_stats(conn) -> dict` — 
- `nearest_chunks(conn, chunk_id: str, *, k: int=K) -> list[dict]` — 
- `search(conn, text: str, *, k: int=K, model: str=bge.MODEL) -> list[dict]` — The `k` chunks nearest to an arbitrary piece of text (HNSW-approximate).