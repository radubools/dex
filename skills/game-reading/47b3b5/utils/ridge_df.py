"""Ridge effective degrees of freedom (ESL 3.50) inverted to a penalty, and the one-standard-error rule (ESL 7.10), numpy only."""
import numpy as np


def lambda_for_df(d, df, iters=300):
    """The ridge penalty lambda with sum d_j^2 / (d_j^2 + lambda) = df (ESL 3.50), by bisection on the singular values d; 0 for df >= len(d), inf for df <= 0."""
    d = np.asarray(d, dtype=float)
    if df >= len(d):
        return 0.0
    if df <= 0:
        return np.inf
    lo, hi = 0.0, 1e9 * max(1.0, float((d ** 2).max()))
    for _ in range(iters):
        lam = (lo + hi) / 2
        if (d ** 2 / (d ** 2 + lam)).sum() > df:
            lo = lam
        else:
            hi = lam
    return (lo + hi) / 2


def one_se_index(cv, se):
    """Index of the first (least complex) grid point whose CV error is within one standard error of the minimum (ESL 7.10); grids must run simple to complex."""
    cv, se = np.asarray(cv, dtype=float), np.asarray(se, dtype=float)
    i = int(cv.argmin())
    return int(np.nonzero(cv <= cv[i] + se[i])[0][0])
