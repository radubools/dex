"""Checks for game-voice's speech.speakable: each family of reading voice.speakable gets wrong."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from utils.speech import speakable  # noqa: E402

CASES = [
    # brackets: functions, products, bracketed powers
    ("$$f(x)$$", "f of x"),
    ("$$p = \\sigma(z)$$", "p equals sigma of z"),
    ("$$F(n) = F(n-1) + F(n-2)$$", "F of n equals F of n minus 1 plus F of n minus 2"),
    ("$$\\frac{n(n-1)}{2}$$", "n times the quantity n minus 1, all over 2"),
    ("$$(1.5 - 2)^2 = 0.25$$", "the quantity 1.5 minus 2, squared, equals 0.25"),
    ("$$G = 1 - p^2 - (1-p)^2$$", "G equals 1 minus p squared minus the quantity 1 minus p, squared"),
    ("$$w(n) = w(n-1) + 1$$", "w of n equals w of n minus 1 plus 1"),
    ("$$a(x - 1)$$", "a times the quantity x minus 1"),
    ("$$\\hat y(1 - \\hat y)$$", "y hat times the quantity 1 minus y hat"),
    ("$$d_1(q, p_0) = ?$$", "d 1 of q, p 0 equals?"),
    ("$$P(c \\mid x)$$", "P of c given x"),
    ("$$p(1) = 0.73$$", "p of 1 equals 0.73"),
    ("$$O(\\max(m, n))$$", "O of the max of m, n"),
    ("$$O(n + m)$$", "O of n plus m"),
    # limits, logs, norms, mod, binomials, factorial
    ("$$\\sum_{i=1}^{n} x_i$$", "the sum from i equals 1 to n of x i"),
    ("$$\\sum_i w_i$$", "the sum over i of w i"),
    ("$$\\min_{c \\le a} b$$", "the min over c is at most a of b"),
    ("$$n \\log_2 n$$", "n log base 2 of n"),
    ("$$\\lVert w \\rVert = 1$$", "the norm of w equals 1"),
    ("$$h = (5 \\cdot 10 + 2) \\bmod 13$$", "h equals 5 times 10 plus 2 mod 13"),
    ("$$\\binom{4}{2}$$", "4 choose 2"),
    ("$$n! = 6$$", "n factorial equals 6"),
    # the game's Unicode minus in filled values
    ("Right: $$\\delta_o h_1 = −0.418$$.", "Right: delta o h 1 equals minus 0.418."),
    # code in plain text
    ("Line 7: if arr[lo] <= arr[mid], the left half is in order.",
     "Line 7: if arr at lo less than or equal to arr at mid, the left half is in order."),
    ("What is dp[i][j]?", "What is dp at i, j?"),
    ("count[3] after line 4 = ?", "count at 3 after line 4 equals what?"),
    ("At i = 5, h == want, but the window is b b a.", "At i equals 5, h equals want, but the window is b b a."),
    ("The body of remove_dups is blank.", "The body of remove dups is blank."),
    ("ways(1) calls ways(-1), then -3.", "ways of 1 calls ways of minus 1, then minus 3."),
    ("-1 is not a height, so lh and rh can't be compared.", "minus 1 is not a height, so lh and rh can't be compared."),
    ("-low[0] is the largest value in the low half.", "minus low at 0 is the largest value in the low half."),
    ("we need 3 - 3 = 0.", "we need 3 minus 3 equals 0."),
    ("dp[i][j] = up + 1", "dp at i, j equals up plus 1"),
    ("len(height) - 1 is the last index.", "len of height minus 1 is the last index."),
    ("Then + would join the texts: \"3\" + \"4\" is \"34\".", "Then plus would join the texts: \"3\" plus \"4\" is \"34\"."),
    ("no rule that * goes before +: the order", "no rule that times goes before plus: the order"),
    ("Every reading carried the same +2.", "Every reading carried the same plus 2."),
    ("mins[-1] is the top.", "mins at minus 1 is the top."),
    ("its lowest value $$L^* = 0.10$$", "its lowest value L star equals 0.10"),
    ("The cross marks ■'s mean.", "The cross marks cube's mean."),
    ("Try a = [] so A starts right at the shared tail.", "Try a equals an empty list so A starts right at the shared tail."),
    ("line 2 were count = {}?", "line 2 were count equals an empty dict?"),
    ("The stack now holds { and [, still waiting.", "The stack now holds a curly brace and a square bracket, still waiting."),
    ("Line 20 adds left[3:], line 21 adds right[:2] and mid[1:4].",
     "Line 20 adds left from 3 on, line 21 adds right up to 2 and mid from 1 to 4."),
    ("Try [2, 1, 2, 1].", "Try [2, 1, 2, 1]."),
    # shapes, buttons and marks
    ("Slide k: ■ up to k = 4, ● from 5 on.", "Slide k: cube up to k equals 4, sphere from 5 on."),
    ("Press ▶ Step, then ⏩ Run or 🎲 New ball.", "Press Step, then Run or New ball."),
    ("Read the ✓ and ✗ marks.", "Read the tick and cross marks."),
    # prose left alone
    ("Re-linking the nodes in place costs one spare node.", "Re-linking the nodes in place costs one spare node."),
    ("Fresh accuracy 73%. Next, prune it (gently).", "Fresh accuracy 73 percent. Next, prune it (gently)."),
    ("the bag(s) of samples", "the bag(s) of samples"),
]


@pytest.mark.parametrize("text,want", CASES)
def test_speakable(text, want):
    assert speakable(text) == want
