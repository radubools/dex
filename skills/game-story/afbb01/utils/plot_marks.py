"""Marks for the llm-math plot board as plot.function formulas: upright lines at an x (knots, thresholds) and gaps where a curve jumps."""

# Model Workshop's board samples each curve at this many segments (src/actions/plot.js SAMPLES) and ends a
# curve's run wherever two neighbouring samples differ by more than the window's height.
BOARD_SAMPLES = 64


def _num(v):
    s = f"{v:.6g}"
    return "0" if s in ("-0", "") else s


def vline_fn(x0, x_window, y_window, *, samples=BOARD_SAMPLES, margin=0.85, var="x"):
    """A steep line through x0 that the board draws as an upright mark spanning the y window: slope just under its jump limit."""
    (xa, xb), (ya, yb) = x_window, y_window
    step = (xb - xa) / samples
    slope = margin * (yb - ya) / step
    return f"{_num(slope)}*({var} - {_num(x0)}) + {_num((ya + yb) / 2)}"


def gap_fn(xs, *, half=0.02, var="x"):
    """Terms to add to a formula (" + ..." joined) that make it NaN within `half` of each x in xs, so the board leaves a gap there."""
    return " + ".join(f"0*sqrt(abs({var} - {_num(k)}) - {_num(half)})" for k in xs)
