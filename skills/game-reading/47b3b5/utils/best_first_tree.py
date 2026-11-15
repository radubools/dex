"""Best-first least-squares regression trees (CART cuts, ESL 9.2.2), numpy only: fitted values at every leaf count up to a maximum."""
import numpy as np


def best_split(X, y, idx, min_leaf=5):
    """The best squared-error cut of rows `idx` of X: (RSS decrease, j, s, left idx, right idx), or None if no cut keeps min_leaf a side."""
    yy = y[idx]
    n = len(idx)
    if n < 2 * min_leaf:
        return None
    base = ((yy - yy.mean()) ** 2).sum()
    best = None
    for j in range(X.shape[1]):
        o = np.argsort(X[idx, j], kind="stable")
        xs, ys = X[idx, j][o], yy[o]
        cs, cs2 = np.cumsum(ys), np.cumsum(ys ** 2)
        i = np.arange(min_leaf, n - min_leaf + 1)  # the left side holds the first i sorted points
        sl, sr = cs[i - 1], cs[-1] - cs[i - 1]
        rss = cs2[-1] - sl ** 2 / i - sr ** 2 / (n - i)
        rss[xs[i - 1] == xs[i % n]] = np.inf  # tied values cannot be separated
        k = int(np.argmin(rss))
        if np.isfinite(rss[k]) and (best is None or rss[k] < best[0]):
            cut = i[k]
            best = (rss[k], j, (xs[cut - 1] + xs[cut]) / 2, idx[o[:cut]], idx[o[cut:]])
    if best is None:
        return None
    return (base - best[0],) + best[1:]


def grow_fits(X, y, max_leaves=10, min_leaf=5):
    """Fitted values of the tree grown best first (largest RSS decrease anywhere goes next) at 1..max_leaves leaves: array (leaves, N); stops early if no cut is left."""
    X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
    leaves = [np.arange(len(y))]
    cands = [best_split(X, y, leaves[0], min_leaf)]
    fits = []
    for _ in range(max_leaves):
        f = np.empty(len(y))
        for leaf in leaves:
            f[leaf] = y[leaf].mean()
        fits.append(f)
        k = max(range(len(leaves)), key=lambda q: -np.inf if cands[q] is None else cands[q][0])
        if cands[k] is None:
            break
        _, _, _, left, right = cands[k]
        leaves[k:k + 1] = [left, right]
        cands[k:k + 1] = [best_split(X, y, left, min_leaf), best_split(X, y, right, min_leaf)]
    return np.array(fits)
