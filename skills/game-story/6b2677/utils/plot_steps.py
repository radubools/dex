"""Step functions of a parameter as one plain plot.function formula, for piecewise-constant paths (tree size against alpha, support counts)."""


def _n(v, digits=4):
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def step_fn(breakpoints, param="x", *, steep=200, digits=4):
    """breakpoints [[p_0, v_0], [p_1, v_1], ...] (p increasing) as a formula in `param`: v_k for p_k <= param < p_{k+1}, each p_k included (ramps 1/steep wide end at p_k)."""
    parts = [_n(breakpoints[0][1], digits)]
    for k in range(1, len(breakpoints)):
        a, d = breakpoints[k][0], breakpoints[k][1] - breakpoints[k - 1][1]
        if abs(d) < 10 ** -digits:
            continue
        parts.append(f"{'-' if d < 0 else '+'} {_n(abs(d), digits)}*min(max({steep}*({param} - {_n(a, digits)}) + 1, 0), 1)")
    return " ".join(parts)
