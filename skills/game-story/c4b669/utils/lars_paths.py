"""Exact least angle regression and lasso coefficient paths (ESL Algorithms 3.2 and 3.2a) from a Gram matrix and X^T y, with their L1 arc lengths."""
import numpy as np


def lar_path(G, c, lasso=False, tol=1e-12, max_steps=50):
    """(knots, events) of LAR from G = X^T X (unit diagonal) and c = X^T y; lasso=True adds rule 4a (drop a coefficient that hits 0).

    knots is an array of coefficient vectors, one per breakpoint, from 0 to the
    least-squares fit; events[k] = (alpha, entered index or None, dropped index or None)
    for step k, alpha the fraction of that step's joint least-squares move taken.
    """
    G, c = np.asarray(G, float), np.asarray(c, float)
    p = len(c)
    beta = np.zeros(p)
    A = [int(np.argmax(abs(c)))]
    knots, events = [beta.copy()], []
    for _ in range(max_steps):
        r = c - G @ beta
        C = abs(r[A[0]])
        D = np.zeros(p)
        D[A] = np.linalg.solve(G[np.ix_(A, A)], r[A])
        a = G @ D
        al, new, drop = 1.0, None, None
        for j in range(p):
            if j in A:
                continue
            for t in ((C - r[j]) / (C - a[j]), (C + r[j]) / (C + a[j])):
                if tol < t < al:
                    al, new = t, j
        if lasso:
            for j in A:
                t = -beta[j] / D[j]
                if tol < t < al:
                    al, new, drop = t, None, j
        beta = beta + al * D
        if drop is not None:
            beta[drop] = 0
            A.remove(drop)
        knots.append(beta.copy())
        events.append((al, new, drop))
        if drop is None and new is None:
            break
        if new is not None:
            A.append(new)
    return np.array(knots), events


def l1_arc_length(knots):
    """The L1 arc length at each knot of a piecewise-linear path: 0, then the running sum of |change| over coefficients."""
    knots = np.asarray(knots, float)
    return np.concatenate([[0.0], np.cumsum(abs(np.diff(knots, axis=0)).sum(1))])
