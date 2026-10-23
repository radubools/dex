"""Exercise utils/jev.py against the live endpoint. Run it; it prints and asserts.

    /path/to/python skills/jev-decisions/<version>/test/test_jev.py

Run it by path, not `-m test.test_jev`: `test` is a standard library package
and shadows this directory.

Needs `JEV_API_KEY` in the environment and about 60 live requests' worth of
credits. Everything before the "live endpoint" section runs offline, so a run
without a key still checks the builders, validation and the splitter.
"""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "utils"))

import jev  # noqa: E402

# A real document rather than an invented fixture: an OCR'd construction
# analysis pulled from the eDIL export, 100 KB of the noisy prose and tables
# this skill actually gets pointed at.
CORPUS = (
    HERE.parents[3]
    / "assets"
    / "construction"
    / "change-order-risk-criteria-doc-1"
    / "document.txt"
)

QUESTIONS = {
    "change_order": jev.yes_no("Does this passage discuss a change order?"),
    "topic": jev.choice(
        "What is this passage mainly about?",
        {
            "cost": "money, budget, invoices, payment applications",
            "schedule": "dates, delays, milestones, completion",
            "other": "anything else",
        },
    ),
    "specificity": jev.score(
        "How specific is this passage?",
        ["boilerplate", "general description", "exact figures and dates"],
    ),
}


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"  ok  {label}{' — ' + detail if detail else ''}")


print("question specs")
check("yes_no emits the endpoint's type name", QUESTIONS["change_order"]["type"] == "noul")
check(
    "choice keeps its descriptions",
    set(QUESTIONS["topic"]["criteria"]) == {"cost", "schedule", "other"},
)
check("score copies its levels", QUESTIONS["specificity"]["criteria"][0] == "boilerplate")

print("\nvalidation")
jev.validate(QUESTIONS)
print("  ok  a good set passes")
for bad, why in [
    ({}, "empty set"),
    ({"q": {"type": "bool", "instructions": "?"}}, "unknown type"),
    ({"q": {"type": "noul"}}, "no instructions"),
    ({"q": {"type": "choice", "instructions": "?"}}, "choice with no options"),
    ({"q": {"type": "score", "instructions": "?", "criteria": ["only"]}}, "one rubric level"),
    (
        {f"q{i}": {"type": "noul", "instructions": "?"} for i in range(9)},
        "nine questions",
    ),
]:
    try:
        jev.validate(bad)
    except ValueError as error:
        print(f"  ok  rejects {why} — {error}")
    else:
        raise AssertionError(f"validate accepted {why}")

print("\ntoken counting and splitting")
text = CORPUS.read_text(encoding="utf-8", errors="replace")
check("the corpus is real", len(text) > 50_000, f"{len(text)} characters")
chunks = jev.split(text)
sizes = [jev.token_count(chunk) for chunk in chunks]
check("splits into many chunks", len(chunks) > 20, f"{len(chunks)} chunks")
check(
    "every chunk is inside the budget",
    max(sizes) <= jev.CHUNK_TOKENS,
    f"largest {max(sizes)} of {jev.CHUNK_TOKENS}",
)
check(
    "chunks are packed, not dribbled",
    sum(sizes) / len(sizes) > jev.CHUNK_TOKENS * 0.5,
    f"mean {sum(sizes) / len(sizes):.0f} tokens",
)
check("nothing is dropped", " ".join(chunks).split() == text.split())
check("a short text is one chunk", jev.split("one line about a change order") == [
    "one line about a change order"])

print("\nresult helpers, on a canned response")
canned = {
    "answers": {
        "change_order": {"type": "noul", "noul": 0.95},
        "topic": {
            "type": "choice",
            "choice": "cost",
            "probabilities": {"cost": 0.88, "schedule": 0.1, "other": 0.02},
            "confidence": 0.81,
        },
        "specificity": {
            "type": "score",
            "score": 1.5,
            "legend": {"0": "boilerplate", "1": "general description", "2": "exact figures and dates"},
            "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5},
            "confidence": 0.9,
        },
    },
    "usage": {"input_tokens": 400, "output_tokens": 60},
    "credits": 1,
    "elapsed_ms": 420,
}
flat = jev.answers(canned)
check("answers flattens all three types", flat == {
    "change_order": 0.95, "topic": "cost", "specificity": 1.5}, str(flat))
rich = jev.detail(canned)
check("detail adds a distribution to a yes/no",
      rich["change_order"]["probabilities"] == {"false": 0.05, "true": 0.95})
check("detail keeps the choice's own probabilities",
      rich["topic"]["probabilities"]["cost"] == 0.88)
check("scale puts a yes/no through untouched",
      jev.scale(QUESTIONS["change_order"], rich["change_order"]) == 0.95)
