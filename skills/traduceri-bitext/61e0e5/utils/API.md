## `bitext_checks`

Answer the questions a finished bitext has to pass, as lists of problems.

- `numbering_problems(segments: list[dict]) -> list[str]` — Report whether `n` runs 1..N with no gaps, no repeats and in reading order.
- `source_drift_problems(segments: list[dict], sources: dict[int, str]) -> list[str]` — Report any `source` that no longer matches the extractor's, keyed by segment number.
- `proofread_problems(segments: list[dict]) -> list[str]` — Report any `suggestion` lacking a `note`, or identical to the draft it replaces.
- `untranslated(segments: list[dict]) -> list[int]` — Return the numbers of the segments with neither a `target` nor a `note` explaining why.