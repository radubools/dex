"""Exact lasso and Dantzig selector solutions for small examples, from the Gram matrix G = X^T X and c = X^T y."""
import itertools

import numpy as np


def lasso_exact(G, c, lam, tol=1e-9):
    """The lasso minimiser of 1/2 b'Gb - c'b + lam |b|_1 (ESL (3.88)), found by checking KKT on every sign pattern (small p only)."""
    G, c = np.asarray(G, float), np.asarray(c, float)
    p = len(c)
    for sg in itertools.product((-1, 0, 1), repeat=p):
        sg = np.array(sg)
        A = sg != 0
        b = np.zeros(p)
        if A.any():
            b[A] = np.linalg.solve(G[np.ix_(A, A)], c[A] - lam * sg[A])
            if np.any(np.sign(b[A]) != sg[A]):
                continue
        if np.all(np.abs((c - G @ b)[~A]) <= lam + tol):
            return b
    raise ValueError("no KKT point: is G positive definite?")


def dantzig(G, c, s, weights=None):
    """The Dantzig selector (ESL (3.78)): min |b|_1 subject to |c - G b|_inf <= s, as a linear program; `weights` (2p costs) perturbs it to test uniqueness."""
    from scipy.optimize import linprog
    G, c = np.asarray(G, float), np.asarray(c, float)
    p = len(c)
    A = np.vstack([np.hstack([-G, G]), np.hstack([G, -G])])
    w = np.ones(2 * p) if weights is None else np.asarray(weights, float)
    r = linprog(w, A_ub=A, b_ub=np.concatenate([s - c, s + c]), bounds=[(0, None)] * (2 * p), method="highs")
    return r.x[:p] - r.x[p:]