check("scale reads a choice's probability",
      jev.scale(QUESTIONS["topic"], rich["topic"]) == 0.88)
check("scale divides a rubric by its top level",
      jev.scale(QUESTIONS["specificity"], rich["specificity"]) == 0.75)
described = jev.describe(QUESTIONS)
check("describe records what was asked",
      [row["id"] for row in described] == ["change_order", "topic", "specificity"])
check("describe carries the options", described[1]["options"]["cost"].startswith("money"))
check("cost totals a run", jev.cost([canned, canned]) == {
    "requests": 2, "credits": 2, "input_tokens": 800, "output_tokens": 120})

if not os.environ.get("JEV_API_KEY"):
    print("\nJEV_API_KEY is not set — stopping before the live endpoint.")
    raise SystemExit(0)

print("\nlive endpoint")
client = jev.client()
one = jev.ask(client, chunks[0], QUESTIONS)
check("ask unwraps the envelope", set(one) == {"answers", "usage", "credits", "elapsed_ms"},
      str(sorted(one)))
check("every question is answered", set(one["answers"]) == set(QUESTIONS))
check("a chunk-sized request is one credit", one["credits"] == 1,
      f"credits={one['credits']} for {one['usage']['input_tokens']} input tokens")
check("usage is reported", one["usage"]["input_tokens"] > 0, str(one["usage"]))

try:
    jev.ask(jev.Client("sk_not_a_real_key"), "hello", QUESTIONS)
except jev.AuthError as error:
    print(f"  ok  a bad key raises AuthError — {error}")
else:
    raise AssertionError("a bad key was accepted")

try:
    jev.ask(client, " ".join(text.split()[:8000]), QUESTIONS)
except jev.TooLong as error:
    print(f"  ok  an over-large state raises TooLong before sending — {error}")
else:
    raise AssertionError("an over-large state was posted")

print("\nchunks, in order and in parallel")
wanted = chunks[:12]
serial_start = time.time()
serial = [pair for pair in jev.ask_each(client, wanted, QUESTIONS, workers=1)]
serial_elapsed = time.time() - serial_start

parallel_start = time.time()
parallel = [pair for pair in jev.ask_each(client, wanted, QUESTIONS, workers=6)]
parallel_elapsed = time.time() - parallel_start

check("order is the document's, whatever the concurrency",
      [chunk for chunk, _ in serial] == wanted == [chunk for chunk, _ in parallel])
check("concurrency is faster",
      parallel_elapsed < serial_elapsed,
      f"{parallel_elapsed:.1f}s at 6 workers against {serial_elapsed:.1f}s at 1")
check("every chunk is one credit", all(r["credits"] == 1 for _, r in parallel),
      str(sorted({r["credits"] for _, r in parallel})))

verdicts = [jev.answers(result) for _, result in parallel]
check("verdicts are the shapes the types promise",
      all(
          isinstance(v["change_order"], float)
          and v["topic"] in QUESTIONS["topic"]["criteria"]
          and 0.0 <= v["specificity"] <= 2.0
          for v in verdicts
      ),
      str(verdicts[0]))

# The endpoint is not deterministic: asking one chunk again moves P(true) by up
# to 0.06 and a rubric score by up to 0.11 — measured over three runs of
# twenty-four chunks, median movement 0.00 — while the chosen label never
# moved. So compare within a tolerance: `==` between two runs fails on most
# chunks and means nothing.
before = [jev.answers(result) for _, result in serial]
drift = max(abs(a["change_order"] - b["change_order"]) for a, b in zip(verdicts, before))
score_drift = max(abs(a["specificity"] - b["specificity"]) for a, b in zip(verdicts, before))
identical = sum(1 for a, b in zip(verdicts, before) if a == b)
print(f"  ..  {identical}/{len(verdicts)} chunks came back bit-identical the second time")
check("labels are stable across runs",
      all(a["topic"] == b["topic"] for a, b in zip(verdicts, before)))
check("numeric verdicts stay within the measured jitter",
      drift <= 0.10 and score_drift <= 0.20,
      f"P(true) moved {drift:.3f}, score moved {score_drift:.3f}")

print("\nask_chunks over a slice of the document")
slice_text = "\n\n".join(chunks[:5])
pairs = list(jev.ask_chunks(client, slice_text, QUESTIONS, workers=4))
check("ask_chunks splits and answers", len(pairs) >= 4, f"{len(pairs)} chunks")
check("ask_chunks yields (chunk, result)",
      all(isinstance(c, str) and "answers" in r for c, r in pairs))
totals = jev.cost(result for _, result in pairs)
print(f"  ..  run cost {totals['credits']} credits over {totals['requests']} requests, "
      f"{totals['input_tokens']} input tokens")
check("cost counts every request", totals["requests"] == len(pairs))

print("\nall checks passed")
