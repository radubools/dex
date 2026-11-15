"""Fisher's reduced-rank LDA (ESL Section 4.3.3), numpy only: canonical variates from the sphered centroids, and nearest-centroid scores in the first L of them."""
import numpy as np


def inv_sqrt(S):
    """The symmetric inverse square root S^(-1/2) of a symmetric positive-definite matrix, from its eigen-decomposition."""
    d, U = np.linalg.eigh(S)
    return U @ np.diag(d ** -0.5) @ U.T


def canonical(M, W, pis):
    """ESL 4.3.3's steps for centroids M (K, p), within-class covariance W and priors pis: (V with columns v_l = W^-1/2 v*_l, eigenvalues D_B of the pis-weighted covariance B* of M* = M W^-1/2, V*), largest first."""
    Wm = inv_sqrt(W)
    Ms = M @ Wm
    c = pis @ Ms
    Bs = (Ms - c).T @ np.diag(pis) @ (Ms - c)
    dB, Vs = np.linalg.eigh(Bs)
    order = np.argsort(dB)[::-1]
    dB, Vs = dB[order], Vs[:, order]
    return Wm @ Vs, dB, Vs


def rr_scores(X, V, M, pis, L):
    """Reduced-rank LDA scores of the rows of X, one column per class: -1/2 squared distance to each centroid in the first L discriminant coordinates (columns of V), plus log pi_k; argmax classifies."""
    Z, C = X @ V[:, :L], M @ V[:, :L]
    return -0.5 * ((Z[:, None, :] - C[None]) ** 2).sum(-1) + np.log(pis)
