"""Exact lasso coefficient paths by least angle regression with the lasso modification (ESL Algorithm 3.2 + 3.2a), numpy only."""
import numpy as np


def lasso_path(X, y, tol=1e-12):
    """Every knot of the lasso path for (3.52) with centred y and standardized X: a list of (lambda, beta), lambda decreasing to 0."""
    n, p = X.shape
    beta = np.zeros(p)
    c = X.T @ y
    lam = float(np.max(np.abs(c)))
    active = [int(np.argmax(np.abs(c)))]
    knots = [(lam, beta.copy())]
    while lam > tol:
        A = np.array(active)
        s = np.sign(c[A])
        XA = X[:, A]
        d = np.linalg.solve(XA.T @ XA, s)            # beta_A moves by d per unit drop in lambda
        a = X.T @ (XA @ d)                            # how fast each inner product with the residual changes
        gam, nxt = lam, None
        for j in range(p):                            # the next inactive variable to tie the active ones
            if j in active:
                continue
            for g in ((lam - c[j]) / (1 - a[j]), (lam + c[j]) / (1 + a[j])):
                if tol < g < gam - tol:
                    gam, nxt = g, ("add", j)
        for k, j in enumerate(A):                     # or the next active coefficient to reach zero (3.2a)
            if d[k] != 0:
                g = -beta[j] / d[k]
                if tol < g < gam - tol:
                    gam, nxt = g, ("drop", j)
        beta[A] += gam * d
        c = c - gam * a
        lam -= gam
        if nxt is None:
            lam = 0.0
        elif nxt[0] == "add":
            active.append(nxt[1])
        else:
            beta[nxt[1]] = 0.0
            active.remove(nxt[1])
        knots.append((lam, beta.copy()))
    return knots


def at_s(knots, s):
    """The lasso coefficients at shrinkage factor s = t / t0 (t0 the L1 norm at the path's end), linear between knots."""
    betas = [b for _, b in knots]
    t0 = np.abs(betas[-1]).sum()
    ts = [np.abs(b).sum() / t0 for b in betas]
    for s0, s1, b0, b1 in zip(ts, ts[1:], betas, betas[1:]):
        if s0 <= s <= s1:
            return b0 + (s - s0) / (s1 - s0) * (b1 - b0)
    return betas[-1]
