"""Unit hands for the llm-math plot board: the segment from the origin to (cos a, sin a) as a plot.function formula, for vectors that turn."""

# The board samples y = f(x) and draws nothing where f is not finite. A hand is
# y = tan(a) x with x clamped to [0, cos a] (so y never passes sin a), and
# "+ 0 sqrt(...)" leaves it undefined more than MARGIN past either end. With
# plot_balls' window (samples about 0.1 apart) the drawn ends land within half a
# sample of the true ones. An upright hand (cos a = 0) shows as a short slant.
MARGIN = 0.05


def hand_fn(angle, margin=MARGIN):
    """The plot.function formula of the unit hand at `angle` (radians, itself a formula in the curve's parameters, e.g. "0.5 + t*θ")."""
    c = f"cos({angle})"
    return (f"tan({angle})*max(min(x, max(0, {c})), min(0, {c}))"
            f" + 0*sqrt(abs({c})/2 + {margin} - abs(x - {c}/2))")


def segment_fn(angle, length, ox=0, oy=0, margin=MARGIN):
    """The plot.function formula of the segment from (ox, oy) at `angle`, `length` long (angle and length may be formulas in the curve's parameters)."""
    c = f"({length})*cos({angle})"
    return (f"{oy} + tan({angle})*(max(min(x, max({ox}, {ox} + {c})), min({ox}, {ox} + {c})) - {ox})"
            f" + 0*sqrt(abs({c})/2 + {margin} - abs(x - {ox} - {c}/2))")


def bar_fn(height, half, x0=0, margin=MARGIN):
    """The plot.function formula of a horizontal bar at `height` from x0 to x0 + 2*half (`half` may be a formula), e.g. a length that grows with a slider."""
    return f"{height} + 0*sqrt({half} + {margin} - abs(x - {x0} - {half}))"
