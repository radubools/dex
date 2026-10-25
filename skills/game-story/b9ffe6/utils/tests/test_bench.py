"""Checks for game-story's bench simulator: panels drawn, replaced, hidden, and missed hides."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils import bench  # noqa: E402


def _beat(bid, action=None, **args):
    b = {"id": bid, "say": "x", "wait": "next"}
    if action:
        b["act"] = {"action": action, "args": args}
    return b


def test_panels_drawn_replaced_and_hidden():
    beats = [
        _beat("a", "ui.steps", id="work", title="Sums", lines=["1"]),
        _beat("b", "ui.steps", id="work", lines=["2"], append=True),
        _beat("c", "model.params", title="Line"),
        _beat("d", "regression.fitStep", lr=0.1),
        _beat("e", "ui.hide", id="params"),
        _beat("f", "ui.hide", id="chart:reg-loss"),
        _beat("g", "ui.steps", id="work", title="Hessian", lines=["3"]),
        _beat("h", "model.params"),
    ]
    trace = dict(bench.bench_trace(beats))
    assert trace["b"] == {"steps:work": "Sums"}
    assert trace["d"] == {"steps:work": "Sums", "params": "Line", "chart:reg-loss": None}
    assert trace["f"] == {"steps:work": "Sums"}
    assert trace["h"] == {"steps:work": "Hessian", "params": None}
    assert bench.ui_hide_misses(beats) == []


def test_bare_ids_and_misses():
    beats = [
        _beat("a", "ui.table", id="terms", title="T"),
        _beat("b", "ui.note", text="careful", tone="warn"),
        _beat("c", "ui.hide", id="terms"),          # a bare id reaches table:terms
        _beat("d", "ui.hide", id=["note", "x"]),    # a list hides nothing in the game
        _beat("e", "ui.hide", id="explain"),        # nothing up under it
    ]
    trace = dict(bench.bench_trace(beats))
    assert trace["c"] == {"note": None}
    assert trace["d"] == {"note": None}
    assert bench.ui_hide_misses(beats) == ["d", "e"]


def test_trace_hide_misses_reads_the_simulated_bench():
    # Snapshots as story.simulate returns them: panels by key, charts and meters by bare id.
    trace = [
        {"id": "a", "panels": {"boost-cands": [], "table:data": []}, "charts": {"boost-curve": {}}, "meters": {"boost-mse": {}}},
        {"id": "b", "panels": {"table:data": []}, "charts": {}, "meters": {}},
        {"id": "c", "panels": {"table:data": []}, "charts": {}, "meters": {}},
    ]
    beats = [
        _beat("a", "ada.candidates", round=1),                                    # a panel the static map does not know
        _beat("b", "ui.hide", id=["boost-cands", "chart:boost-curve", "boost-mse"]),
        _beat("c", "ui.hide", id=["data", "explain"]),                            # table:data stayed; explain was never up
    ]
    assert bench.trace_hide_misses(trace, beats) == [("c", "data"), ("c", "explain")]
