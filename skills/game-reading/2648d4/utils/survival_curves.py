"""Survival-data estimates for reading figures, numpy only: Kaplan-Meier with Greenwood standard errors and a one-covariate Cox fit."""
import numpy as np


def km_with_se(time, event):
    """Kaplan-Meier steps with Greenwood standard errors (event 1 = death, 0 = censored): arrays (times, S, se), starting at (0, 1, 0)."""
    time, event = np.asarray(time, float), np.asarray(event, float)
    ts, ss, se, s, g = [0.0], [1.0], [0.0], 1.0, 0.0
    for t in np.unique(time[event == 1]):
        n = (time >= t).sum()
        d = ((time == t) & (event == 1)).sum()
        s *= 1 - d / n
        if n > d:
            g += d / (n * (n - d))
        ts.append(float(t)); ss.append(s); se.append(s * np.sqrt(g))
    return np.array(ts), np.array(ss), np.array(se)


def cox_1d(x, time, event, iters=50):
    """Cox partial-likelihood fit of one covariate x by Newton's method (Breslow ties): (beta, likelihood-ratio statistic against beta = 0)."""
    x, time, event = (np.asarray(v, float) for v in (x, time, event))
    order = np.argsort(-time)                      # descending, so cumulative sums are the risk sets
    x, e = x[order], event[order]

    def loglik(b):
        w = np.exp(b * x)
        c0, c1, c2 = np.cumsum(w), np.cumsum(w * x), np.cumsum(w * x * x)
        ll = np.sum(e * (b * x - np.log(c0)))
        g = np.sum(e * (x - c1 / c0))
        h = -np.sum(e * (c2 / c0 - (c1 / c0) ** 2))
        return ll, g, h
    b = 0.0
    for _ in range(iters):
        _, g, h = loglik(b)
        b -= g / h
    return b, 2 * (loglik(b)[0] - loglik(0.0)[0])
