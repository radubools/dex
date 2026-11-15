"""Model Workshop's Gini decision tree (src/ml/tree.js) in Python, to design a tree lesson's floor data offline.

Points are story rows `[x, z, class, ...]`. Thresholds are midpoints between neighbouring distinct values, and a
split must beat the best so far by more than 1e-12 with x tried before z: the game's own tie-break, so the splits
these functions return are the ones `tree.bestSplit` and `tree.grow` make on the same points.
"""


def gini(counts):
    """Gini impurity 1 - sum p_k^2 of a list of class counts (0 for an empty node)."""
    n = sum(counts)
    return 1 - sum((c / n) ** 2 for c in counts) if n else 0


def class_counts(points, classes):
    """The number of points of each class 0..classes-1."""
    c = [0] * classes
    for p in points:
        c[p[2]] += 1
    return c


def thresholds(points, feature):
    """The candidate thresholds on feature 0 (x) or 1 (z): midpoints between neighbouring distinct values."""
    v = sorted(set(p[feature] for p in points))
    return [(a + b) / 2 for a, b in zip(v, v[1:])]


def split_score(points, feature, threshold, classes):
    """Weighted Gini of the two sides of `feature <= threshold` (the game's split score; lower is purer)."""
    left = [p for p in points if p[feature] <= threshold]
    right = [p for p in points if p[feature] > threshold]
    return (len(left) * gini(class_counts(left, classes)) + len(right) * gini(class_counts(right, classes))) / len(points)


def best_split(points, classes):
    """The game's best split of a node: (feature 0 or 1, threshold, gain), or None when no split lowers the Gini."""
    parent = gini(class_counts(points, classes))
    best = None
    for f in (0, 1):
        for t in thresholds(points, f):
            gain = parent - split_score(points, f, t, classes)
            if gain > 1e-12 and (best is None or gain > best[2] + 1e-12):
                best = (f, t, gain)
    return best


def grow(points, classes, max_depth=3, min_size=2):
    """The splits of the game's greedy tree to `max_depth`, parents first: [(node id 'n0', 'n0L', …, 'x' or 'z', threshold)]."""
    def rec(pts, depth, node):
        if depth >= max_depth or len(pts) < min_size or gini(class_counts(pts, classes)) == 0:
            return []
        s = best_split(pts, classes)
        if not s:
            return []
        f, t, _ = s
        return ([(node, "xz"[f], t)]
                + rec([p for p in pts if p[f] <= t], depth + 1, node + "L")
                + rec([p for p in pts if p[f] > t], depth + 1, node + "R"))
    return rec(points, 0, "n0")
