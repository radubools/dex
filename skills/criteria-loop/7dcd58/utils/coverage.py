"""Judge criteria on broad coverage: drop only the non-confident, the flat, and the redundant.

The probe-and-walk rule in `usefulness` judges a criterion from ~300 probes and
the neighbourhoods of its hits. This rule instead waits until a criterion has
been asked about most of the corpus, then drops it for exactly three reasons:

- **non-confident** — too many of its answers sit in the undecided middle
  (`Rule.band`, P between 0.4 and 0.6): Clef cannot tell from the text;
- **low variance** — its answers barely move across chunks (standard deviation
  of P under `min_std`): it says the same thing everywhere, yes or no;
- **highly correlated** — its answers track another kept criterion's
  (Pearson r ≥ `max_corr` on the chunks both were asked about): it adds nothing.

A criterion is only judged once it is **eligible**: decided on at least
`min_documents` documents and more than `min_coverage` of the usable chunks.
Until then it stays a `candidate` and the next round keeps asking it.

Coverage comes from `coverage_set()`: a fixed, hash-chosen share of every
document's usable chunks (75% by default), so every criterion is asked about
the same chunks and their answers can be correlated pair by pair.

    from utils import coverage
    cover = await coverage.coverage_set(conn)
    profiles = await coverage.profile(conn, criterion_ids, cover)
    verdicts = coverage.judge(profiles, coverage.Rule())
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

CORPUS = "corpus"
CRIT = "criteria"
#: The undecided middle. Was (0.2, 0.8) for rounds 5-7; loosened by the operator
#: on 2026-10-08 so that only answers Clef is close to a coin-flip on count.
UNCERTAIN_BAND = (0.4, 0.6)


@dataclass
class Rule:
    min_documents: int = 5
    min_coverage: float = 0.70
    #: More than this share of answers inside `band` is non-confident.
    max_uncertain: float = 0.25
    band: tuple[float, float] = UNCERTAIN_BAND
    #: A standard deviation of P below this is low variance. For a criterion that
    #: is near 0/1 everywhere it means firing on fewer than ~1 chunk in 400, or on
    #: all but ~1 in 400.
    min_std: float = 0.05
    #: At or above this |r| with a kept criterion is redundant.
    max_corr: float = 0.85


DDL = f"""
CREATE TABLE IF NOT EXISTS {CRIT}.judgements (
    round_id        int NOT NULL REFERENCES {CRIT}.rounds(id),
    criterion_id    int NOT NULL REFERENCES {CRIT}.criteria(id) ON DELETE CASCADE,
    decided         int NOT NULL,
    coverage        real NOT NULL,
    documents       int NOT NULL,
    hits            int NOT NULL,
    hit_documents   int NOT NULL,
    mean_p          real NOT NULL,
    std_p           real NOT NULL,
    uncertain_share real NOT NULL,
    mean_confidence real NOT NULL,
    max_corr        real,
    corr_with       int REFERENCES {CRIT}.criteria(id) ON DELETE SET NULL,
    verdict         text NOT NULL,            -- kept | dropped | pending
    reason          text NOT NULL,
    PRIMARY KEY (round_id, criterion_id)
);
"""


async def ensure_schema(conn) -> None:
    await conn.execute(DDL)


async def coverage_set(conn, *, share: float = 0.75, salt: str = "cover-v1") -> list[str]:
    """`share` of every document's usable, embedded chunks, chosen by hash (rounded up)."""
    rows = await conn.fetch(
        f"""SELECT id FROM (
                SELECT c.id,
                       row_number() OVER (PARTITION BY c.document_id ORDER BY md5(c.id || $2)) AS r,
                       count(*)    OVER (PARTITION BY c.document_id) AS n
                  FROM {CORPUS}.chunks c JOIN {CORPUS}.embeddings e ON e.chunk_id = c.id
                 WHERE c.usable) s
             WHERE r <= ceil(n * $1::float8) ORDER BY id""",
        share, salt,
    )
    return [r["id"] for r in rows]


async def usable_total(conn) -> int:
    return await conn.fetchval(
        f"SELECT count(*) FROM {CORPUS}.chunks c JOIN {CORPUS}.embeddings e ON e.chunk_id = c.id WHERE c.usable")


