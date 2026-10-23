"""Checks for the coverage rule's judge — synthetic profiles, no database, no model.

    /Users/raduparadovschi/dex/.venv/bin/python skills/criteria-loop/<version>/test/test_coverage.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import coverage  # noqa: E402


def make(rows: dict[int, np.ndarray], docs: int = 8) -> dict:
    out = {}
    for i, (cid, ps) in enumerate(rows.items()):
        out[cid] = {"criterion_id": cid, "decided": len(ps), "coverage": 0.75, "documents": docs,
                    "hits": int((ps >= 0.5).sum()), "hit_documents": docs,
                    "mean_p": float(ps.mean()), "std_p": float(ps.std()),
                    "uncertain_share": float(((ps > 0.2) & (ps < 0.8)).mean()),
                    "mean_confidence": float((np.abs(ps - 0.5) * 2).mean()), "_row": i}
    out["_matrix"] = np.stack(list(rows.values())).astype(np.float32)
    return out


def test_rule():
    rng = np.random.default_rng(0)
    base = (rng.random(400) < 0.2).astype(float) * 0.97 + 0.01
    rows = {
        1: base,                                           # kept
        2: np.where(rng.random(400) < 0.02, 0.6, base),    # near-copy of 1 -> correlated
        3: rng.uniform(0.3, 0.7, 400),                     # non-confident
        4: np.full(400, 0.02),                             # low variance
        5: (rng.random(400) < 0.3).astype(float) * 0.98 + 0.01,  # independent -> kept
    }
    v = {x["criterion_id"]: x for x in coverage.judge(make(rows))}
    assert v[1]["verdict"] == "kept" and v[5]["verdict"] == "kept"
    assert v[2]["reason"].startswith("correlated") and v[2]["corr_with"] == 1
    assert v[3]["reason"].startswith("non-confident")
    assert v[4]["reason"].startswith("low variance")


def test_pending():
    p = make({1: np.array([0.9, 0.1] * 50)}, docs=3)
    assert coverage.judge(p)[0]["verdict"] == "pending"


if __name__ == "__main__":
    test_rule(); print("ok test_rule")
    test_pending(); print("ok test_pending")
