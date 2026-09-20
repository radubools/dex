---
name: traduceri-bitext
description: The bitext format — the checks a finished translation has to pass, and the viewer that reads it chunk by chunk.
---

# Bitext

A `.bitext.json` is a translation kept as numbered chunks: each source chunk,
the paragraph that replaced it, and any proofreading note.

| Piece | For |
|---|---|
| `utils/bitext_checks.py` | The questions a finished bitext has to pass, as lists of problems |
| `widgets/bitext/` | Reads one as numbered chunks, source above target |

Every check takes data the caller has already loaded and returns a list of
problems — it reads nothing and decides nothing. That is deliberate: a check
that opened files could not be run over a chunk you are still assembling.

The widget fetches no siblings. Everything the reader needs is inside the
`.bitext.json`, because the sidecar *is* the alignment; a viewer re-deriving it
from the original PDF would be asserting an alignment nobody wrote down.

## What it will not do

Getting text out of a document or a wiki page is `traduceri-sources`.
