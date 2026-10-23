"""Checks for clef-decisions: builders and validation offline, then a live run on the GPU.

    /Users/raduparadovschi/dex/.venv/bin/python skills/clef-decisions/<version>/test/test_clef.py
    ... test_clef.py --offline        # builders only, no model load

The live half loads the model (~17 GB, ~10 s) and asks two short records.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import clef  # noqa: E402


def test_builders():
    assert clef.yes_no("Is it?") == {"type": "noul", "instructions": "Is it?"}
    assert clef.choice("Who?", ["a", "b"])["criteria"] == {"a": "", "b": ""}
    assert clef.score("How?", ["low", "high"])["criteria"] == ["low", "high"]
    for bad in ({"q": {"type": "x"}}, {"q": clef.choice("Who?", ["only"])}, {}):
        try:
            clef.validate(bad)
        except ValueError:
            continue
        raise AssertionError(f"validate accepted {bad}")


def test_live():
    text = ("The general contractor submitted change order #4 for $182,000 for unforeseen rock "
            "excavation; the lender has not yet approved it.")
    qs = {"pending": clef.yes_no("Does this passage say a change order is awaiting approval?"),
          "weather": clef.yes_no("Does this passage say work was delayed by weather?")}
    r = clef.ask(text, qs)
    a = clef.answers(r)
    assert a["pending"] > 0.8 and a["weather"] < 0.2, a
    assert clef.ask(text, qs)["answers"] == r["answers"], "Clef should be deterministic"
    many = {f"q{i}": clef.yes_no(f"Does this passage mention item {i}?") for i in range(40)}
    assert [len(g) for g in clef.groups(text, many)] == [32, 8]
    assert clef.ask(text, many)["usage"]["records"] == 2


if __name__ == "__main__":
    test_builders()
    print("ok test_builders")
    if "--offline" not in sys.argv:
        test_live()
        print("ok test_live")
