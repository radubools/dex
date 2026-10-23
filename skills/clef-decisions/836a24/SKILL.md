---
name: clef-decisions
description: Ask Clef-Flash — Cloudflare's 9B SystemOne decision model, run locally on MLX from a pinned, reviewed snapshot of mlx-community/clef-flash-8bit — typed questions about text (yes/no, choice, score) and get calibrated probabilities back in Jev's shapes; batches every question for a text into one forward pass and runs several model instances on the GPU at once. The local, free replacement for laya-decisions and jev-decisions.
---

# Clef decisions

`utils/clef.py` is the whole skill. Same builders, result shapes, `answers()` and
`scale()` as `jev-decisions`, so code written for Jev ports by changing the import.

```python
from utils import clef

questions = {
    "pending_co": clef.yes_no("Does this passage say a change order is awaiting lender approval?"),
    "who": clef.choice("Who raised the issue?", ["lender", "contractor", "inspector"]),
    "risk": clef.score("How much cost risk does this passage describe?", ["none", "some", "serious"]),
}
result = clef.ask(text, questions)        # one record, one forward pass
clef.answers(result)                      # {'pending_co': 0.97, 'who': 'lender', 'risk': 1.6}

with clef.Pool() as pool:                 # model in a worker process, results as they finish
    for cid, result in pool.ask_each([(id, text), ...], questions):
        ...

clef.estimate(texts, questions)           # tokens and hours before you start

clef.ask_image(png_bytes, questions)      # image input (PNG/JPEG bytes, path or PIL image)
clef.ask_image(png_bytes, questions, text="Site photo, looking north")   # with context
```

## Images

`ask_image` loads the vision backbone through mlx-vlm (`load(vision=True)`, a
separate ~19 GB instance from the text one) and puts the image in the record
beside an optional `text` state. Images are scaled to at most `MAX_IMAGE_SIDE`
(1,280 px) first: image tokens grow with area — 16 px patches merged 2x2, so
1,280 x 960 is ~1,200 tokens. The `edil-corpus` OCR stage indexes picture regions
in `corpus.images`; `criteria-loop`'s `image_sweep` asks criteria about them.

## What it costs — measure before sweeping

Everything is tokens. A record is `system + STATE + one schema block per
question + suffix`, and **each yes/no question adds ~105 tokens** whatever the
text's length. A 400-token chunk asked 30 questions is ~3,400 tokens.

Measured on this M4 Max (40-core GPU, 128 GB), steady state over 40 s,
3,000-token records:

| Setup | Tokens/s, total |
|---|---|
| **one instance, dequantized to bf16 (`load()` default; 17 GB)** | **857** |
| one instance, 8-bit weights (11 GB) | 654 |
| two / three / four bf16 instances at once | 681 / 761 / 553 |
| two / three 8-bit instances at once | 624 / 544 |
| batching 2–8 records into one forward pass | no gain |

One bf16 instance is the fastest configuration: the GPU is compute-bound on a
single forward pass, so neither batching nor extra processes add throughput —
they contend. `Pool` defaults to one worker; `workers=` exists to re-measure on
other hardware. RAM is not the limit.

The model card's 0.34 s per 1k tokens is an M5 Max figure.

## How many questions per record

The hard limit is `MAX_LENGTH` (16,384 tokens, what the head was trained at):
140 yes/no questions beside a ~400-token chunk is 13,785 tokens and runs in
22.7 s. `ask()` never truncates — `ContextTooLong` instead.

The practical limit is lower, because **Clef decides a record's questions
jointly**. The same 140 criteria asked in one record and in fourteen records of
ten moved P by 0.06 on average and up to 0.45, and flipped one verdict in 140;
one question alone against the same question among 30 moved by under 0.01. So
`ask()` cuts a set into records of at most `MAX_QUESTIONS` (32): an answer then
barely depends on which other criteria shared its record, for ~12% extra spent
repeating the chunk.

**Caching.** There is no cross-chunk cache to exploit: the schema comes *after*
the state, so every chunk pays for every question it is asked. Within one chunk
the `system + STATE` prefix is identical across its records, and a causal model
could reuse it: at 32 questions per record that would save the ~420 prefix
tokens of every record after the first, about 10% on a chunk asked 140
questions. Not implemented yet (it needs the backbone's KV and gated-delta state
cached and copied per record); the larger lever is asking fewer (criterion,
chunk) pairs, which is what the `criteria-loop` skill's probe-and-walk is for.

## Pinned code

The MLX port ships its loader (`clef_mlx.py`) beside the weights; it imports only
`mlx` and the standard library, and was read before use. It is loaded from the
snapshot at `clef.REVISION`; a new upstream revision is not used until someone
reads it and moves the pin.

Needs `mlx-lm` 0.32.x, and `mlx-vlm` 0.7.x for images (both installed in the dex venv; `clef_mlx` warns on other versions).
