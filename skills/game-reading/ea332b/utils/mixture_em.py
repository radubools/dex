"""One-dimensional Gaussian mixtures fitted by EM (ESL 6.32, 6.33, Section 8.5), numpy only."""
import numpy as np


def gaussian_density(x, mu, var):
    """Gaussian density with mean mu and variance var at x (broadcasts over arrays)."""
    return np.exp(-0.5 * (x - mu) ** 2 / var) / np.sqrt(2 * np.pi * var)


def responsibilities(x, mu, var, alpha):
    """ESL (6.33): an (N, M) array of the probability that each x_i came from each component."""
    w = np.asarray(alpha, float) * gaussian_density(np.asarray(x, float)[:, None], mu, var)
    return w / w.sum(axis=1, keepdims=True)


def gaussian_mixture_em_1d(x, mu, var, alpha, iters=3000, fix_var=False):
    """EM for a 1-D Gaussian mixture from starting (mu, var, alpha); fix_var keeps var fixed. Returns (mu, var, alpha, log-likelihood)."""
    x = np.asarray(x, float)
    mu, var, alpha = (np.asarray(v, float).copy() for v in (mu, var, alpha))
    for _ in range(iters):
        r = responsibilities(x, mu, var, alpha)
        n = r.sum(axis=0)
        alpha = n / len(x)
        mu = (r * x[:, None]).sum(axis=0) / np.maximum(n, 1e-300)
        if not fix_var:
            var = (r * (x[:, None] - mu) ** 2).sum(axis=0) / n
    ll = np.log((alpha * gaussian_density(x[:, None], mu, var)).sum(axis=1)).sum()
    return mu, var, alpha, ll
