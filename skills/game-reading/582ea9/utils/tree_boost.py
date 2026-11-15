"""Small gradient tree boosting (ESL Algorithms 10.3 and 10.4) with partial dependence by weighted tree traversal (Ex. 10.11) and by averaging (10.48), numpy only."""
import numpy as np


def bin_edges(X, nbins=64):
    """Per column, the candidate split values: up to nbins - 1 distinct quantiles; x <= edge goes left."""
    qs = np.linspace(0, 1, nbins + 1)[1:-1]
    return [np.unique(np.quantile(X[:, j], qs)) for j in range(X.shape[1])]


def binned(X, edges):
    """Bin index of every value: the number of edges strictly below it, so bin <= k iff x <= edges[k]."""
    return np.column_stack([np.searchsorted(e, X[:, j], side="left") for j, e in enumerate(edges)])


def _best_split(B, r, idx, nbins, min_leaf):
    rr, n = r[idx], len(idx)
    S = rr.sum()
    best = (0.0, None, None)
    for j in range(B.shape[1]):
        b = B[idx, j]
        cnt = np.bincount(b, minlength=nbins[j])
        sm = np.bincount(b, weights=rr, minlength=nbins[j])
        cl, sl = np.cumsum(cnt)[:-1], np.cumsum(sm)[:-1]
        cr, sr = n - cl, S - sl
        ok = (cl >= min_leaf) & (cr >= min_leaf)
        if not ok.any():
            continue
        gain = np.full(len(cl), -np.inf)
        gain[ok] = sl[ok] ** 2 / cl[ok] + sr[ok] ** 2 / cr[ok] - S ** 2 / n
        k = int(np.argmax(gain))
        if gain[k] > best[0]:
            best = (float(gain[k]), j, k)
    return best


def grow_tree(B, r, edges, J, leaf_value, min_leaf=10):
    """A least-squares regression tree on residuals r grown best-first to J terminal nodes (as MART does).

    Returns a dict of node arrays: feat (-1 at leaves), thr, left, right, value, count, gain.
    leaf_value(idx) gives a terminal node's constant from the training rows idx that reach it.
    """
    nbins = [len(e) + 1 for e in edges]
    nodes = [dict(idx=np.arange(len(r)), feat=-1, thr=np.nan, left=-1, right=-1, gain=0.0)]
    cand = {0: _best_split(B, r, nodes[0]["idx"], nbins, min_leaf)}
    leaves = 1
    while leaves < J and cand:
        t = max(cand, key=lambda i: cand[i][0])
        g, j, k = cand.pop(t)
        if j is None or g <= 0:
            break
        idx = nodes[t]["idx"]
        go = B[idx, j] <= k
        nodes[t].update(feat=j, thr=float(edges[j][k]), gain=g, left=len(nodes), right=len(nodes) + 1)
        for part in (idx[go], idx[~go]):
            nodes.append(dict(idx=part, feat=-1, thr=np.nan, left=-1, right=-1, gain=0.0))
            cand[len(nodes) - 1] = _best_split(B, r, part, nbins, min_leaf)
        leaves += 1
    value = np.array([leaf_value(nd["idx"]) if nd["feat"] < 0 else 0.0 for nd in nodes])
    return dict(feat=np.array([nd["feat"] for nd in nodes]), thr=np.array([nd["thr"] for nd in nodes]),
                left=np.array([nd["left"] for nd in nodes]), right=np.array([nd["right"] for nd in nodes]),
                count=np.array([len(nd["idx"]) for nd in nodes]), gain=np.array([nd["gain"] for nd in nodes]),
                value=value)


def tree_predict(tree, X):
    """The tree's value at every row of X (raw values; x <= thr goes left)."""
    return tree["value"][_leaf_of(tree, X)]


def huber_boost(X, y, *, J=6, nu=0.1, M=800, alpha=0.9, edges=None, X_test=None, y_test=None):
    """Gradient boosting with Huber loss (Algorithm 10.3; delta the alpha-quantile of |residual| each step).

    Returns (model, train AAE per iteration, test AAE per iteration); model = {f0, trees}.
    """
    edges = edges or bin_edges(X)
    B = binned(X, edges)
    f0 = float(np.median(y))
    f = np.full(len(y), f0)
    ft = None if X_test is None else np.full(len(X_test), f0)
    trees, tr, te = [], [], []
    for _ in range(M):
        res = y - f
        delta = np.quantile(np.abs(res), alpha)
        g = np.where(np.abs(res) <= delta, res, delta * np.sign(res))

        def leaf(idx):
            rr = res[idx]
            med = np.median(rr)
            d = rr - med
            return nu * (med + np.mean(np.sign(d) * np.minimum(delta, np.abs(d))))

        t = grow_tree(B, g, edges, J, leaf)
        trees.append(t)
        f += tree_predict(t, X)
        tr.append(np.mean(np.abs(y - f)))
        if ft is not None:
            ft += tree_predict(t, X_test)
            te.append(np.mean(np.abs(y_test - ft)))
    return dict(f0=f0, trees=trees), np.array(tr), np.array(te)


