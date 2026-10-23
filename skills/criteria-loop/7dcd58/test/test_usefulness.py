"""Checks for the keep rule and naming — no database, no model.

    /Users/raduparadovschi/dex/.venv/bin/python skills/criteria-loop/<version>/test/test_usefulness.py

The probe/walk/measure queries are exercised end to end by a small round
against `dex_test` (`DEX_CORPUS_DATABASE_URL=postgresql://127.0.0.1/dex_test`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import criteria_store, usefulness  # noqa: E402


def m(**kw):
    base = {"criterion_id": 1, "probes": 300, "probe_hits": 30, "probe_rate": 0.1, "walked": 50,
            "walk_hits": 20, "walk_precision": 0.4, "seeds": 30, "mean_depth": 1.5, "max_depth": 3,
            "mean_reach": 4.0, "hit_documents": 9, "doc_frequency": 0.3, "cross_doc_reach": 2.0}
    return {**base, **kw}


def test_verdicts():
    assert usefulness.verdict(m())["keep"]
    assert usefulness.verdict(m(probe_hits=0, walk_hits=0, probe_rate=0.0))["reason"].startswith("absent")
    assert usefulness.verdict(m(probe_rate=0.6))["reason"].startswith("pervasive")
    assert usefulness.verdict(m(hit_documents=2))["reason"].startswith("rare")
    assert usefulness.verdict(m(walk_precision=0.1))["reason"].startswith("incoherent")
    # Too few walked to judge coherence: kept on the other numbers.
    assert usefulness.verdict(m(walk_precision=0.0, walked=3))["keep"]


def test_slug():
    assert criteria_store.slug("Does this passage say the GMP excludes contingency?") == "the_gmp_excludes_contingency"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