async def profile(conn, criterion_ids: list[int], cover: list[str], *, threshold: float = 0.5,
                  band: tuple[float, float] = UNCERTAIN_BAND, table: str = "decisions",
                  item: str = "chunk_id") -> dict[int, dict]:
    """Per criterion: coverage of the usable corpus, spread and confidence of its answers, and
    its strongest correlation with any other criterion in `criterion_ids` over `cover`."""
    import numpy as np

    if table == "decisions":
        total = await usable_total(conn) or 1
        rows = await conn.fetch(
            f"""SELECT d.criterion_id, d.chunk_id AS item, d.p, c.document_id
                  FROM {CRIT}.decisions d JOIN {CORPUS}.chunks c ON c.id = d.chunk_id AND c.usable
                 WHERE d.criterion_id = ANY($1)""",
            criterion_ids,
        )
    else:  # image_decisions: the items are pictures, the corpus is every picture in `cover`
        total = len(cover) or 1
        rows = await conn.fetch(
            f"""SELECT d.criterion_id, d.image_id AS item, d.p, i.document_id
                  FROM {CRIT}.image_decisions d JOIN {CORPUS}.images i ON i.id = d.image_id
                 WHERE d.criterion_id = ANY($1) AND d.image_id = ANY($2)""",
            criterion_ids, cover,
        )
    by_crit: dict[int, dict[str, float]] = {c: {} for c in criterion_ids}
    docs: dict[int, set] = {c: set() for c in criterion_ids}
    hit_docs: dict[int, set] = {c: set() for c in criterion_ids}
    for r in rows:
        by_crit[r["criterion_id"]][r["item"]] = r["p"]
        docs[r["criterion_id"]].add(r["document_id"])
        if r["p"] >= threshold:
            hit_docs[r["criterion_id"]].add(r["document_id"])

    # Correlation over the shared coverage set: a criteria x chunks matrix, NaN where undecided.
    col = {ch: j for j, ch in enumerate(cover)}
    M = np.full((len(criterion_ids), len(cover)), np.nan, dtype=np.float32)
    for i, c in enumerate(criterion_ids):
        for ch, p in by_crit[c].items():
            j = col.get(ch)
            if j is not None:
                M[i, j] = p

    out: dict[int, dict] = {}
    lo, hi = band
    for i, c in enumerate(criterion_ids):
        ps = np.asarray(list(by_crit[c].values()), dtype=np.float32)
        n = len(ps)
        out[c] = {
            "criterion_id": c,
            "decided": n,
            "coverage": n / total,
            "documents": len(docs[c]),
            "hits": int((ps >= threshold).sum()) if n else 0,
            "hit_documents": len(hit_docs[c]),
            "mean_p": float(ps.mean()) if n else 0.0,
            "std_p": float(ps.std()) if n else 0.0,
            "uncertain_share": float(((ps > lo) & (ps < hi)).mean()) if n else 0.0,
            "mean_confidence": float((np.abs(ps - 0.5) * 2).mean()) if n else 0.0,
            "_row": i,
        }
    out["_matrix"] = M  # type: ignore[index]
    return out


def _corr(a, b, min_shared: int = 50) -> float | None:
    import numpy as np

    mask = ~np.isnan(a) & ~np.isnan(b)
    if mask.sum() < min_shared:
        return None
    x, y = a[mask], b[mask]
    if x.std() < 1e-6 or y.std() < 1e-6:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def judge(profiles: dict, rule: Rule = Rule(), *, order: list[int] | None = None) -> list[dict]:
    """Verdicts for every profiled criterion: kept, dropped (with the reason), or pending.

    Correlation is resolved greedily: eligible criteria that pass the first two
    tests are taken most-informative first (highest std of P, then oldest id),
    and each is dropped if it correlates at `max_corr` with one already kept.
    """
    M = profiles["_matrix"]
    items = [p for k, p in profiles.items() if k != "_matrix"]
    verdicts: dict[int, dict] = {}
    survivors = []
    for p in items:
        v = {k: val for k, val in p.items() if not k.startswith("_")}
        v.update(max_corr=None, corr_with=None)
        if p["documents"] < rule.min_documents or p["coverage"] <= rule.min_coverage:
            v.update(verdict="pending",
                     reason=f"pending: {p['coverage']:.0%} coverage over {p['documents']} documents")
        elif p["uncertain_share"] > rule.max_uncertain:
            v.update(verdict="dropped",
                     reason=f"non-confident: {p['uncertain_share']:.0%} of answers between "
                            f"{rule.band[0]} and {rule.band[1]}")
        elif p["std_p"] < rule.min_std:
            v.update(verdict="dropped",
                     reason=f"low variance: std {p['std_p']:.3f}, {p['hits']} hits in {p['decided']}")
        else:
            survivors.append(p)
            v.update(verdict="kept")
        verdicts[p["criterion_id"]] = v

    survivors.sort(key=lambda p: (-p["std_p"], p["criterion_id"]))
    kept: list[dict] = []
    for p in survivors:
        best_r, best_c = None, None
        for k in kept:
            r = _corr(M[p["_row"]], M[k["_row"]])
            if r is not None and (best_r is None or abs(r) > abs(best_r)):
                best_r, best_c = r, k["criterion_id"]
        v = verdicts[p["criterion_id"]]
        v.update(max_corr=best_r, corr_with=best_c)
        if best_r is not None and abs(best_r) >= rule.max_corr:
            v.update(verdict="dropped", reason=f"correlated: r={best_r:.2f} with criterion {best_c}")
        else:
            kept.append(p)
            corr = f", max r {best_r:.2f}" if best_r is not None else ""
            v["reason"] = (f"kept: {p['coverage']:.0%} coverage, {p['documents']} documents, "
                           f"{p['hits']} hits in {p['hit_documents']} documents, std {p['std_p']:.2f}, "
                           f"{p['uncertain_share']:.0%} uncertain{corr}")
    return list(verdicts.values())


async def record(conn, round_id: int, verdicts: list[dict]) -> None:
    """Write the judgements and move every eligible criterion to kept/dropped."""
    cols = ["decided", "coverage", "documents", "hits", "hit_documents", "mean_p", "std_p",
            "uncertain_share", "mean_confidence", "max_corr", "corr_with", "verdict", "reason"]
    async with conn.transaction():
        await conn.executemany(
            f"""INSERT INTO {CRIT}.judgements (round_id, criterion_id, {", ".join(cols)})
                VALUES ($1, $2, {", ".join(f"${i + 3}" for i in range(len(cols)))})
                ON CONFLICT (round_id, criterion_id) DO UPDATE SET
                {", ".join(f"{c} = EXCLUDED.{c}" for c in cols)}""",
            [(round_id, v["criterion_id"], *[v[c] for c in cols]) for v in verdicts],
        )
        await conn.executemany(
            f"UPDATE {CRIT}.criteria SET status = $2, reason = $3, judged_in = $4 WHERE id = $1",
            [(v["criterion_id"], v["verdict"] if v["verdict"] != "pending" else "candidate",
              v["reason"], round_id) for v in verdicts],
        )


def rule_dict(rule: Rule) -> dict:
    return asdict(rule)
