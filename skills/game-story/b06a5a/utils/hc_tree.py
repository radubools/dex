"""Model Workshop's agglomerative clustering in Python (its merge loop, tie rule and merge ids), with cuts, diameters and cophenetic matrices."""
import math

import numpy as np

LINKAGES = ("single", "complete", "average")


def dist(a, b):
    """Euclidean distance between two (x, z) points, as the game measures it."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def cluster_distance(A, B, pts, linkage="single"):
    """Linkage between index lists A and B of pts: single (min), complete (max) or average (mean) of the pairs across."""
    ds = [dist(pts[i], pts[j]) for i in A for j in B]
    if linkage == "complete":
        return max(ds)
    if linkage == "average":
        return sum(ds) / len(ds)
    return min(ds)


def agglomerate(pts, linkage="single"):
    """The game's merges [{a, b, id, height, members}] in order (cluster.js / hierarchical.js: first closest pair wins ties, merge i gets id n + i)."""
    active = [{"id": i, "members": [i]} for i in range(len(pts))]
    merges = []
    nxt = len(pts)
    while len(active) > 1:
        best = None
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                d = cluster_distance(active[i]["members"], active[j]["members"], pts, linkage)
                if best is None or d < best[2] - 1e-12:
                    best = (i, j, d)
        i, j, d = best
        A, B = active[i], active[j]
        m = {"a": A["id"], "b": B["id"], "id": nxt, "height": d, "members": A["members"] + B["members"]}
        nxt += 1
        merges.append(m)
        active = [c for k, c in enumerate(active) if k not in (i, j)] + [{"id": m["id"], "members": m["members"]}]
    return merges


def cut(merges, n, h):
    """The clusters (sorted lists of point indices, ordered by their first point) left by the merges at or below height h."""
    label = list(range(n))
    for m in merges:
        if m["height"] <= h:
            keep = label[m["members"][0]]
            old = {label[k] for k in m["members"]}
            label = [keep if lab in old else lab for lab in label]
    groups = {}
    for i in range(n):
        groups.setdefault(label[i], []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def diameter(members, pts):
    """ESL (14.44): the largest distance between two members of a group (0 for one point)."""
    return max((dist(pts[i], pts[j]) for i in members for j in members), default=0)


def cophenetic(merges, n):
    """The n x n cophenetic matrix: C[i, j] is the height of the merge where i and j first share a cluster."""
    C = np.zeros((n, n))
    seen = {i: [i] for i in range(n)}
    for m in merges:
        for i in seen[m["a"]]:
            for j in seen[m["b"]]:
                C[i, j] = C[j, i] = m["height"]
        seen[m["id"]] = seen[m["a"]] + seen[m["b"]]
    return C


def cophenetic_correlation(pts, merges):
    """Correlation between the pairwise distances of pts and their cophenetic heights over all pairs."""
    n = len(pts)
    iu = np.triu_indices(n, 1)
    D = np.array([[dist(a, b) for b in pts] for a in pts])
    return float(np.corrcoef(D[iu], cophenetic(merges, n)[iu])[0, 1])
