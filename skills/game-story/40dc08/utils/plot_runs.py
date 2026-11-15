"""Simulated runs for the llm-math plot board: a trace of values and a histogram of samples as plot.function formulas."""


def _n(v, digits=4):
    """A number as plain formula text, rounded to `digits` decimals, without trailing zeros."""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def polyline_fn(values, var="k", digits=4):
    """The trace values[0], values[1], ... as a plot.function formula in `var`: straight segments between whole steps 0, 1, 2, ..."""
    parts = [_n(values[0], digits)]
    for i in range(1, len(values)):
        d = round(values[i] - values[i - 1], digits)
        if d:
            parts.append(f"{'-' if d < 0 else '+'} {_n(abs(d), digits)}*min(max({var} - {i - 1}, 0), 1)")
    return " ".join(parts)


def histogram_fn(edges, heights, var="x", steep=1000, digits=4):
    """Bars of `heights` between consecutive `edges` (len(edges) == len(heights) + 1), 0 outside, as a plot.function formula in `var`."""
    hs = [0.0, *heights, 0.0]
    parts = []
    for j, a in enumerate(edges):
        d = round(hs[j + 1] - hs[j], digits)
        if d:
            parts.append(f"{'-' if d < 0 else '+'} {_n(abs(d), digits)}*min(max({steep}*({var} - {_n(a, digits)}), 0), 1)")
    s = " ".join(parts)
    if not s:
        return "0"
    return s[2:] if s.startswith("+ ") else "0 " + s
