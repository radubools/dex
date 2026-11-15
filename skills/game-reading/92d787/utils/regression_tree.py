"""A least-squares regression tree (CART growing, ESL 9.2.2), numpy only, whose leaves keep their training responses."""
import numpy as np


def tree_fit(X, y, depth=3, min_leaf=5):
    """Grow a tree on rows X, responses y by exhaustive squared-error splits: nested dicts {j, s, l, r}, leaves {leaf: y values}."""
    X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
    if depth == 0 or len(y) < 2 * min_leaf:
        return {"leaf": y}
    best = None
    for j in range(X.shape[1]):
        o = np.argsort(X[:, j])
        xs, ys = X[o, j], y[o]
        cs, cs2 = np.cumsum(ys), np.cumsum(ys ** 2)
        for i in range(min_leaf, len(y) - min_leaf + 1):
            if xs[i - 1] == xs[i]:
                continue
            nl, nr = i, len(y) - i
            sl, sr = cs[i - 1], cs[-1] - cs[i - 1]
            sse = cs2[-1] - sl ** 2 / nl - sr ** 2 / nr
            if best is None or sse < best[0]:
                best = (sse, j, (xs[i - 1] + xs[i]) / 2)
    if best is None:
        return {"leaf": y}
    _, j, s = best
    left = X[:, j] <= s
    return {"j": j, "s": s, "l": tree_fit(X[left], y[left], depth - 1, min_leaf),
            "r": tree_fit(X[~left], y[~left], depth - 1, min_leaf)}


def tree_leaf(tree, x):
    """The training responses in the terminal node row x falls into (x[j] <= s goes left): take .mean() to predict, sample to draw."""
    while "leaf" not in tree:
        tree = tree["l"] if x[tree["j"]] <= tree["s"] else tree["r"]
    return tree["leaf"]
