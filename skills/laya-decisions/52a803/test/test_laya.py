"""Exercise utils/laya.py against the real checkpoint. Run it; it prints and asserts.

    /path/to/python skills/laya-decisions/<version>/test/test_laya.py

Run it by path, not `-m test.test_laya`: `test` is a standard library package
and shadows this directory.

Needs `laya_mlx` and Apple silicon. The first run downloads the checkpoint
(~850 MB) into the Hugging Face cache; later runs are local.
"""

import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "utils"))

import laya  # noqa: E402

# A real document rather than an invented fixture: this repository's own guide,
# which is long enough to need splitting and is ordinary English prose.
CORPUS = HERE.parents[3] / "AGENTS.common.md"

QUESTIONS = {
    "about_widgets": laya.yes_no("Is this text about building a viewer for a file?"),
    "audience": laya.choice(
        "Who is this text written for?",
        {
            "developer": "someone writing or running code",
            "manager": "someone planning or approving work",
            "end user": "someone using a finished product",
        },
    ),
    "specificity": laya.score(
        "How specific are the instructions?",
        ["vague intentions", "general guidance", "exact commands to run"],
    ),
}


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"  ok  {label}{' — ' + detail if detail else ''}")


print("question specs")
check("yes_no emits upstream's type name", QUESTIONS["about_widgets"]["type"] == "noul")
check("choice keeps its descriptions", set(QUESTIONS["audience"]["criteria"]) == {
    "developer", "manager", "end user"})
check("score copies its levels", QUESTIONS["specificity"]["criteria"][0] == "vague intentions")

print("\nvalidation")
laya.validate(QUESTIONS)
print("  ok  a good set passes")
for bad, why in [
    ({"q": {"type": "bool", "instructions": "?"}}, "unknown type"),
    ({"q": {"type": "noul"}}, "no instructions"),
    ({"q": {"type": "choice", "instructions": "?", "criteria": ["a", "a"]}}, "duplicate labels"),
    ({"q": {"type": "score", "instructions": "?", "criteria": []}}, "empty rubric"),
]:
    try:
        laya.validate(bad)
    except ValueError as exc:
        print(f"  ok  rejects {why} — {exc}")
    else:
        raise AssertionError(f"accepted {why}")

print("\ntoken budget")
budget = laya.state_budget(QUESTIONS)
print(f"  budget for these three questions: {budget} tokens of text")
check("the budget is well under 512", budget < 512, f"{budget}")
check("the budget leaves usable room", budget > 200, f"{budget}")

# The point of the whole module: prove laya_mlx truncates rather than complains.
from laya_mlx.agent import Agent  # noqa: E402
from laya_mlx.common import build_sequence  # noqa: E402

tok, cfg = laya.checkpoint()
one = {"audience": QUESTIONS["audience"]}
overlong = "word " * 2000
ids, _ = build_sequence(tok, overlong, Agent._to_internal(one["audience"]), 512, 192)
check(
    "upstream silently truncates an over-long state",
    len(ids) == 512 and laya.token_count(overlong) > 512,
    f"{laya.token_count(overlong)} tokens in, {len(ids)} out, no error raised",
)

print("\nsplitting a real document")
text = CORPUS.read_text(encoding="utf-8")
total = laya.token_count(text)
chunks = laya.split(text, budget)
print(f"  {CORPUS.name}: {total} tokens -> {len(chunks)} chunks")
check("splitting was needed", total > budget, f"{total} > {budget}")
check("every chunk fits", all(laya.token_count(c) <= budget for c in chunks),
      f"max {max(laya.token_count(c) for c in chunks)}")
check("nothing was dropped", sum(len(c.split()) for c in chunks) == len(text.split()),
      f"{sum(len(c.split()) for c in chunks)} words of {len(text.split())}")
check("short text stays whole", len(laya.split("One short sentence.", budget)) == 1)

print("\nloading the checkpoint")
agent = laya.load()
print(f"  loaded {agent.model_id}")

