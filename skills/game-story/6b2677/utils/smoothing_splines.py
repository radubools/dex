"""Natural cubic splines exactly: the Reinsch factors Q and R of a set of knots, and the spline through given values as a plain plot.function formula."""
import numpy as np


def penalty_matrix(t):
    """(K, Q, R) for distinct increasing knots t (Green & Silverman): f^T K f = integral of g''^2 for the natural cubic spline g through (t, f), K = Q R^-1 Q^T."""
    t = np.asarray(t, float)
    n = len(t)
    h = np.diff(t)
    Q = np.zeros((n, n - 2))
    R = np.zeros((n - 2, n - 2))
    for j in range(1, n - 1):
        Q[j - 1, j - 1] = 1 / h[j - 1]
        Q[j, j - 1] = -1 / h[j - 1] - 1 / h[j]
        Q[j + 1, j - 1] = 1 / h[j]
        R[j - 1, j - 1] = (h[j - 1] + h[j]) / 3
        if j < n - 2:
            R[j - 1, j] = R[j, j - 1] = h[j] / 6
    return Q @ np.linalg.solve(R, Q.T), Q, R


def natural_coeffs(t, f):
    """(a, b, c) with g(u) = a + b u + sum_j c_j (u - t_j)_+^3 the natural cubic spline through (t, f): linear past both ends, sum c = sum c t = 0."""
    t = np.asarray(t, float)
    f = np.asarray(f, float)
    _, Q, R = penalty_matrix(t)
    h = np.diff(t)
    gam = np.zeros(len(t))
    gam[1:-1] = np.linalg.solve(R, Q.T @ f)       # g'' at the knots (0 at both ends)
    slope = np.diff(gam) / h                       # g''' on each interval
    c = np.diff(np.concatenate([[0.0], slope, [0.0]])) / 6
    b = (f[1] - f[0]) / h[0] - h[0] * (2 * gam[0] + gam[1]) / 6
    return f[0] - b * t[0], b, c


def spline_values(t, f, u):
    """The natural cubic spline through (t, f), evaluated with numpy at the points u."""
    a, b, c = natural_coeffs(t, f)
    u = np.asarray(u, float)
    t = np.asarray(t, float)
    return a + b * u + (c[None, :] * np.maximum(u[:, None] - t[None, :], 0) ** 3).sum(1)


def _num(v, digits):
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def natural_spline_fn(t, f, var="u", digits=8):
    """The natural cubic spline through (t, f) as a plain plot.function formula in `var`: a + b var + sum c_j relu(var - t_j)^3 (keep digits high: the cubes amplify rounding)."""
    a, b, c = natural_coeffs(t, f)
    parts = [_num(a, digits), f"{_num(b, digits)}{var}"]
    for cj, tj in zip(c, t):
        if abs(cj) > 10 ** -digits:
            parts.append(f"{_num(cj, digits)} relu({var} - {_num(tj, 4)})^3")
    return " + ".join(parts).replace("+ -", "- ")
