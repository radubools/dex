"""Exact B-splines by the Cox-de Boor recursion as piecewise polynomials, and any piecewise polynomial as one llm-math plot.function formula.

A polynomial is a list of Fractions, lowest power first; a piecewise polynomial is {k: poly}, the poly on the
knot interval [tau[k], tau[k+1]) of a knot sequence tau (repeated knots allowed: empty intervals have no key).

    tau = [0] * 4 + [1, 2, 3] + [4] * 4          # cubic, boundary knots repeated
    B = bsplines(tau, 4)[4]                      # the K + M = 7 cubic B-splines (some may be {} when knots repeat)
    bval(B[2], tau, 1.5)                         # exact value
    fn = piece_fn(B[2], tau, support=True)       # a plot.function formula in x
"""
from fractions import Fraction as F

__all__ = ["bsplines", "bval", "shift", "poly_fn", "window", "piece_fn"]


def _add(p, q):
    n = max(len(p), len(q))
    return [(p[k] if k < len(p) else 0) + (q[k] if k < len(q) else 0) for k in range(n)]


def _mul(p, q):
    r = [F(0)] * (len(p) + len(q) - 1)
    for a, pa in enumerate(p):
        for b, qb in enumerate(q):
            r[a + b] += pa * qb
    return r


def _eval(p, x):
    return sum(c * x ** k for k, c in enumerate(p))


def bsplines(tau, M):
    """{m: [B_{i,m} as {interval: poly}]} for m = 1..M by ESL (5.77)-(5.78) on knots tau, i 0-based; terms with a 0 denominator are dropped."""
    tau = [F(v) for v in tau]
    out = {1: [({i: [F(1)]} if tau[i] < tau[i + 1] else {}) for i in range(len(tau) - 1)]}
    for m in range(2, M + 1):
        row = []
        for i in range(len(tau) - m):
            b = {}
            d1, d2 = tau[i + m - 1] - tau[i], tau[i + m] - tau[i + 1]
            if d1:
                w = [-tau[i] / d1, 1 / d1]
                for j, p in out[m - 1][i].items():
                    b[j] = _add(b.get(j, []), _mul(w, p))
            if d2:
                v = [tau[i + m] / d2, -1 / d2]
                for j, p in out[m - 1][i + 1].items():
                    b[j] = _add(b.get(j, []), _mul(v, p))
            row.append(b)
        out[m] = row
    return out


def bval(b, tau, x, closed_right=False):
    """A piecewise polynomial's exact value at x; closed_right=True also counts the last knot (the right boundary) as inside."""
    tau = [F(v) for v in tau]
    x = F(x)
    for j, p in b.items():
        last = closed_right and tau[j + 1] == tau[-1]
        if tau[j] <= x < tau[j + 1] or (last and x == tau[j + 1]):
            return _eval(p, x)
    return F(0)


def shift(p, a):
    """The polynomial p(s + a), as a polynomial in s."""
    r = [F(0)]
    for k in reversed(range(len(p))):
        r = _add(_mul(r, [F(a), F(1)]), [p[k]])
    return r


def _frac(c):
    c = F(c)
    return str(c.numerator) if c.denominator == 1 else f"{c.numerator}/{c.denominator}"


def poly_fn(p, s):
    """A polynomial in Horner form as a plain formula in the expression s, exact fractions: c0 + s*(c1 + s*(...))."""
    p = list(p)
    while len(p) > 1 and p[-1] == 0:
        p.pop()
    out = f"({_frac(p[-1])})"
    for c in reversed(p[:-1]):
        out = f"({_frac(c)} + {s}*{out})" if c else f"({s}*{out})"
    return out


def window(j, last=None, scale=1):
    """A formula that is 1 on [j, j + 1) of u = scale*x and 0 elsewhere (open to the right when j == last)."""
    u = "x" if scale == 1 else f"{scale}x"
    lo = f"step({u} - {j})" if j else f"step({u})"
    if last is not None and j == last:
        return lo
    return f"({lo} - step({u} - {j + 1}))"


def piece_fn(b, tau, scale=1, last=None, support=False, trim=0):
    """A piecewise polynomial on integer knots tau of u = scale*x as one plot.function formula in x.

    last: the interval start left open to the right (the last one, so the right boundary is drawn);
    support=True makes the formula NaN outside the support, so the board ends the curve and its label there;
    trim pulls that right end in (a box then ends at its top, not after its drop)."""
    u = "x" if scale == 1 else f"{scale}x"
    terms = []
    for k, p in sorted(b.items()):
        j = int(tau[k])
        q = shift(p, j)
        s = f"({u} - {j})" if j else (f"({u})" if scale != 1 else "x")
        terms.append(f"{window(j, last, scale)}*{poly_fn(q, s)}")
    if not terms:
        return "0"
    tail = ""
    if support:
        lo, hi = int(tau[min(b)]), int(tau[max(b) + 1])
        tail = f" + 0*sqrt(({u} - {lo})*({hi - trim if trim else hi} - {u}))"
    return " + ".join(terms) + tail
