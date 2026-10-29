"""Checks for game-story's camfit: input beats count their form in the card."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils import camfit  # noqa: E402


def test_input_as_choice_counts_the_input_form():
    beat = {"id": "q", "say": "A residual is a point's height above the line.",
            "wait": {"input": {"prompt": "r = y - yhat = ?", "answer": "2", "tolerance": 0.01}}}
    as_choice = camfit.input_as_choice(beat)
    assert as_choice["wait"]["choice"]["prompt"] == "r = y - yhat = ?"
    assert beat["wait"]["input"]["answer"] == "2"          # the original is left alone
    for vp in camfit.VIEWPORTS:
        assert camfit.card_rect(vp, as_choice)[3] > camfit.card_rect(vp, beat)[3]


def test_input_as_choice_passes_other_beats_through():
    for wait in ("next", {"choice": {"prompt": "p", "options": [{"label": "a"}]}}, {"until": "$data.count >= 8"}):
        beat = {"id": "b", "say": "x", "wait": wait}
        assert camfit.input_as_choice(beat) is beat
