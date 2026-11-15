"""Local averages of data points for the llm-math plot board: a band (moving) average as one plot.function formula."""


def band_indicator_fn(xi, h="h", var="x", steep=40):
    """A steep 0-1 ramp formula: 1 when |var - xi| < h, 0 beyond, 0.5 exactly at the edge (ramp width 1/steep)."""
    return f"min(1, max(0, {steep}*({h} - |{var} - {xi}|) + 0.5))"


def band_average_fn(points, h="h", var="x", steep=40):
    """The formula averaging y over the points [x, y] within h of var: NaN (a gap on the board) where none is near."""
    num = " + ".join(f"{y}*{band_indicator_fn(x, h, var, steep)}" for x, y in points)
    den = " + ".join(band_indicator_fn(x, h, var, steep) for x, _ in points)
    return f"({num})/({den})"
