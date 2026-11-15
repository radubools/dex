"""Local polynomial regression weights (ESL 6.1): the tri-cube kernel and the equivalent kernel l(x0) of a degree-d local fit."""
import numpy as np


def tricube(t):
    """The tri-cube function D(t) = (1 - |t|^3)^3 for |t| <= 1, else 0 (ESL 6.6); works on arrays."""
    a = np.clip(1 - np.abs(t) ** 3, 0, None)
    return a ** 3


def local_poly_weights(x0, x, lam, d, kernel=tricube):
    """Equivalent-kernel weights l(x0) of a degree-d local polynomial fit (ESL 6.8–6.9, 6.11): f-hat(x0) = l @ y.

    `kernel` maps the scaled distance (x - x0) / lam to a weight. The basis is centred at x0
    (1, x - x0, ..., (x - x0)^d), which spans the same polynomials as (1, x, ..., x^d) and is better conditioned.
    """
    x = np.asarray(x, dtype=float)
    w = kernel((x - x0) / lam)
    B = np.vander(x - x0, d + 1, increasing=True)
    e0 = np.zeros(d + 1)
    e0[0] = 1.0
    BtW = B.T * w
    return BtW.T @ np.linalg.solve(BtW @ B, e0)
