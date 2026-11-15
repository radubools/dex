"""Checks for game-voice's speech.speakable_code: string lists, URL escapes, XML tags and / read as words."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from utils.speech import code_words, speakable_code  # noqa: E402

CASES = [
    # lists of strings lose their brackets; indexing with a string key is untouched
    ("Try adding the pair ['Jon', 'Kris']: the groups merge.", "Try adding the pair 'Jon', 'Kris': the groups merge."),
    ("Try s = ['a', ' ', 'b'] with n = 4.", "Try s equals 'a', ' ', 'b' with n equals 4."),
    ("such as [['bee', 2], ['cat', 3]].", "such as 'bee', 2, 'cat', 3."),
    ("idx['Johnny'] is 2, in one step.", "idx at 'Johnny' is 2, in one step."),
    ("Try [2, 1, 2, 1].", "Try [2, 1, 2, 1]."),
    # URL escapes
    ("each one is written %20.", "each one is written percent 2 0."),
    ("cat%20in%20hat needs...", "cat percent 2 0 in percent 2 0 hat needs..."),
    # XML tags
    ("Then </person> closes the element.", "Then the closing tag person closes the element."),
    ("Open with <family> first.", "Open with the tag family first."),
    # / between operands
    ("Suppose line 14 were key = dy / dx.", "Suppose line 14 were key equals dy over dx."),
    ("About n / 2 sifts, each slow.", "About n over 2 sifts, each slow."),
    ("F gets in with chance m / (i + 1) = ?", "F gets in with chance m over (i plus 1) equals what?"),
    ("In Python, / always gives a float.", "In Python, / always gives a float."),
    # math is left to the shared reader
    ("Here $$\\frac{a}{b} = 2$$ holds.", "Here a over b equals 2 holds."),
    ("Re-linking the nodes in place costs one spare node.", "Re-linking the nodes in place costs one spare node."),
]


@pytest.mark.parametrize("text,want", CASES)
def test_speakable_code(text, want):
    assert speakable_code(text) == want


def test_code_words_leaves_plain_prose():
    assert code_words("A plain sentence, with no code.") == "A plain sentence, with no code."
