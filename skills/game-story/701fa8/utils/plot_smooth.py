"""Local averages of data points for the llm-math plot board: band (moving) and Gaussian-kernel averages as one plot.function formula."""


def band_indicator_fn(xi, h="h", var="x", steep=40):
    """A steep 0-1 ramp formula: 1 when |var - xi| < h, 0 beyond, 0.5 exactly at the edge (ramp width 1/steep)."""
    return f"min(1, max(0, {steep}*({h} - |{var} - {xi}|) + 0.5))"


def band_average_fn(points, h="h", var="x", steep=40):
    """The formula averaging y over the points [x, y] within h of var: NaN (a gap on the board) where none is near."""
    num = " + ".join(f"{y}*{band_indicator_fn(x, h, var, steep)}" for x, y in points)
    den = " + ".join(band_indicator_fn(x, h, var, steep) for x, _ in points)
    return f"({num})/({den})"


def _num(v, digits):
    t = f"{float(v):.{digits}f}".rstrip("0").rstrip(".")
    return "0" if t in ("-0", "") else t


def gaussian_average_fn(points, lam="lam", var="x", digits=4):
    """The Nadaraya-Watson average of the points [x, y] with a Gaussian kernel of sd lam, repeated x merged into one weighted term, terms in increasing x."""
    groups = {}
    for x, y in points:
        key = _num(x, digits)
        n, s = groups.get(key, (0, 0.0))
        groups[key] = (n + 1, s + float(y))
    groups = dict(sorted(groups.items(), key=lambda kv: float(kv[0])))
    k = lambda xi: f"exp(-(({var} - {xi})/{lam})^2/2)"  # noqa: E731
    num = " + ".join(f"{_num(s, digits)}*{k(xi)}" for xi, (n, s) in groups.items() if _num(s, digits) != "0") or "0"
    den = " + ".join(f"{n}*{k(xi)}" for xi, (n, s) in groups.items())
    return f"({num})/({den})"
