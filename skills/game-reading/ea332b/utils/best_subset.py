"""Best-subset linear regression (ESL Section 3.3.1) and exact leave-one-out least-squares fits, numpy only."""
import itertools

import numpy as np


def design(X, subset):
    """The least-squares design for the columns `subset` of X: a column of ones, then those columns in order."""
    return np.column_stack([np.ones(len(X))] + [X[:, j] for j in subset])


def best_subsets(X, y):
    """The lowest-RSS subset of each size k = 0..p by exhaustive search, intercept always in: list of (subset tuple, rss)."""
    p = X.shape[1]
    best = []
    for k in range(p + 1):
        fits = []
        for s in itertools.combinations(range(p), k):
            A = design(X, s)
            beta, *_ = np.linalg.lstsq(A, y, rcond=None)
            fits.append((s, float(np.sum((y - A @ beta) ** 2))))
        best.append(min(fits, key=lambda f: f[1]))
    return best


def loo_predictions(X, y, subset):
    """Leave-one-out fitted values of the least-squares fit on `subset` (with intercept), from the hat matrix, no refitting."""
    A = design(X, subset)
    H = A @ np.linalg.pinv(A)
    fit = H @ y
    return y - (y - fit) / (1 - np.diag(H))
