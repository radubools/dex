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


def epanechnikov_average_fn(points, lam="lam", var="x", digits=4):
    """The Nadaraya-Watson average of y over the points [x, y] with an Epanechnikov kernel of half-width lam, as one formula; NaN where no point is within lam."""
    w = [f"max(0, 1 - (({var} - {_num(x, digits)})/{lam})^2)" for x, _ in points]
    num = " + ".join(f"{_num(y, digits)}*{wi}" for (_, y), wi in zip(points, w))
    return f"({num})/({' + '.join(w)})"


def knn_running_mean(xs, ys, k):
    """The 1-D k-nearest-neighbour running mean of points sorted by x: (edges, levels), the target x where the window moves on and the mean between edges."""
    edges = [(xs[i] + xs[i + k]) / 2 for i in range(len(xs) - k)]
    levels = [sum(ys[i:i + k]) / k for i in range(len(xs) - k + 1)]
    return edges, levels


def stairs_fn(edges, levels, lo, hi, var="x", steep=2000, digits=4):
    """A staircase formula: levels[0], stepping to levels[j+1] at edges[j] (ramp width 1/steep); NaN outside [lo, hi]."""
    parts = [_num(levels[0], digits)]
    for e, a, b in zip(edges, levels, levels[1:]):
        d = round(b - a, digits)
        if d:
            parts.append(f"{'-' if d < 0 else '+'} {_num(abs(d), digits)}*min(max({steep}*({var} - {_num(e, digits)}), 0), 1)")
    return " ".join(parts) + f" + 0*sqrt(({var} - {_num(lo, digits)})*({_num(hi, digits)} - {var}))"
