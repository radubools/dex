"""Sparse principal components by ESL (14.71), numpy only: the elastic-net V-step, the Procrustes Theta-step, and the alternation.

Everything works from the p x p cross-product matrix S = X^T X, because (14.71) depends on X only through it
(Exercise 14.12): sum_i ||x_i - Theta V^T x_i||^2 = tr(S) - 2 tr(Theta^T S V) + tr(V^T S V) when Theta^T Theta = I.
"""
import numpy as np


def soft(z, g):
    """Soft thresholding: sign(z) (|z| - g)_+."""
    return np.sign(z) * np.maximum(np.abs(z) - g, 0.0)


def elastic_net(S, theta, lam, lam1, v0=None, sweeps=500, tol=1e-12):
    """argmin_v (theta - v)^T S (theta - v) + lam ||v||_2^2 + lam1 ||v||_1, by coordinate descent.

    This is the elastic-net regression of the response X theta on X, written with S = X^T X.
    """
    p = len(theta)
    v = np.zeros(p) if v0 is None else v0.copy()
    St = S @ theta
    for _ in range(sweeps):
        old = v.copy()
        for j in range(p):
            r = St[j] - S[j] @ v + S[j, j] * v[j]
            v[j] = soft(2 * r, lam1) / (2 * (S[j, j] + lam))
        if np.max(np.abs(v - old)) < tol:
            break
    return v


def elastic_net_block(S, Theta, lam, lam1, V0=None, iters=20000, tol=1e-10):
    """All K elastic nets of the V-step at once, by accelerated proximal gradient (FISTA); for large p.

    Same minimiser as elastic_net column by column; lam1 is a scalar or one value per column.
    """
    lam1 = np.broadcast_to(np.asarray(lam1, float), (Theta.shape[1],))
    L = 2 * (np.linalg.eigvalsh(S)[-1] + lam)
    V = np.zeros_like(Theta) if V0 is None else V0.copy()
    Y, t = V.copy(), 1.0
    ST = S @ Theta
    for _ in range(iters):
        G = 2 * (S @ Y - ST) + 2 * lam * Y
        Vn = soft(Y - G / L, lam1[None, :] / L)
        tn = (1 + np.sqrt(1 + 4 * t * t)) / 2
        Y = Vn + (t - 1) / tn * (Vn - V)
        done = np.max(np.abs(Vn - V)) < tol
        V, t = Vn, tn
        if done:
            break
    return V


def theta_step(S, V):
    """argmax_Theta tr(Theta^T M) s.t. Theta^T Theta = I_K with M = S V (14.116): Theta = U Q^T from M = U D Q^T."""
    U, _, Qt = np.linalg.svd(S @ V, full_matrices=False)
    return U @ Qt


def criterion(S, Theta, V, lam, lam1):
    """The value of (14.71), computed from S = X^T X."""
    lam1 = np.broadcast_to(np.asarray(lam1, float), (V.shape[1],))
    fit = np.trace(S) - 2 * np.trace(Theta.T @ S @ V) + np.trace(V.T @ S @ V @ Theta.T @ Theta)
    return fit + lam * np.sum(V ** 2) + np.sum(lam1 * np.abs(V).sum(0))


def spca(S, K, lam, lam1, Theta0=None, iters=200, tol=1e-10, trace=False):
    """Alternate the V-step (K elastic nets) and the Theta-step (Procrustes by SVD) on (14.71).

    Returns (V normalised to unit columns, Theta, raw V, criterion after every half-step if trace).
    Starts from the K leading eigenvectors of S unless Theta0 is given.
    """
    lam1 = np.broadcast_to(np.asarray(lam1, float), (K,))
    if Theta0 is None:
        w, E = np.linalg.eigh(S)
        Theta = E[:, np.argsort(w)[::-1][:K]]
    else:
        Theta = Theta0.copy()
    V = np.zeros_like(Theta)
    hist = []
    prev = np.inf
    for _ in range(iters):
        if len(S) > 30:
            V = elastic_net_block(S, Theta, lam, lam1, V)
        else:
            V = np.column_stack([elastic_net(S, Theta[:, k], lam, lam1[k], V[:, k]) for k in range(K)])
        if trace:
            hist.append(criterion(S, Theta, V, lam, lam1))
        Theta = theta_step(S, V)
        c = criterion(S, Theta, V, lam, lam1)
        if trace:
            hist.append(c)
        if prev - c < tol * max(1.0, abs(c)):
            break
        prev = c
    norms = np.linalg.norm(V, axis=0)
    Vn = V / np.where(norms > 0, norms, 1.0)
    return Vn, Theta, V, hist