def _leaf_of(tree, X):
    node = np.zeros(len(X), dtype=int)
    while True:
        inner = tree["feat"][node] >= 0
        if not inner.any():
            return node
        i = np.nonzero(inner)[0]
        nd = node[i]
        left = X[i, tree["feat"][nd]] <= tree["thr"][nd]
        node[i] = np.where(left, tree["left"][nd], tree["right"][nd])


def softmax(F):
    """Row-wise class probabilities (10.21) from an (N, K) array of f_k."""
    E = np.exp(F - F.max(axis=1, keepdims=True))
    return E / E.sum(axis=1, keepdims=True)


def multiclass_boost(X, g, K, *, J=6, nu=0.1, M=300, X_test=None, g_test=None):
    """K-class gradient boosting with multinomial deviance (Algorithm 10.4); classes g in 0..K-1.

    Returns (models, test error per iteration); models[k] = {f0: 0, trees} is f_k.
    """
    edges = bin_edges(X)
    B = binned(X, edges)
    Y = np.eye(K)[g]
    F = np.zeros((len(g), K))
    Ft = None if X_test is None else np.zeros((len(X_test), K))
    models = [dict(f0=0.0, trees=[]) for _ in range(K)]
    err = []
    for _ in range(M):
        P = softmax(F)
        for k in range(K):
            r = Y[:, k] - P[:, k]

            def leaf(idx, r=r):
                a = np.abs(r[idx])
                den = np.sum(a * (1 - a))
                return nu * (K - 1) / K * (r[idx].sum() / den if den > 1e-12 else 0.0)

            t = grow_tree(B, r, edges, J, leaf)
            models[k]["trees"].append(t)
            F[:, k] += tree_predict(t, X)
            if Ft is not None:
                Ft[:, k] += tree_predict(t, X_test)
        if Ft is not None:
            err.append(np.mean(Ft.argmax(axis=1) != g_test))
    return models, np.array(err)


def predict(model, X, M=None):
    """f_M(x) = f0 + the sum of the first M trees (all by default) at every row of X."""
    return model["f0"] + sum(tree_predict(t, X) for t in model["trees"][:M])


def tree_partial_dependence(tree, S, G):
    """Ex. 10.11: one tree's partial dependence on the columns S at the rows of G (len(S) columns), by weighted traversal.

    At a split on a variable in S the weight follows the branch G selects; at a split on a variable
    in C it is divided between both branches in proportion to the training counts there.
    """
    S = list(S)
    out = np.zeros(len(G))

    def walk(n, w):
        f = tree["feat"][n]
        if f < 0:
            out[:] += w * tree["value"][n]
            return
        l, r = tree["left"][n], tree["right"][n]
        if f in S:
            go = G[:, S.index(f)] <= tree["thr"][n]
            walk(l, w * go)
            walk(r, w * ~go)
        else:
            share = tree["count"][l] / tree["count"][n]
            walk(l, w * share)
            walk(r, w * (1 - share))

    walk(0, np.ones(len(G)))
    return out


def partial_dependence(model, S, G):
    """A boosted model's partial dependence on S at the rows of G: f0 plus the sum of its trees' (Ex. 10.11)."""
    return model["f0"] + sum(tree_partial_dependence(t, S, G) for t in model["trees"])


def average_dependence(fun, X, S, G):
    """(10.48): at each row of G, the average of fun over the rows of X with the columns S set to that row."""
    out = np.empty(len(G))
    Z = X.copy()
    for i, row in enumerate(G):
        Z[:, list(S)] = row
        out[i] = fun(Z).mean()
    return out


def importance(trees, p):
    """(10.42)-(10.43): relative importance of each of p inputs over the trees, square-rooted, largest 100."""
    I2 = np.zeros(p)
    for t in trees:
        for f, g in zip(t["feat"], t["gain"]):
            if f >= 0:
                I2[f] += g
    I = np.sqrt(I2 / len(trees))
    return 100 * I / I.max()
