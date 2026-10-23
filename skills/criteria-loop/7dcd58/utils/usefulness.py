"""Judge criteria by walking the chunk-neighbour graph: how far and how widely does each one reach?

A criterion's probability on one chunk says little. What makes it worth keeping
is how it behaves across the corpus, and the BGE neighbour graph
(`corpus.neighbors`) is what lets that be measured without asking it about every
chunk:

1. **Probe.** Ask every criterion about a fixed, stratified sample of chunks —
   `probe_set()`: the same few chunks from every document, chosen by hash, so a
   round's numbers compare with the last round's.
2. **Walk.** From each probe chunk where a criterion fires, step to its nearest
   neighbours and ask the criterion there; from those that fire, step again, up
   to `max_depth`. `walk_plan()` says which (criterion, chunk) pairs the next
   step needs — only pairs never asked before, since Clef is deterministic.
3. **Measure** — `measure()`, one recursive query over `criteria.decisions` and
   `corpus.neighbors`:

   - `probe_rate`      share of probes it fires on: *how frequent*
   - `mean_depth`      steps a walk from a seed hit keeps firing: *how deep*
   - `mean_reach`      distinct chunks a seed's walk reaches
   - `walk_precision`  share of walked-to neighbours it fires on — high means
                       it tracks a topic the embedding also sees; low means it
                       fires on words, not meaning
   - `hit_documents`   distinct documents it fired on anywhere
   - `cross_doc_reach` mean documents a single seed's walk crosses into

4. **Judge** — `verdict()` applies the keep rule; every threshold is a keyword.

"Fires" means P(yes) >= `threshold` (default 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass

CORPUS = "corpus"
CRIT = "criteria"


@dataclass
class Rule:
    threshold: float = 0.5
    #: More than this share of probes firing is a criterion about the genre, not the project.
    max_probe_rate: float = 0.30
    #: Fewer documents than this and it describes one report, not a pattern.
    min_documents: int = 3
    #: Below this share of walked-to neighbours firing (with enough walked) it is lexical noise.
    min_walk_precision: float = 0.20
    min_walked: int = 5


async def probe_set(conn, *, per_document: int = 10, salt: str = "probe-v1") -> list[str]:
    """`per_document` usable, embedded chunks from every document, chosen by hash."""
    rows = await conn.fetch(
        f"""SELECT id FROM (
                SELECT c.id, row_number() OVER (PARTITION BY c.document_id
                                                ORDER BY md5(c.id || $2)) AS r
                  FROM {CORPUS}.chunks c JOIN {CORPUS}.embeddings e ON e.chunk_id = c.id
                 WHERE c.usable) s
             WHERE r <= $1 ORDER BY id""",
        per_document, salt,
    )
    return [r["id"] for r in rows]


async def walk_plan(conn, criterion_ids: list[int], *, threshold: float, k: int,
                    frontier: set[tuple[int, str]]) -> dict[str, set[int]]:
    """`{chunk_id: {criterion_id, ...}}` still to ask: undecided usable neighbours of `frontier` hits."""
    if not frontier:
        return {}
    crit = [c for c, _ in frontier]
    chunk = [ch for _, ch in frontier]
    rows = await conn.fetch(
        f"""SELECT DISTINCT f.criterion_id, n.neighbor_id
              FROM unnest($1::int[], $2::text[]) AS f(criterion_id, chunk_id)
              JOIN {CORPUS}.neighbors n ON n.chunk_id = f.chunk_id AND n.rank <= $3
              JOIN {CORPUS}.chunks c ON c.id = n.neighbor_id AND c.usable
              LEFT JOIN {CRIT}.decisions d ON d.criterion_id = f.criterion_id AND d.chunk_id = n.neighbor_id
             WHERE d.chunk_id IS NULL""",
        crit, chunk, k,
    )
    plan: dict[str, set[int]] = {}
    for r in rows:
        plan.setdefault(r["neighbor_id"], set()).add(r["criterion_id"])
    return plan


async def neighbor_hits(conn, frontier: set[tuple[int, str]], *, threshold: float,
                        k: int) -> set[tuple[int, str]]:
    """Neighbours of `frontier` chunks where the same criterion has already fired."""
    if not frontier:
        return set()
    rows = await conn.fetch(
        f"""SELECT DISTINCT f.criterion_id, n.neighbor_id
              FROM unnest($1::int[], $2::text[]) AS f(criterion_id, chunk_id)
              JOIN {CORPUS}.neighbors n ON n.chunk_id = f.chunk_id AND n.rank <= $3
              JOIN {CRIT}.decisions d ON d.criterion_id = f.criterion_id
                                     AND d.chunk_id = n.neighbor_id AND d.p >= $4""",
        [c for c, _ in frontier], [ch for _, ch in frontier], k, threshold,
    )
    return {(r["criterion_id"], r["neighbor_id"]) for r in rows}


async def hits_among(conn, pairs: list[tuple[int, str]], threshold: float) -> set[tuple[int, str]]:
    if not pairs:
        return set()
    rows = await conn.fetch(
        f"""SELECT d.criterion_id, d.chunk_id FROM {CRIT}.decisions d
              JOIN unnest($1::int[], $2::text[]) AS f(criterion_id, chunk_id)
                ON f.criterion_id = d.criterion_id AND f.chunk_id = d.chunk_id
             WHERE d.p >= $3""",
        [c for c, _ in pairs], [ch for _, ch in pairs], threshold,
    )
    return {(r["criterion_id"], r["chunk_id"]) for r in rows}


async def measure(conn, criterion_ids: list[int], probes: list[str], *, threshold: float = 0.5,
                  k: int = 5, max_depth: int = 4) -> dict[int, dict]:
    """The usefulness numbers for each criterion, from everything decided so far."""
    docs_total = await conn.fetchval(
        f"SELECT count(DISTINCT c.document_id) FROM {CORPUS}.chunks c JOIN {CORPUS}.embeddings e ON e.chunk_id = c.id"
    ) or 1
    base = await conn.fetch(
        f"""SELECT d.criterion_id,
                   count(*) FILTER (WHERE d.chunk_id = ANY($2)) AS probes,
                   count(*) FILTER (WHERE d.chunk_id = ANY($2) AND d.p >= $3) AS probe_hits,
                   count(*) FILTER (WHERE NOT d.chunk_id = ANY($2)) AS walked,
                   count(*) FILTER (WHERE NOT d.chunk_id = ANY($2) AND d.p >= $3) AS walk_hits,
                   count(DISTINCT c.document_id) FILTER (WHERE d.p >= $3) AS hit_documents
              FROM {CRIT}.decisions d JOIN {CORPUS}.chunks c ON c.id = d.chunk_id
             WHERE d.criterion_id = ANY($1)
             GROUP BY d.criterion_id""",
        criterion_ids, probes, threshold,
    )
    walks = await conn.fetch(
        f"""WITH RECURSIVE
            hits AS (SELECT criterion_id, chunk_id FROM {CRIT}.decisions
                      WHERE criterion_id = ANY($1) AND p >= $3),
            walk(criterion_id, seed, chunk, depth) AS (
                SELECT criterion_id, chunk_id, chunk_id, 0 FROM hits WHERE chunk_id = ANY($2)
                UNION
                SELECT w.criterion_id, w.seed, n.neighbor_id, w.depth + 1
                  FROM walk w
                  JOIN {CORPUS}.neighbors n ON n.chunk_id = w.chunk AND n.rank <= $4
                  JOIN hits h ON h.criterion_id = w.criterion_id AND h.chunk_id = n.neighbor_id
                 WHERE w.depth < $5),
            per_seed AS (
                SELECT w.criterion_id, w.seed, max(w.depth) AS depth,
                       count(DISTINCT w.chunk) AS reach, count(DISTINCT c.document_id) AS docs
                  FROM walk w JOIN {CORPUS}.chunks c ON c.id = w.chunk
                 GROUP BY w.criterion_id, w.seed)
            SELECT criterion_id, count(*) AS seeds, avg(depth)::real AS mean_depth,
                   max(depth) AS max_depth, avg(reach)::real AS mean_reach,
                   avg(docs)::real AS cross_doc_reach
              FROM per_seed GROUP BY criterion_id""",
        criterion_ids, probes, threshold, k, max_depth,
    )
    by_walk = {r["criterion_id"]: dict(r) for r in walks}
    out: dict[int, dict] = {}
    for r in base:
        cid = r["criterion_id"]
        w = by_walk.get(cid, {})
        out[cid] = {
            "criterion_id": cid,
            "probes": r["probes"],
            "probe_hits": r["probe_hits"],
            "probe_rate": r["probe_hits"] / r["probes"] if r["probes"] else 0.0,
            "walked": r["walked"],
            "walk_hits": r["walk_hits"],
            "walk_precision": r["walk_hits"] / r["walked"] if r["walked"] else None,
            "seeds": w.get("seeds", 0),
            "mean_depth": w.get("mean_depth"),
            "max_depth": w.get("max_depth"),
            "mean_reach": w.get("mean_reach"),
            "hit_documents": r["hit_documents"],
            "doc_frequency": r["hit_documents"] / docs_total,
            "cross_doc_reach": w.get("cross_doc_reach"),
        }
    return out


def verdict(m: dict, rule: Rule = Rule()) -> dict:
    """`m` with `keep` and a one-line `reason` added."""
    if m["probe_hits"] == 0 and m["walk_hits"] == 0:
        keep, reason = False, f"absent: fired on none of {m['probes']} probes"
    elif m["probe_rate"] > rule.max_probe_rate:
        keep, reason = False, f"pervasive: fired on {m['probe_rate']:.0%} of probes"
    elif m["hit_documents"] < rule.min_documents:
        keep, reason = False, f"rare: fired in only {m['hit_documents']} document(s)"
    elif (m["walk_precision"] is not None and m["walked"] >= rule.min_walked
          and m["walk_precision"] < rule.min_walk_precision):
        keep, reason = False, (f"incoherent: {m['walk_precision']:.0%} of its hits' neighbours share it "
                               f"({m['walked']} walked)")
    else:
        keep = True
        depth = f"{m['mean_depth']:.1f}" if m["mean_depth"] is not None else "-"
        prec = f"{m['walk_precision']:.0%}" if m["walk_precision"] is not None else "-"
        reason = (f"kept: {m['probe_rate']:.0%} of probes, {m['hit_documents']} documents, "
                  f"depth {depth}, walk precision {prec}")
    return {**m, "keep": keep, "reason": reason}
