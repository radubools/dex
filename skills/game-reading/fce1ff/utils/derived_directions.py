"""Derived-input regressions of ESL Section 3.5, numpy only: principal components regression, partial least squares (Algorithm 3.3) and conjugate gradients."""
import numpy as np


def pcr(X, y, M):
    """Principal components regression (ESL 3.61–3.62) on centred X: (beta of the first M components, all thetas, U, d, V)."""
    U, d, Vt = np.linalg.svd(X, full_matrices=False)
    Z = X @ Vt.T
    yc = y - y.mean()
    theta = (Z.T @ yc) / (Z ** 2).sum(0)
    return Vt[:M].T @ theta[:M], theta, U, d, Vt.T


def pls(X, y, M, tol=1e-20):
    """Partial least squares (ESL Algorithm 3.3) on standardized X: (beta_pls(m) for m = 1..M, the z_m, the phi_m); stops early when z_m vanishes."""
    yc = y - y.mean()
    Xm = X.copy()
    fit = np.zeros_like(yc)
    betas, zs, phis = [], [], []
    for _ in range(M):
        phi = Xm.T @ yc
        z = Xm @ phi
        if z @ z < tol:
            break
        fit = fit + (z @ yc) / (z @ z) * z
        Xm = Xm - np.outer(z, (z @ Xm) / (z @ z))
        zs.append(z)
        phis.append(phi)
        betas.append(np.linalg.lstsq(X, fit, rcond=None)[0])
    return betas, zs, phis


def cg(A, b, steps, tol=1e-24):
    """Conjugate-gradient iterates for A beta = b (A symmetric positive definite) from beta = 0: a list of `steps` vectors."""
    x = np.zeros_like(b, dtype=float)
    r = np.array(b, dtype=float)
    p = r.copy()
    out = []
    for _ in range(steps):
        if r @ r < tol:
            out.append(x.copy())
            continue
        a = (r @ r) / (p @ A @ p)
        x = x + a * p
        rn = r - a * (A @ p)
        p = rn + (rn @ rn) / (r @ r) * p
        r = rn
        out.append(x.copy())
    return out
