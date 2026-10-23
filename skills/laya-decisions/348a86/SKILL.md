---
name: laya-decisions
description: Ask a local model typed questions about a piece of text — yes/no, multiple choice, or a continuous rubric score — and get calibrated probabilities back, with the 512-token budget worked out and the text split to fit.
---

# Laya decisions

One module, `utils/laya.py`, for putting typed questions to a piece of text and
getting numbers back.

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

## Testing it

```
/path/to/python skills/laya-decisions/<version>/test/test_laya.py
```

It checks the builders, the validation failures, the budget, the splitter and a
real `predict` against `AGENTS.common.md` — and asserts that upstream really
does truncate, so the claim above stays honest. Run it by path: `test` is a
standard library package and `-m test.test_laya` picks that one instead.

## Requirements

Apple silicon, macOS 14+, Python 3.11+, and `laya-mlx` installed (`mlx` has no
wheels for anything else). This is an English checkpoint with a 512-token
context; `laya_mlx` also ships multilingual and typed-decisions checkpoints with
1,024, and a `Router` that picks between them by script — `load()` takes any of
their ids and the rest of the module reads the limits from whatever it loaded.

## What it will not do

It does not chunk documents *semantically*, extract text from PDFs, or decide
what a good question is. It takes text and questions and returns numbers.
Anything that knows what a document *is* belongs in the project, not here.
