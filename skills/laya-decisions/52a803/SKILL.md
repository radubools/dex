---
name: laya-decisions
description: Ask a local model typed questions about a piece of text — yes/no, multiple choice, or a continuous rubric score — and get calibrated probabilities back, with the 512-token budget worked out and the text split to fit.
---

# Laya decisions

Three modules. `utils/laya.py` puts typed questions to a piece of text and gets
numbers back — that is the whole of the model. `utils/sweep.py` is for when you
are asking the *same* corpus questions more than once and want to improve the
questions: it stores the text once by hash and keeps each attempt in its own
versioned run file. `utils/ledger.py` takes those runs to the shared experiments
database, so what one package learns about a criterion is there for the next
one. Read [Tuning a question set](#tuning-a-question-set) and
[Pooling what every task has scored](#pooling-what-every-task-has-scored) below
only if you need them.

[Laya](https://huggingface.co/convaiinnovations/laya) is a decision model, not a
chat model. It does not generate tokens: a single bidirectional forward pass
turns *text + questions* into probabilities over the answers you defined. There
is no JSON to parse back and nothing to hallucinate, and it runs locally in
about 13 ms per question. This module drives the MLX FP16 conversion,
[`aac6fef/laya-mlx`](https://huggingface.co/aac6fef/laya-mlx), through the
`laya_mlx` package.

| Question | Built with | Answer |
|---|---|---|
| Yes/no | `laya.yes_no(instructions)` | P(true), `0.0`–`1.0` |
| Multiple choice | `laya.choice(instructions, options)` | the winning label |
| Rubric score | `laya.score(instructions, levels)` | expected level, `0.0`–`len(levels) - 1` |

## Using it

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import laya

questions = {
    "refund": laya.yes_no("Does the customer ask for money back?"),
    "team": laya.choice("Who should handle this?", {
        "billing": "invoices, payments, refunds",
        "technical": "bugs and outages",
    }),
    "urgency": laya.score("How urgent is this?", ["not urgent", "soon", "critical"]),
}

agent = laya.load()
for chunk, result in laya.ask_chunks(agent, text, questions):
    print(laya.answers(result))          # {'refund': 0.93, 'team': 'billing', ...}
    print(result["answers"]["team"])     # confidence and per-label probabilities
```

`answers()` gives the verdict alone. The full result keeps `confidence` and
`probabilities` for every question — use them, because a decision model is only
useful when you can tell a 0.95 from a 0.4.

## The 512-token budget is not 512 tokens of text

This is the thing the module exists for. Laya's limit covers the **whole
sequence** — `[CLS] type instructions [SEP] options [SEP] text [SEP]` — and the
text gets only what the question left over. `laya_mlx` fills the sequence and
**silently discards the rest of the text**: no exception, no warning, just a
confident answer about the first part of what you passed and no sign the rest
was never read.

So:

- `laya.state_budget(questions)` returns the tokens actually left for text.
  Every question in a call sees the same text, so the most expensive one sets
  the limit for all of them. The three questions above leave **473**; a choice
  with twenty described options leaves far less.
- `laya.split(text, budget)` cuts text to fit, breaking at paragraphs first and
  sentences second, packing greedily so a short document stays one chunk.
- `laya.ask()` raises `TooLong` rather than letting a chunk be truncated.
  `laya.ask_chunks()` does the budgeting and splitting for you.

Never call `agent.predict()` directly unless you have checked the budget
yourself — that is the one way to get silently wrong answers out of this model.

## Memory over a long run

MLX does not free a buffer when an array dies; it keeps it in a reuse cache.
Chunks are all different lengths, so the buffers rarely match and almost
nothing is reused: asking 32 questions of one 61-segment document left **48.8 GB
of cached buffers** behind, and MLX's own ceiling is the machine's whole
recommended working set — 130 GB on a 137 GB Mac. Nothing errors. The machine
simply runs out of room somewhere in the middle of a corpus.

The module bounds this in three places, and the answers are bit-for-bit the
same either way:

- **`load()` caps the cache** at `laya.CACHE_LIMIT`, 4 GiB. Measured free: 62.6 s
  for that document capped against 62.7 s uncapped, and a 1 GiB cap was no
  slower again. It is a soft ceiling — MLX evicts once it is over, so expect it
  to settle one forward pass above the number. Pass `load(cache=None)` to leave
  MLX alone, `laya.cache_limit(n)` to change it later.
- **`ask_chunks()` releases the buffers every `laya.BATCH` chunks** (20). With
  that in place the cache reads 0 MB at every batch boundary and the working
  set holds at ~1.9 GB peak — weights included — for a document of any length.
- **`ask_chunks()` is a generator.** It yields `(chunk, result)` as each chunk
  is answered instead of returning the document's results in one list. Wrap it
  in `list()` only if you genuinely need them all live at once.

`laya.ask_batches(agent, text, questions, batch=20)` is the same work handed
back 20 pairs at a time, for when a batch is also the caller's unit of work — a
write, a transaction, a progress line. `laya.memory()` returns MLX's `active`,
`cache` and `peak` bytes for a log line.

**Batch size is not the forward pass.** Laya answers one chunk per `predict`
call, and `laya_mlx`'s own `batch_size` is *questions* per pass, not chunks —
between 4 and 32 it moved peak memory by a quarter of a gigabyte and the
running time not at all. `BATCH` buys a bounded cache, not a bigger matmul.

If you call `laya.ask` in your own loop rather than `ask_chunks` — which is
what a fixed-segment sweep should do, see below — the cap from `load()` still
holds, but nothing calls `release()` for you. Call it yourself every so often
if you want the memory back between documents.

## What to know before changing it

- **The boolean type is spelled `noul` on the wire.** Not a typo — it is
  upstream's name, in both the model card and the code. `yes_no()` is a wrapper
  over it, and `answers()` reads the `noul` key out of the result.
- **`state_budget` and `validate` call `laya_mlx.agent.Agent._to_internal`**, a
  private static method, to normalise a question the same way `predict` will.
  Reimplementing that normalisation here would let the budget drift from the
  sequence actually built. `validate()` raises a clear `RuntimeError` if
  upstream renames it; that is the signal to re-check the call, not to write a
  second copy of the rules.
- **Budgeting and splitting do not need the weights.** `checkpoint()` fetches
  only `tokenizer/` and `rl_agent_config.json`, a few kilobytes, so a corpus can
  be planned before anything downloads. Only `load()` pulls the ~850 MB
  checkpoint.
- **Confidence on choices with 11+ options is uncalibrated.** The checkpoint
  ships a temperature of 0.1006 for that bucket, which would report a coin flip
  as near-certainty; `laya_mlx` clamps it and warns at load. Prefer fewer
  options, or treat those confidences as ordering only.
- **Every chunk is answered on its own.** The model sees no context beyond the
  chunk in hand, so a question that depends on the whole document has to be
  aggregated from per-chunk answers by the caller.

## Tuning a question set

`utils/sweep.py`, for the second pass and every one after it. A first question
set is usually wrong in ways you cannot see until you score it, and fixing it
means running the corpus again — which is where three things go wrong: the text
gets copied into every result file, a round that only re-asks two questions
loses the other thirteen, and nothing records which answers came from which
wording.

```python
from utils import sweep

chunks = {sweep.chunk_id(t): {"text": t, "tokens": n} for t, n in texts}
sweep.save_chunks(path, chunks, document="report.pdf")

run = sweep.build_run("v3", asked, answers, metrics=m, previous=prior)
sweep.save_run(runs / "v3.json", run)
```

- **The text lives in one file, keyed by hash.** Runs reference chunks by id;
  `missing_chunks(run, store)` returns any that do not resolve.
- **A run that re-asks part of the set carries the rest forward.** Questions
  absent from `answers` are copied from `previous` with `last_run` set to the
  version that actually asked them, so every run file describes the whole set
  while a round costs only what it changed.
- **`binary_scores(truth, verdicts)`** gives recall, specificity, agreement and
  Youden's J against an oracle you supply. **Optimise J.** Agreement flatters a
  question that never fires; mean-probability separation flatters one that
  fires at everything. Both mistakes are easy to make and J catches them.
- **`convergence(history, min_gain=0.02, patience=2)`** says, per question, its
  best version and whether iterating has stopped paying. A later round only
  takes the crown by beating the standing best by more than `min_gain` — plain
  argmax lets a rounding wobble reset the clock and nothing ever converges.

**Segment to the full set's budget, not the subset's.** A round asking two of
fifteen questions leaves far more room for text, so `ask_chunks` would re-split
the document and every chunk hash would change — after which no run can be
compared with any other. Split once with `state_budget(full_set)`, then use
`laya.ask` on those fixed segments; any subset is cheaper and still fits.

What it will not do: choose your oracle, or tell you a question is good. It
stores, joins and scores; the judgement stays with you.

## Pooling what every task has scored

`utils/ledger.py`, the bridge to the `postgres-experiments-state` skill. A run
file answers "did *my* last round pay?". It cannot answer the question the
second package asks — *has anyone already tried this criterion, and did
iterating on it still pay?* — because run files live in whichever package wrote
them. The experiments database is shared, so that is where the answer goes.

Two calls, one per direction:

```python
from utils import ledger

run = sweep.build_run("v3", asked, answers, metrics=m, previous=prior)
sweep.save_run(runs / "v3.json", run)

with experiments.connect() as conn:
    experiments.ensure_schema(conn)
    ledger.publish(conn, run, task="pdf-qa/iterate.py")   # after each iteration

    standing = ledger.standing(conn)                      # before the next one
    todo = ledger.worth_improving(standing)               # ['is_draft', 'topic']
```

- **`publish` writes one history entry per criterion the run actually asked.**
  Not the ones it carried forward — those were not executed this round, and
  writing them would invent runs that never happened and tilt every track
  record built on them. Each entry carries the metrics, the wording that scored
  them, and the answers behind it, so a later reader can see *which version of
  the question* earned a number rather than just the number.
- **`standing` pools every task's entries**, in time order, and runs
  `convergence` over the combined sequence. Versions are labelled
  `task:version`, because two packages both calling a round `v3` are two
  different rounds. It reports `runs`, `tasks`, `best`, `latest`,
  `rounds_since_best` and `converged` per criterion, and `min_gain` and
  `patience` pass straight through — same defaults, same argument to have.
- **Unmeasured is not the same as finished.** A criterion nobody has scored on
  the metric you asked for comes back `converged: False` with `best: None`.
  Which of the two to act on is yours to decide; `worth_improving` simply
  returns everything not converged.

`metric` defaults to `"J"`, which is what `binary_scores` calls Youden's J.
Pass `metric=` for anything else you put in a run's metrics.

This is the only part of this skill that needs a database. `ledger` imports
`experiments` lazily, so with the `postgres-experiments-state` skill disabled
everything else here still works and only these calls raise.

## Testing it

```
/path/to/python skills/laya-decisions/<version>/test/test_laya.py
/path/to/python skills/laya-decisions/<version>/test/test_sweep.py
/path/to/python skills/laya-decisions/<version>/test/test_ledger.py
```

`test_laya.py` checks the builders, the validation failures, the budget, the
splitter and a real `predict` against `AGENTS.common.md` — and asserts that
upstream really does truncate, so the claim above stays honest. It also covers
the batching: that `ask_batches` cuts where it says it does, that batching
changes no answer, that `release()` empties the cache and that MLX settles near
a 256 MB cap instead of climbing past it. `test_sweep.py`
covers hashing, carry-forward across two hops, dangling foreign keys, the
degenerate scoring cases and the convergence rule; it needs no model and runs in
under a second. `test_ledger.py` needs a Postgres and the
`postgres-experiments-state` skill, and works in a throwaway
`ledger_selftest` schema it drops on the way out: it asserts that a
carried-forward question is not republished, that a malformed question takes
the whole iteration down rather than half-publishing it, and that two packages'
runs pool into one convergence sequence. Run all three by path: `test` is a
standard library package and `-m test.test_laya` picks that one instead.

## Requirements

Apple silicon, macOS 14+, Python 3.11+, and `laya-mlx` installed (`mlx` has no
wheels for anything else). This is an English checkpoint with a 512-token
context; `laya_mlx` also ships multilingual and typed-decisions checkpoints with
1,024, and a `Router` that picks between them by script — `load()` takes any of
their ids and the rest of the module reads the limits from whatever it loaded.

`ledger.py` alone adds two: the `postgres-experiments-state` skill and a
reachable Postgres. Nothing else here touches either, and the import is lazy,
so a project that has not enabled that skill loses `ledger` and keeps the rest.

## What it will not do

It does not chunk documents *semantically*, extract text from PDFs, or decide
what a good question is. It takes text and questions and returns numbers,
`sweep.py` stores and scores what comes back, and `ledger.py` shares it. None of
the three will tell you a criterion is good enough — `converged` says iterating
has stopped paying, which is not the same claim. Anything that knows what a
document *is* belongs in the project, not here.
