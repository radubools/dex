"""Unit balls |x1|^p + |x2|^p = 1 for the llm-math plot board: the plot.function acts and the equal-scale window that draw one."""

# The plot board is 9 wide and 4 tall (BOARD in model-workshop's
# src/actions/plot.js). This window keeps one unit the same length both ways,
# so a circle is round, and with the board's 64 samples it puts samples
# exactly at x = 0 and x = ±1: a ball's corners and tips.
BALL_X = [-3.2, 3.2]
BALL_Y = [-1.4222, 1.4222]

# The two halves of the edge. "+ 0 sqrt(1 - |x|)" is 0 inside the ball and
# undefined outside, so the curve stops at x = ±1 for every p; for p = 1 or
# p = 1/2 the power alone stays defined outside and would draw past the tips.
BALL_TOP = "(1 - |x|^p)^(1/p) + 0 sqrt(1 - |x|)"
BALL_BOTTOM = "-(1 - |x|^p)^(1/p) - 0 sqrt(1 - |x|)"


def p_slider(value, *, lo=0.25, hi=12, step=0.05, label="$$p$$"):
    """A plot.function parameter spec for p: its value and the slider's range."""
    return {"value": value, "min": lo, "max": hi, "step": step, "label": label}


def unit_ball_acts(p, *, ids=("top", "bottom"), color="class-0", label="$$\\|x\\|_p = 1$$",
                   sliders=True, clear=False, x_label="$$x_1$$", y_label="$$x_2$$"):
    """The two plot.function acts drawing the l_p unit ball (p a number, or a p_slider spec when sliders)."""
    top, bottom = ids
    if sliders:
        params = {"p": p if isinstance(p, dict) else p_slider(p)}
    else:
        params = {"p": p["value"] if isinstance(p, dict) else p}
    common = {"x": BALL_X, "y": BALL_Y, "color": color}
    head = {"id": top, "fn": BALL_TOP, "params": params}
    if label is not None:
        head["label"] = label
    a = {"action": "plot.function", "args": {**head, "xLabel": x_label, "yLabel": y_label, "clear": clear, **common}}
    b ={"action": "plot.function", "args": {"id": bottom, "fn": BALL_BOTTOM, "params": params, **common}}
    if sliders:
        a["args"]["sliders"] = ["p"]
        b["args"]["sliders"] = ["p"]
    return [a, b]
