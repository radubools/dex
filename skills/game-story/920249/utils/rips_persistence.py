"""Vietoris-Rips persistent homology of a small point cloud over Z/2: filtration, barcodes, simplex counts and Betti numbers.

A simplex enters the filtration at the largest distance between two of its vertices
(an edge when d <= eps). Exact and pure Python, meant for the handful of points a lesson
shows (every subset up to the top dimension is enumerated).
"""
from itertools import combinations
from math import dist, inf


def rips_filtration(points, max_dim=2, max_eps=inf):
    """[(eps, dim, vertices)] for every simplex up to dimension max_dim entering by max_eps, sorted by (eps, dim)."""
    n = len(points)
    D = [[dist(points[i], points[j]) for j in range(n)] for i in range(n)]
    out = [(0.0, 0, (i,)) for i in range(n)]
    for k in range(2, max_dim + 2):
        for s in combinations(range(n), k):
            e = max(D[a][b] for a, b in combinations(s, 2))
            if e <= max_eps:
                out.append((e, k - 1, s))
    out.sort(key=lambda t: (round(t[0], 12), t[1], t[2]))
    return out


def persistence(points, top=1, max_eps=inf):
    """{dim: sorted [(birth, death)]} for dims 0..top, death inf for features that never die; zero-length bars dropped."""
    filt = rips_filtration(points, top + 1, max_eps)
    index = {s: i for i, (_, _, s) in enumerate(filt)}
    cols = [set() if d == 0 else {index[f] for f in combinations(s, d)} for _, d, s in filt]
    low_of = {}
    pairs = {}
    for j, col in enumerate(cols):
        while col:
            low = max(col)
            if low in low_of:
                col ^= cols[low_of[low]]
            else:
                low_of[low] = j
                pairs[low] = j
                break
    killed = set(pairs.values())
    res = {}
    for i, (e, d, s) in enumerate(filt):
        if d > top or i in killed:
            continue
        if i in pairs:
            death = filt[pairs[i]][0]
            if death > e + 1e-12:
                res.setdefault(d, []).append((e, death))
        else:
            res.setdefault(d, []).append((e, inf))
    return {d: sorted(v) for d, v in sorted(res.items())}


def counts(points, eps, max_dim=3):
    """Simplices of VR_eps by dimension, [vertices, edges, triangles, ...] up to max_dim."""
    n = len(points)
    c = [n] + [0] * max_dim
    for k in range(2, max_dim + 2):
        for s in combinations(range(n), k):
            if all(dist(points[a], points[b]) <= eps + 1e-12 for a, b in combinations(s, 2)):
                c[k - 1] += 1
    return c


def betti_at(diagram, eps):
    """{dim: number of bars alive at eps (birth <= eps < death)} for a persistence() diagram."""
    return {d: sum(1 for b, x in bars if b <= eps < x) for d, bars in diagram.items()}