print("\nasking one chunk")
result = laya.ask(agent, chunks[0], QUESTIONS)
flat = laya.answers(result)
print(f"  answers: {flat}")
print(f"  usage:   {result['usage']}")
check("every question is answered", set(flat) == set(QUESTIONS))
check("yes_no gives a probability", 0.0 <= flat["about_widgets"] <= 1.0, str(flat["about_widgets"]))
check("choice gives one of the labels", flat["audience"] in QUESTIONS["audience"]["criteria"])
check("score is continuous in range", 0.0 <= flat["specificity"] <= 2.0, str(flat["specificity"]))
check("the sequence never exceeded 512",
      result["usage"]["input_tokens"] <= 512 * len(QUESTIONS),
      str(result["usage"]["input_tokens"]))

print("\nrefusing to truncate")
try:
    laya.ask(agent, text, QUESTIONS)
except laya.TooLong as exc:
    print(f"  ok  ask() raised TooLong — {exc}")
else:
    raise AssertionError("ask() accepted a document that would have been truncated")

print("\nasking every chunk")
stream = laya.ask_chunks(agent, text, QUESTIONS)
check("ask_chunks streams instead of building the whole list",
      isinstance(stream, types.GeneratorType))
pairs = list(stream)
check("one result per chunk", len(pairs) == len(chunks), f"{len(pairs)} pairs")
for chunk, res in pairs:
    verdict = laya.answers(res)
    print(f"  {verdict['audience']:<10} specificity={verdict['specificity']:.2f} "
          f"viewer={verdict['about_widgets']:.2f}  {chunk[:44]!r}")

print("\nbatching")
groups = list(laya.ask_batches(agent, text, QUESTIONS, batch=5))
sizes = [len(g) for g in groups]
print(f"  {len(chunks)} chunks in batches of 5: {sizes}")
check("no batch is bigger than asked for", max(sizes) <= 5, str(sizes))
check("only the last batch is short", all(n == 5 for n in sizes[:-1]), str(sizes))
check("the batches are the chunks, in order",
      [c for g in groups for c, _ in g] == chunks)
check("batching changes no answer",
      [laya.answers(r) for g in groups for _, r in g] == [laya.answers(r) for _, r in pairs])
check("one batch holds the whole document",
      len(list(laya.ask_batches(agent, chunks[0], QUESTIONS, batch=laya.BATCH))) == 1)
for bad in (0, -1, 2.5, True):
    try:
        next(laya.ask_batches(agent, text, QUESTIONS, batch=bad))
    except ValueError as exc:
        print(f"  ok  rejects batch={bad!r} — {exc}")
    else:
        raise AssertionError(f"accepted batch={bad!r}")

print("\nbounding memory")
print(f"  after a full document: "
      + "  ".join(f"{k}={v / 2**20:.0f}MB" for k, v in laya.memory().items()))
check("load() left the cache under its ceiling",
      laya.memory()["cache"] <= laya.CACHE_LIMIT,
      f"{laya.memory()['cache'] / 2**20:.0f}MB of {laya.CACHE_LIMIT / 2**20:.0f}MB")
laya.release()
check("release() hands the buffers back",
      laya.memory()["cache"] == 0, f"{laya.memory()['cache']} bytes still cached")

tight = 256 * 2**20
previous = laya.cache_limit(tight)
check("cache_limit reports the ceiling it replaced",
      previous == laya.CACHE_LIMIT, f"{previous} bytes")
for chunk in chunks[:8]:
    laya.ask(agent, chunk, QUESTIONS)
held = laya.memory()["cache"]
print(f"  eight more chunks under a {tight / 2**20:.0f}MB cap: cache={held / 2**20:.0f}MB")
# A soft ceiling: MLX evicts once it is over, so it settles about one forward
# pass's buffers above the cap. Uncapped, eight chunks hold some 8 GB.
check("MLX settles near the cap", held <= tight * 1.25, f"{held / 2**20:.0f}MB")
laya.cache_limit(previous)

print("\nall checks passed")
