"""DANN's local between-class matrices B_i and their average B-bar (ESL 13.4, (13.10)), as numpy arrays."""
import numpy as np


def local_between(P, cls, i, k=6):
    """B_i = sum_k pi_k (m_k - m)(m_k - m)^T over the k points of P (rows) nearest P[i], itself included, with class labels cls."""
    P = np.asarray(P, float)
    cls = np.asarray(cls)
    d = np.linalg.norm(P - P[i], axis=1)
    nb = np.argsort(d, kind="stable")[:k]
    m = P[nb].mean(0)
    B = np.zeros((P.shape[1], P.shape[1]))
    for c in np.unique(cls[nb]):
        sel = nb[cls[nb] == c]
        v = P[sel].mean(0) - m
        B += len(sel) / k * np.outer(v, v)
    return B


def bbar(P, cls, k=6):
    """(13.10): the average over every point of P of its local_between matrix."""
    return sum(local_between(P, cls, i, k) for i in range(len(P))) / len(P)
