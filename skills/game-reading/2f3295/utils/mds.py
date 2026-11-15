"""Multidimensional scaling, numpy only (ESL 14.8): classical, least squares, Sammon and nonmetric maps from a dissimilarity matrix."""
import numpy as np


def dists(Z):
    """All pairwise Euclidean distances between the rows of Z, as an N x N array."""
    Z = np.asarray(Z, float)
    return np.sqrt(((Z[:, None] - Z[None]) ** 2).sum(-1))


def _off(n):
    return ~np.eye(n, dtype=bool)


def double_centre(D):
    """Centred inner products from distances (ESL 18.31): (I - M)(-D^2 / 2)(I - M), M = 11^T / N."""
    D = np.asarray(D, float)
    n = len(D)
    J = np.eye(n) - np.ones((n, n)) / n
    return J @ (-0.5 * D ** 2) @ J


def classical(S, k=2):
    """Classical scaling (ESL 14.100, Ex. 14.11): (Z = E_k D_k from the top-k eigenpairs of S, all eigenvalues largest first)."""
    ev, V = np.linalg.eigh(np.asarray(S, float))
    o = np.argsort(ev)[::-1]
    ev, V = ev[o], V[:, o]
    return V[:, :k] * np.sqrt(np.maximum(ev[:k], 0)), ev


def stress(Z, D):
    """Least squares stress S_M (ESL 14.98): squared misfit of map distances to D over ordered pairs i != i'."""
    D = np.asarray(D, float)
    return float(((D - dists(Z))[_off(len(D))] ** 2).sum())


def _pair_grad(Z, C):
    C = C.copy()
    np.fill_diagonal(C, 0.0)
    return (C[:, :, None] * (Z[:, None] - Z[None])).sum(1)


def stress_grad(Z, D):
    """Gradient of S_M (14.98) in each z_i: -4 sum_i' (d - r)(z_i - z_i') / r, each unordered pair counted twice."""
    Z, D = np.asarray(Z, float), np.asarray(D, float)
    R = dists(Z)
    np.fill_diagonal(R, 1.0)
    return _pair_grad(Z, -4 * (D - R) / R)


def least_squares(Z0, D, lr=0.01, steps=400):
    """Gradient descent on S_M (14.98) from Z0: (final map, stress before and after every step)."""
    Z = np.array(Z0, float)
    trace = [stress(Z, D)]
    for _ in range(steps):
        Z = Z - lr * stress_grad(Z, D)
        trace.append(stress(Z, D))
    return Z, trace


def sammon_stress(Z, D):
    """Sammon stress S_Sm (ESL 14.99): each pair's squared error divided by its dissimilarity (D > 0 off the diagonal)."""
    D = np.asarray(D, float)
    off = _off(len(D))
    return float((((D - dists(Z)) ** 2)[off] / D[off]).sum())


def sammon_grad(Z, D):
    """Gradient of S_Sm (14.99) in each z_i."""
    Z, D = np.asarray(Z, float), np.asarray(D, float)
    R = dists(Z)
    np.fill_diagonal(R, 1.0)
    Dd = D.copy()
    np.fill_diagonal(Dd, 1.0)
    return _pair_grad(Z, -4 * (D - R) / (R * Dd))


def sammon(Z0, D, lr=0.005, steps=4000):
    """Gradient descent on S_Sm (14.99) from Z0: the final map."""
    Z = np.array(Z0, float)
    for _ in range(steps):
        Z = Z - lr * sammon_grad(Z, D)
    return Z


def isotonic(y):
    """Pool-adjacent-violators: the non-decreasing sequence closest to y in least squares."""
    blocks = []  # [mean, weight]
    for v in y:
        blocks.append([float(v), 1.0])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            m2, w2 = blocks.pop()
            m1, w1 = blocks.pop()
            blocks.append([(m1 * w1 + m2 * w2) / (w1 + w2), w1 + w2])
    return np.concatenate([np.full(int(w), m) for m, w in blocks]) if blocks else np.array([])


def theta_fit(Z, D):
    """Disparities theta(d) for nonmetric scaling (ESL 14.101): isotonic regression of map distances on the order of D (ties by map distance), N x N."""
    D = np.asarray(D, float)
    iu = np.triu_indices(len(D), 1)
    d, r = D[iu], dists(Z)[iu]
    o = np.lexsort((r, d))
    th = np.empty_like(r)
    th[o] = isotonic(r[o])
    T = np.zeros_like(D)
    T[iu] = th
    return T + T.T


def nonmetric_stress(Z, T):
    """Nonmetric stress S_NM (ESL 14.101) for fixed disparities T = theta(d)."""
    R = dists(Z)
    off = _off(len(R))
    return float(((R - T)[off] ** 2).sum() / (R[off] ** 2).sum())


def nonmetric_grad(Z, T):
    """Gradient of S_NM (14.101) in each z_i for fixed disparities T (quotient rule)."""
    Z = np.asarray(Z, float)
    R = dists(Z)
    off = _off(len(R))
    num = ((R - T)[off] ** 2).sum()
    den = (R[off] ** 2).sum()
    np.fill_diagonal(R, 1.0)
    gn = _pair_grad(Z, 4 * (R - T) / R)
    gd = 4 * (Z[:, None] - Z[None]).sum(1)
    return (gn * den - num * gd) / den ** 2


def nonmetric(Z0, D, outer=60, inner=200, lr=2.0):
    """Shephard-Kruskal nonmetric scaling (14.101): alternate gradient descent on Z and isotonic theta; (map, final disparities, stress per round)."""
    Z = np.array(Z0, float)
    trace = []
    for _ in range(outer):
        T = theta_fit(Z, D)
        for _ in range(inner):
            Z = Z - lr * nonmetric_grad(Z, T)
        trace.append(nonmetric_stress(Z, theta_fit(Z, D)))
    return Z, theta_fit(Z, D), trace


def procrustes(A, B):
    """B shifted, rotated and reflected (no scaling) to best match A, for comparing maps that are unique only up to rigid motion."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    a, b = A.mean(0), B.mean(0)
    U, _, Vt = np.linalg.svd((B - b).T @ (A - a))
    return (B - b) @ (U @ Vt) + a
