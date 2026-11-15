"""Linear smoothers on 1-D data as numpy matrices (local linear, loess, smoothing spline), their CV/GCV/Cp scores, and a local linear fit as a plot.function formula."""
import math

import numpy as np


def _num(v, digits):
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def local_linear_row(xs, x0, w):
    """The equivalent kernel l(x0) of a weighted local linear fit at x0 with kernel weights w (ESL (6.8)-(6.9))."""
    xs, w = np.asarray(xs, float), np.asarray(w, float)
    B = np.column_stack([np.ones_like(xs), xs - x0])
    A = B.T @ (w[:, None] * B)
    return np.linalg.solve(A, (B * w[:, None]).T)[0]


def local_linear_S(xs, lam):
    """The smoother matrix of local linear regression with a Gaussian kernel of standard deviation lam: row i is l(x_i)."""
    xs = np.asarray(xs, float)
    return np.array([local_linear_row(xs, x0, np.exp(-0.5 * ((xs - x0) / lam) ** 2)) for x0 in xs])


def loess_S(xs, span):
    """The smoother matrix of loess: local linear, tri-cube kernel (6.6), nearest-neighbour width holding round(span * N) points."""
    xs = np.asarray(xs, float)
    k = int(round(span * len(xs)))
    rows = []
    for x0 in xs:
        d = np.abs(xs - x0)
        t = d / (np.sort(d)[k - 1] * 1.000001)
        rows.append(local_linear_row(xs, x0, np.where(t < 1, (1 - t ** 3) ** 3, 0.0)))
    return np.array(rows)


def spline_penalty_K(xs):
    """The natural cubic smoothing spline's penalty matrix K = Q R^-1 Q^T (Reinsch form) for sorted, distinct xs."""
    xs = np.asarray(xs, float)
    n = len(xs)
    h = np.diff(xs)
    Q = np.zeros((n, n - 2))
    R = np.zeros((n - 2, n - 2))
    for j in range(1, n - 1):
        Q[j - 1, j - 1] = 1 / h[j - 1]
        Q[j, j - 1] = -1 / h[j - 1] - 1 / h[j]
        Q[j + 1, j - 1] = 1 / h[j]
        R[j - 1, j - 1] = (h[j - 1] + h[j]) / 3
        if j < n - 2:
            R[j - 1, j] = R[j, j - 1] = h[j] / 6
    return Q @ np.linalg.solve(R, Q.T)


def smoothing_spline_S(xs, df, *, iters=200):
    """(S, lam): the smoothing spline's smoother (I + lam K)^-1 whose trace is df (2 < df < N), lam found by bisection on log lam."""
    K = spline_penalty_K(xs)
    I = np.eye(len(K))
    lo, hi = -20.0, 5.0
    for _ in range(iters):
        mid = (lo + hi) / 2
        if np.trace(np.linalg.inv(I + math.exp(mid) * K)) > df:
            lo = mid
        else:
            hi = mid
    lam = math.exp((lo + hi) / 2)
    return np.linalg.inv(I + lam * K), lam


def smoother_scores(S, y, f=None, sigma2=None):
    """{df, asr, cv, gcv, cp, pe} of a linear smoother S on y: LOO CV by (y - fit)/(1 - S_ii); cp needs sigma2, pe also the true f (else None)."""
    S, y = np.asarray(S, float), np.asarray(y, float)
    n = len(y)
    r = y - S @ y
    df = float(np.trace(S))
    asr = float(r @ r / n)
    out = dict(df=df, asr=asr, cv=float(np.mean((r / (1 - np.diag(S))) ** 2)), gcv=asr / (1 - df / n) ** 2, cp=None, pe=None)
    if sigma2 is not None:
        out["cp"] = asr + 2 * sigma2 * df / n
        if f is not None:
            bias = (np.eye(n) - S) @ np.asarray(f, float)
            out["pe"] = float((bias @ bias + sigma2 * np.trace(S.T @ S)) / n + sigma2)
    return out


def local_linear_fn(xs, ys, *, lam="lam", var="x", xdigits=4, ydigits=2):
    """The Gaussian-kernel local linear fit through (xs, ys) as one plain plot.function formula in `var` with parameter `lam`."""
    W = [f"exp(-(({var} - {_num(x, xdigits)})/{lam})^2/2)" for x in xs]
    d = [f"({_num(x, xdigits)} - {var})" for x in xs]
    S0 = " + ".join(W)
    S1 = " + ".join(f"{w}*{di}" for w, di in zip(W, d))
    S2 = " + ".join(f"{w}*{di}^2" for w, di in zip(W, d))
    T0 = " + ".join(f"{_num(y, ydigits)}*{w}" for w, y in zip(W, ys))
    T1 = " + ".join(f"{_num(y, ydigits)}*{w}*{di}" for w, y, di in zip(W, ys, d))
    return f"(({S2})*({T0}) - ({S1})*({T1}))/(({S0})*({S2}) - ({S1})^2)"
