---
name: jev-decisions
description: Ask the hosted Jev API typed questions about a piece of text — yes/no, multiple choice, or a continuous rubric score — and get calibrated probabilities back, with the text split into 512-token chunks and the requests retried, batched eight questions at a time and run in parallel.
---

# Jev decisions

One module. `utils/jev.py` puts typed questions to a piece of text and gets
numbers back. [Jev](https://thejevai.com/docs) is a *System One* model, not a
chat model: one request turns *text + questions* into probabilities over the
answers you defined. It generates no prose, so there is no JSON to parse back
and nothing to hallucinate.

This is the network sibling of the `laya-decisions` skill. The builders,
`split`, `answers`, `detail`, `describe` and `scale` have the same names and
the same behaviour, and `jev.split(text)` at 512 tokens cuts a document into
exactly the same chunks as `laya.split(text, 512)` — verified, identical token
counts on this project's reports — so a Jev run and a Laya run over one
document line up chunk-for-chunk. What differs is everything the network
imposes, and that is most of this file.

| Question | Built with | Answer |
|---|---|---|
| Yes/no | `jev.yes_no(instructions)` | P(true), `0.0`–`1.0` |
| Multiple choice | `jev.choice(instructions, options)` | the winning label |
| Rubric score | `jev.score(instructions, levels)` | expected level, `0.0`–`len(levels) - 1` |

## Using it

The key comes from `JEV_API_KEY` in the environment. Never put it in a file you
write, never print it, never pass it on a command line.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import jev

questions = {
    "refund": jev.yes_no("Does the customer ask for money back?"),
    "team": jev.choice("Who should handle this?", {
        "billing": "invoices, payments, refunds",
        "technical": "bugs and outages",
    }),
    "urgency": jev.score("How urgent is this?", ["not urgent", "soon", "critical"]),
}

client = jev.client()                    # raises AuthError if JEV_API_KEY is unset
for chunk, result in jev.ask_chunks(client, text, questions):
    print(jev.answers(result))           # {'refund': 0.93, 'team': 'billing', ...}
    print(result["answers"]["team"])     # confidence and per-label probabilities
    print(result["credits"], result["usage"])
```

`answers()` gives the verdict alone. The full result keeps `confidence` and
`probabilities` for every question — use them, because a decision model is only
useful when you can tell a 0.95 from a 0.4. `detail()` adds the missing
distribution to a yes/no; `scale()` puts all three types on one 0–1 axis so a
mixed set can be thresholded or sorted.

Already have chunks — a Laya package's `chunks.json`, a page split, one row per
file? Use `jev.ask_each(client, chunks, questions)` and skip the splitter.

## Eight questions per request, and that is the cheapest unit

The endpoint answers `400 Questions must contain 1-8 items.` at nine.
`jev.validate()` catches that locally, along with the malformed sets the
endpoint reports only as `502 The decision provider rejected the request.` —
a choice with no `criteria`, an unknown model, a missing state all give that
same useless message.

**Ask the whole set about each chunk in one request.** Credits are charged per
request and grow with its size, not with the number of questions: one question
about a chunk and eight questions about the same chunk both cost **one credit**.
A set of more than eight questions has to be split into groups asked about the
same chunk, and each group is another credit — so a 16-question sweep costs
exactly twice a 8-question one.

`jev.cost(results)` totals `requests`, `credits`, `input_tokens` and
`output_tokens` over a run. Record it: it is the only thing that tells you what
a sweep cost.

## 512-token chunks are a policy, not a limit

The endpoint does **not** truncate: it read a 9,382-token state without
complaint, and reported every token. What it does instead is dilute. One fact
placed at the end of a state scored P(true) `0.97`, `0.91`, `0.77`, `0.63`
under 50, 400, 1,500 and 3,000 words of filler ahead of it — the same
sentence, the same question, an answer sliding toward *no* as the passage
around it grew.

So `jev.split(text)` cuts to `jev.CHUNK_TOKENS` (512) for three reasons: the
answer stays about a passage the model attends to, every request stays inside
one credit (512 tokens of text plus the ~280-token prefix is well under the
~2,000 tokens where a second credit starts), and the chunks match the Laya
skill's.

Two things about the count:

- `jev.token_count()` uses plain ModernBERT, the same vocabulary
  `laya-decisions` counts with — a 2 MB tokenizer download, no key, no weights.
  Jev's own tokenizer is not published, and on this project's OCR'd reports the
  endpoint's `usage.input_tokens` ran **7–23% above** this count. Since nothing
  is truncated that gap costs nothing; treat the budget as approximate.
- `jev.ask()` refuses a body over `MAX_BODY` (30,000 bytes) with `TooLong`
  rather than letting it come back `413 The playground request is too large.`
  The real wall is between 31 KB and 38 KB of state.

## The four things the docs do not tell you

1. **Send a `User-Agent`.** Without one the CDN answers `403` with
   `error code: 1010` — before the API sees the request, with no mention of the
   key. `jev.py` always sends one; if you post the JSON yourself, do too.
2. **The response is wrapped.** The documented `{"model", "answers", "usage"}`
   is really `{"code", "message", "data": {"result": {...}, "creditsUsed": n}}`.
   There is no `model` in the result and `elapsed` is `elapsedMs`. `jev.ask()`
   returns the unwrapped `{"answers", "usage", "credits", "elapsed_ms"}`; if
   you go around it, unwrap `data.result` yourself.
3. **Errors are HTTP statuses with a `message`.** `401 Invalid API key.`,
   `400 Questions must contain 1-8 items.`, `413 The playground request is too
   large.`, `502 The decision provider rejected the request.` — all of them
   reproducible, none of them worth retrying. `jev.py` retries `429` and `529`
   only, four attempts with exponential backoff, and raises `AuthError`,
   `InvalidRequest`, `RateLimited` or `Overloaded` otherwise.
4. **The answers are not deterministic.** Ask the same chunk again and P(true)
   moves by up to **0.06**, a rubric score by up to **0.11** (median movement
   0.00 over three runs of twenty-four chunks); the chosen label did not move
   once. Store each run's numbers, compare them with a tolerance, and never
   assert `run_a == run_b` — that fails on most chunks and means nothing.

## Run it in parallel

One request is about 0.55 s, almost all of it network, and the endpoint took
sixteen at once without a `429`. So `ask_chunks` and `ask_each` keep
`jev.WORKERS` (8) requests in flight and still **yield in document order**:
twelve chunks took 6.3 s serially and 1.6 s at six workers, measured, with the
same answers.

Both are generators over a bounded window — only `workers` requests are ever
outstanding — so write each result out as it arrives rather than collecting the
document. Wrap in `list()` only when the whole document's results genuinely
have to be live at once.

If you do hit sustained `429`s, lower `workers` rather than raising `RETRIES`:
backing off eight threads at once just moves the queue.

## Jev or Laya?

Both answer the same questions in the same shapes, so the choice is operational:

- **Jev** costs credits and a network round trip, needs no Apple silicon and no
  850 MB download, and is not deterministic. It reads a much larger state than
  512 tokens if you ask it to.
- **Laya** is free, local, deterministic and hard-limited to 512 tokens
  *including* the question's own prefix — where it silently truncates.

For a calibration loop that reruns the same questions over the same corpus many
times, Laya's determinism and zero cost win. For a one-pass sweep, or on a
machine without MLX, Jev wins.

## Testing a change

`test/test_jev.py` runs against the live endpoint and a real 100 KB OCR'd
construction analysis from this project, not an invented fixture:

```
/Users/raduparadovschi/dex/.venv/bin/python3 skills/jev-decisions/<version>/test/test_jev.py
```

Run it by path, not `-m test.test_jev`: `test` is a standard library package and
shadows the directory. Everything up to the "live endpoint" section — builders,
validation, splitting, the result helpers — runs with no key, so a change to
any of those can be checked for free. The live half spends about 60 credits.
