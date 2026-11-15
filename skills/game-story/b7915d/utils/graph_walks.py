"""Small-graph numbers for graph lessons: adjacency, hop distances, random-walk matrix, earth mover's distance, Ollivier-Ricci curvature."""
import numpy as np
from scipy.optimize import linprog


def adjacency(edges, n):
    """Symmetric 0/1 adjacency matrix of an undirected graph on nodes 0..n-1 from (i, j) pairs."""
    A = np.zeros((n, n))
    for i, j in edges:
        A[i, j] = A[j, i] = 1
    return A


def hops(edges, n):
    """All-pairs shortest-path lengths in edges (BFS); inf between disconnected nodes."""
    A = adjacency(edges, n)
    D = np.full((n, n), np.inf)
    for s in range(n):
        D[s, s], frontier, d = 0, [s], 0
        while frontier:
            d += 1
            nxt = []
            for u in frontier:
                for v in np.nonzero(A[u])[0]:
                    if D[s, v] == np.inf:
                        D[s, v] = d
                        nxt.append(v)
            frontier = nxt
    return D


def walk(edges, n, *, self_loops=True):
    """Row-stochastic random-walk matrix D^-1 A (with self-loops by default: A + I, the GCN's A-hat read as a walk)."""
    A = adjacency(edges, n) + (np.eye(n) if self_loops else 0)
    return A / A.sum(axis=1, keepdims=True)


def cloud(i, edges, n):
    """Uniform distribution on node i and its neighbours (Ollivier's one-step measure mu_i)."""
    A = adjacency(edges, n)
    nb = [i, *np.nonzero(A[i])[0]]
    mu = np.zeros(n)
    mu[nb] = 1 / len(nb)
    return mu


def w1(mu, nu, D):
    """Earth mover's (Wasserstein-1) distance between two distributions on the nodes, under ground distances D, by linear programming."""
    n = len(mu)
    A_eq, b_eq = [], []
    for i in range(n):
        row = np.zeros((n, n)); row[i, :] = 1; A_eq.append(row.flatten()); b_eq.append(mu[i])
    for j in range(n):
        row = np.zeros((n, n)); row[:, j] = 1; A_eq.append(row.flatten()); b_eq.append(nu[j])
    r = linprog(np.asarray(D, float).flatten(), A_eq=np.array(A_eq), b_eq=np.array(b_eq), bounds=(0, None), method="highs")
    return r.fun


def ollivier_curvature(i, j, edges, n):
    """Ollivier-Ricci curvature kappa(i, j) = 1 - W1(mu_i, mu_j) / d(i, j), mu uniform on each node and its neighbours."""
    D = hops(edges, n)
    return 1 - w1(cloud(i, edges, n), cloud(j, edges, n), D) / D[i, j]
