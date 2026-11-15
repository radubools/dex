"""Piecewise-linear curves through computed knots as one plain plot.function formula (abs-value hinges), for paths with no closed form."""
import numpy as np


def piecewise_formula(ts, vs, *, digits: int = 4) -> str:
    """The interpolant through knots (ts increasing, vs) as a plain formula in x: v0 + s0 (x - t0) + sum_k c_k (|x - t_k| + x - t_k)."""
    ts, vs = np.asarray(ts, float), np.asarray(vs, float)
    slopes = np.diff(vs) / np.diff(ts)
    f = lambda v: f"{v:.{digits}f}"  # noqa: E731
    terms = [f(vs[0]), f"{slopes[0]:+.{digits}f}*(x - {f(ts[0])})"]
    for k in range(1, len(ts) - 1):
        d = (slopes[k] - slopes[k - 1]) / 2
        if abs(d) > 10 ** -(digits + 2):
            terms.append(f"{d:+.{digits}f}*(|x - {f(ts[k])}| + x - {f(ts[k])})")
    s = " ".join(terms)
    return s.replace("+-", "-").replace("--", "+").replace("- -", "+ ")
