"""Embed text locally with BGE-M3 through Ollama's `/api/embed`.

BGE-M3 is the open-source multilingual embedding model (568M parameters, 1,024
dimensions, 8,192-token input); Ollama serves it on the GPU and takes a list of
inputs per request, so batching is one call:

    from utils import bge
    vectors = bge.embed(["first chunk", "second chunk"])    # list of 1,024-float lists
    for ids, vecs in bge.embed_batches(items, batch=64):   # items: [(id, text), ...]
        ...

Measured on the M4 Max with eDIL chunks of ~260 tokens: 35 chunks/s at a batch
of 16, 43 at 64 and 128 — so the 30-project backfill embeds in about two minutes
and batch size past 64 buys nothing. The vectors come back L2-normalised, so
cosine distance and inner product rank identically.

`llama-server --embedding` would serve the same model; it is not used because
the binary on this machine fails to load (`no LC_RPATH's found`) and Ollama is
already within a few percent of the GPU's throughput on this workload.

`OLLAMA_HOST` overrides the server (default http://127.0.0.1:11434).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Iterable, Iterator

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not HOST.startswith("http"):
    HOST = "http://" + HOST
MODEL = "bge-m3"
DIM = 1024
BATCH = 64
RETRIES = 3


class EmbedError(RuntimeError):
    pass


def _post(path: str, body: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(
        HOST.rstrip("/") + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code < 500:
                raise EmbedError(f"{exc.code} from Ollama: {detail}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(2 ** attempt)
    raise EmbedError(f"Ollama at {HOST} did not answer: {last}")


def embed(texts: list[str], *, model: str = MODEL) -> list[list[float]]:
    """One vector per text, in order. Ollama truncates past the model's context."""
    if not texts:
        return []
    out = _post("/api/embed", {"model": model, "input": texts})
    vectors = out.get("embeddings") or []
    if len(vectors) != len(texts):
        raise EmbedError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
    return vectors


def embed_batches(items: Iterable[tuple[str, str]], *, batch: int = BATCH,
                  model: str = MODEL) -> Iterator[tuple[list[str], list[list[float]]]]:
    """`(ids, vectors)` per batch of `(id, text)` pairs, so callers can write as they go."""
    ids: list[str] = []
    texts: list[str] = []
    for cid, text in items:
        ids.append(cid)
        texts.append(text)
        if len(ids) == batch:
            yield ids, embed(texts, model=model)
            ids, texts = [], []
    if ids:
        yield ids, embed(texts, model=model)


def available(model: str = MODEL) -> bool:
    """Whether Ollama is up and has `model` pulled."""
    try:
        req = urllib.request.Request(HOST.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            names = {m["name"].split(":")[0] for m in json.load(resp).get("models", [])}
    except Exception:  # noqa: BLE001
        return False
    return model.split(":")[0] in names


def vector_literal(vector: list[float]) -> str:
    """A pgvector text literal, `[0.1,0.2,...]`, for a `$n::vector` parameter."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
