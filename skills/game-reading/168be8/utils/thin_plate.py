"""Thin-plate splines in the plane (ESL 5.37-5.39, Ex. 5.14), numpy only: the r^2 log r radial function, design and penalty on any knots."""
import numpy as np


def eta(r):
    """The thin-plate radial function r^2 log r (0 at r = 0), on arrays."""
    r = np.asarray(r, float)
    out = np.zeros_like(r)
    m = r > 0
    out[m] = r[m] ** 2 * np.log(r[m])
    return out


def distances(A, B):
    """Euclidean distances between the rows of A and the rows of B: (len(A), len(B))."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    return np.sqrt(((A[:, None, :] - B[None]) ** 2).sum(-1))


def null_basis(T):
    """An orthonormal basis Z (columns) of {a : T^T a = 0}, from a complete QR of T."""
    Q, _ = np.linalg.qr(np.asarray(T, float), mode="complete")
    return Q[:, T.shape[1]:]


def thin_plate_system(X, knots):
    """(A, Omega, Z) for f = b0 + b^T x + sum_k alpha_k eta(|x - knot_k|) with alpha = Z c: design A = [1, X, E Z], penalty 8 pi Z^T E Z on c."""
    X, knots = np.asarray(X, float), np.asarray(knots, float)
    Z = null_basis(np.c_[np.ones(len(knots)), knots])
    p = 1 + X.shape[1]
    A = np.c_[np.ones(len(X)), X, eta(distances(X, knots)) @ Z]
    Om = np.zeros((A.shape[1], A.shape[1]))
    Om[p:, p:] = 8 * np.pi * Z.T @ eta(distances(knots, knots)) @ Z
    return A, Om, Z
