"""Ellipses of a symmetric 2x2 matrix for the llm-math plot board: the image of the unit circle as two plot.function curves, closed at the tips."""

# The plot board is 9 wide and 4 tall (BOARD in model-workshop's
# src/actions/plot.js). A window 9:4 keeps one unit the same length both ways,
# so a circle is round and (1, 1) points at 45 degrees.
WIDE_X = [-9.45, 9.45]
WIDE_Y = [-4.2, 4.2]
NEAR_X = [-2.7, 2.7]
NEAR_Y = [-1.2, 1.2]


def ellipse_fns(s="s", u="u", window=WIDE_X):
    """(top, bottom) plot.function formulas of Q diag(s, u) Q^T applied to the unit circle, s along (1, 1) and u along (1, -1).

    s and u are formula text: a parameter name ("s"), a number ("1") or an
    expression ("(0.5^t)"). Solving |M^-1 y| = 1 for the second coordinate gives
      y2 = ((s^2 - u^2) y1 +- 2 s u sqrt(R - y1^2)) / (s^2 + u^2),  R = (s^2 + u^2)/2.
    "+ 0 sqrt(R - x^2 + E)" stops the curve about one board sample (window
    width / 64) past the tip, where max(..., 0) makes both halves meet on the
    tip's line, so the ellipse closes for every s, u.
    """
    r = f"({s}^2 + {u}^2)/2"
    e = round(2 * (window[1] - window[0]) / 64, 3)
    mask = f"0 * sqrt({r} - x^2 + {e} * sqrt({r}))"
    root = f"2 * {s} * {u} * sqrt(max({r} - x^2, 0))"
    top = f"(({s}^2 - {u}^2) * x + {root}) / ({s}^2 + {u}^2) + {mask}"
    bottom = f"(({s}^2 - {u}^2) * x - {root}) / ({s}^2 + {u}^2) - {mask}"
    return top, bottom


def ellipse_acts(params, fns, *, ids=("e-top", "e-bot"), window=(WIDE_X, WIDE_Y), color="class-0", label=None,
                 sliders=None, clear=False, x_label="$$x_1$$", y_label="$$x_2$$"):
    """The two plot.function acts drawing one ellipse from ellipse_fns' (top, bottom); both halves share `params`."""
    top, bottom = ids
    wx, wy = window
    head = {"id": top, "fn": fns[0], "params": params, "x": wx, "y": wy, "color": color, "clear": clear,
            "xLabel": x_label, "yLabel": y_label}
    tail = {"id": bottom, "fn": fns[1], "params": params, "x": wx, "y": wy, "color": color}
    if label:
        head["label"] = label
    if sliders:
        head["sliders"] = sliders
        tail["sliders"] = sliders
    return [{"action": "plot.function", "args": head}, {"action": "plot.function", "args": tail}]
