"""Nearest shrunken centroids (ESL 18.2, (18.2)-(18.7)), numpy only: soft thresholding, the fit, and prediction at a threshold."""
import numpy as np


def soft(d, delta):
    """Soft thresholding (18.5): sign(d)(|d| - delta)_+, elementwise."""
    return np.sign(d) * np.maximum(np.abs(d) - delta, 0.0)


def nsc_fit(X, y, K):
    """Fit on rows X with labels y in 0..K-1: {xbar, s, s0, m, d, prior}, d the K x p gaps d_kj of (18.4), s0 the median s_j."""
    N = len(y)
    xbar = X.mean(axis=0)
    cent = np.array([X[y == k].mean(axis=0) for k in range(K)])
    Nk = np.array([(y == k).sum() for k in range(K)])
    resid = X - cent[y]
    s = np.sqrt((resid ** 2).sum(axis=0) / (N - K))
    s0 = np.median(s)
    m = np.sqrt(1.0 / Nk - 1.0 / N)
    d = (cent - xbar) / (m[:, None] * (s + s0))
    return dict(xbar=xbar, s=s, s0=s0, m=m, d=d, prior=Nk / N)


def nsc_predict(fit, X, delta):
    """Classes (18.3), scores (18.2) with shrunken centroids (18.7) and s_j + s_0, and the number of features kept, at threshold delta."""
    dp = soft(fit["d"], delta)
    sc = fit["s"] + fit["s0"]
    shrunk = fit["xbar"] + fit["m"][:, None] * sc * dp
    keep = np.any(dp != 0, axis=0)
    scores = -(((X[:, None, :] - shrunk[None]) / sc) ** 2)[:, :, keep].sum(axis=2) + 2 * np.log(fit["prior"])
    return scores.argmax(axis=1), scores, int(keep.sum())
