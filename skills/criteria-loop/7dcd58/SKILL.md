---
name: criteria-loop
description: Stage 3 of the construction criteria pipeline — rounds of generate (local LLM via Ollama) → evaluate (Clef-Flash over a stratified probe set, then walks along the BGE nearest-neighbour graph from every hit) → judge (how frequent, how deep, how coherent, how many documents) that keep or drop yes/no criteria, all state in the `criteria` Postgres schema; plus `pipeline backfill`, which runs stages 1–3 in sequence outside dex. Needs edil-corpus, bge-embeddings and clef-decisions.
---

# Criteria loop

| Module | Does |
|---|---|
| `criteria_store` | the `criteria` schema: `seeds`, `criteria`, `rounds`, `decisions`, `usefulness` |
| `usefulness` | probe set, walk planning, the recursive neighbour-walk measurement, the probe-and-walk keep rule |
| `coverage` | the coverage rule (current): judge only after ≥5 documents and >70% of usable chunks; drop only non-confident, low-variance or highly correlated criteria; `criteria.judgements` |
| `generator` | the local LLM that writes and revises criteria (`qwen3.8:27b-mlx` on Ollama) |
| `criteria_loop` | one round end to end, seed picking, status — the stage-3 command |
| `pipeline` | `backfill`: ingest → OCR → embed → seeds → rounds, in sequence |
| `image_sweep` | asks criteria about the picture regions OCR indexed (`corpus.images`) with Clef's image input; `criteria.image_decisions` |

## The backfill

```bash
cd /Users/raduparadovschi/dex/assets/construction
../../.venv/bin/python -m utils.pipeline backfill --projects 30 \
    --seeds /Volumes/external/construction/dex-backups/2026-10-07/criteria-seed.json \
    --pick 40 --rounds 3 --generate 20 2>&1 | tee backfill.log
```

Round 1 asks the 40 picked seeds as they are; rounds 2 and 3 have the generator
write 20 more first. Everything is idempotent — rerun the same command after an
interruption and it continues.

## One round, and what it measures

```bash
../../.venv/bin/python -m utils.criteria_loop round --generate 20
../../.venv/bin/python -m utils.criteria_loop status
```

1. **Probe.** Every live criterion (candidate or kept) is asked about the probe
   set: `--probes` (10) usable chunks per document, chosen by hash so the set is
   stable from round to round.
2. **Walk.** From every probe hit (P ≥ 0.5) the walk steps to the chunk's `--k`
   (5) nearest neighbours in `corpus.neighbors`, asks the criterion there, and
   steps again from the neighbours that fire, up to `--depth` (4).
3. **Measure**, in one recursive query over `criteria.decisions` ⋈ `corpus.neighbors`:
   `probe_rate` (how frequent), `mean_depth` / `max_depth` (how deep a walk
   from a hit keeps firing), `mean_reach` (chunks a walk reaches),
   `walk_precision` (share of walked-to neighbours that fire — topic, not
   keyword), `hit_documents` / `doc_frequency`, `cross_doc_reach`.
4. **Judge** (`usefulness.Rule`): dropped as *absent* (no probe hit),
   *pervasive* (> 30% of probes — it describes the genre), *rare* (< 3
   documents), or *incoherent* (< 20% walk precision over ≥ 5 walked); otherwise
   kept. The verdict and every number land in `criteria.usefulness`.

Clef is deterministic, so a (criterion, chunk) pair is asked once ever: a kept
criterion costs nothing in later rounds until new documents arrive, and a round
stopped halfway loses nothing.

## Cost, and the budget

Clef-Flash runs ~850 tokens/s here, and **each criterion costs ~105 tokens per
chunk it is asked about** on top of the chunk itself. Asking 40 criteria of all
~4,000 chunks would be ~18M tokens — about six hours. Probing 300 chunks is
~1.4M (~30 min); the walk then spends on neighbours of hits only. `--budget`
(2.5M tokens, ~50 min) caps a round; what it did not reach waits for the next.

## From a dex task

Every step is a function, so an agent can run the loop on any slice:

```python
from utils import corpus, criteria_loop, criteria_store, usefulness, clef
conn = await corpus.connect()
await criteria_store.add_criteria(conn, [{"question": clef.yes_no("Does this passage say ...?")}],
                                  origin="generated", round_id=None)
await criteria_loop.run_round(conn, probes_per_document=5, budget_tokens=500_000)
print(await criteria_loop.status(conn))
```

Never run two rounds at once: both would load Clef onto the one GPU, and one
instance is the fastest configuration (see `clef-decisions`).

## Pictures

```bash
../../.venv/bin/python -m utils.image_sweep                  # every kept criterion x every picture
../../.venv/bin/python -m utils.image_sweep --criteria 12 --min-share 0.1 --caption
../../.venv/bin/python -m utils.image_sweep --top 20
```

One record per picture with all its criteria (≤ 32 per record), decided once per
pair. Run it when no text round is using the GPU.

## The coverage rule (current)

```bash
../../.venv/bin/python -m utils.criteria_loop reopen               # dropped -> candidate, re-judge
../../.venv/bin/python -m utils.criteria_loop cover                # ask + judge every live criterion
../../.venv/bin/python -m utils.criteria_loop cover --generate 20 --rounds 2
```

Every live criterion is asked about the same 75% of each document's usable
chunks (`coverage.coverage_set`, hash-chosen), and judged only when it has been
decided on ≥ 5 documents and > 70% of the usable corpus; until then it stays
`candidate`. Once eligible it is dropped for exactly three reasons — **non-
confident** (> 25% of answers between 0.2 and 0.8), **low variance** (std of P
< 0.05), **correlated** (|r| ≥ 0.85 with a kept criterion over shared chunks,
the less spread-out one dropped) — and otherwise kept, however common, rare or
lexical it is. Verdicts and every number land in `criteria.judgements`.

The price is coverage: each criterion costs ~105 tokens × 75% of the usable
chunks, ~170k tokens (~5 min) per criterion at 60 projects.

## Text and picture criteria

`criteria.criteria.modality` is `text` (asked of chunks) or `image` (asked of
`corpus.images`). Coverage rounds use text criteria only; image rounds —
`python -m utils.image_sweep --round --generate 20` — use image criteria only,
written by `generator.propose_visual` ("Does this image show ...?") from the
picture classes and captions on file, and judged by the same coverage rule over
pictures instead of chunks.

The text generator is steered to known risk areas (`generator.RISKS`: cost,
design and documents, site and ground, contract and procurement, schedule,
regulatory, parties and management, finance and draws) and forbidden to name
firms, people, projects, places, dollar figures or dates.

## Target leakage

Criteria are features for predicting change orders, so none may restate the
outcome. Three guards: the generator's prompt forbids criteria about change
orders, amendments, overruns, budget increases, adjustments or reallocations;
`generator.implies_target` refuses any proposal whose wording names them; and
`criteria_loop.screen_leakage` refuses any whose BGE-M3 cosine to a description of
the target is ≥ 0.75 (the leak found so far sat at 0.786; the nearest legitimate
causes at 0.65-0.70). Criteria removed for leakage are `dropped` with a reason
starting `removed: implies the target`.
