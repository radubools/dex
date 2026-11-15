"""Non-negative matrix factorization by Lee and Seung's updates (ESL 14.72-14.74) and archetypal analysis (ESL 14.75-14.77), numpy and scipy."""
import numpy as np
from scipy.optimize import nnls

EPS = 1e-12


def poisson_loglik(X, W, H):
    """L(W, H) of ESL (14.73): the Poisson log-likelihood of X with means WH, without the log x! term."""
    WH = W @ H
    return float(np.sum(X * np.log(WH + EPS) - WH))


def nmf(X, r, iters=500, seed=0):
    """Rank-r NMF of non-negative X by the multiplicative updates (14.74) from a random positive start: (W, H, L after each step)."""
    rng = np.random.default_rng(seed)
    N, p = X.shape
    W = rng.uniform(0.1, 1.0, (N, r)) * np.sqrt(X.mean() / r)
    H = rng.uniform(0.1, 1.0, (r, p)) * np.sqrt(X.mean() / r)
    Ls = [poisson_loglik(X, W, H)]
    for _ in range(iters):
        W *= ((X / (W @ H + EPS)) @ H.T) / (H.sum(axis=1)[None, :] + EPS)
        H *= (W.T @ (X / (W @ H + EPS))) / (W.sum(axis=0)[:, None] + EPS)
        Ls.append(poisson_loglik(X, W, H))
    return W, H, np.array(Ls)


def simplex_ls(A, b, M=200.0):
    """argmin ||A w - b|| over w >= 0 with sum(w) = 1: NNLS with the sum constraint as an extra row weighted by M."""
    w, _ = nnls(np.vstack([A, M * np.ones(A.shape[1])]), np.append(b, M))
    return w / w.sum()


def archetypes(X, r, iters=60, starts=6, seed=0):
    """Archetypal analysis of the rows of X with r archetypes by Cutler and Breiman's alternating fit, best of `starts`: (H, W, B, J).

    Each start puts the archetypes on r distinct data points (hull vertices when X is 2-D); W and B are
    row-stochastic, H = B X, and J = ||X - W H||^2 is a local minimum of (14.77).
    """
    rng = np.random.default_rng(seed)
    N = len(X)
    pool = np.arange(N)
    if X.shape[1] == 2:
        from scipy.spatial import ConvexHull
        pool = ConvexHull(X).vertices
    best = None
    for _ in range(starts):
        B = np.zeros((r, N))
        B[np.arange(r), rng.choice(pool, r, replace=len(pool) < r)] = 1.0
        H = B @ X
        J_old = np.inf
        for _ in range(iters):
            W = np.array([simplex_ls(H.T, x) for x in X])            # rows of X as convex mixes of archetypes
            Z = np.linalg.lstsq(W, X, rcond=None)[0]                 # unconstrained best archetypes
            B = np.array([simplex_ls(X.T, z) for z in Z])            # each pulled back to a convex mix of data
            H = B @ X
            W = np.array([simplex_ls(H.T, x) for x in X])
            J = float(((X - W @ H) ** 2).sum())
            if J_old - J < 1e-9 * max(1.0, J):
                break
            J_old = J
        if best is None or J < best[3]:
            best = (H, W, B, J)
    return best
