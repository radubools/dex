"""Random-forest proximities (ESL 15.3.3) and classical MDS, as numpy arrays, for proximity-plot and MDS lessons."""
import numpy as np


def forest_proximity(X, y, *, trees=500, max_features=1, seed=0):
    """N x N out-of-bag proximity of a scikit-learn random forest on (X, y): the share of trees, among those where both points are out of bag, that put them in one leaf (diagonal 1)."""
    from sklearn.ensemble import RandomForestClassifier
    X = np.asarray(X, float)
    y = np.asarray(y)
    f = RandomForestClassifier(n_estimators=trees, max_features=max_features, random_state=seed).fit(X, y)
    leaves = f.apply(X)
    n = len(X)
    same = np.zeros((n, n))
    both = np.zeros((n, n))
    for t, idx in enumerate(f.estimators_samples_):
        oob = np.ones(n, bool)
        oob[idx] = False
        o = np.where(oob)[0]
        lv = leaves[o, t]
        both[np.ix_(o, o)] += 1
        same[np.ix_(o, o)] += (lv[:, None] == lv[None, :])
    prox = np.where(both > 0, same / np.maximum(both, 1), 0.0)
    np.fill_diagonal(prox, 1.0)
    return prox


def classical_mds(d, k=2):
    """Classical (Torgerson) MDS: an N x k array of coordinates whose distances best match the N x N dissimilarity matrix d."""
    d = np.asarray(d, float)
    n = len(d)
    J = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * J @ (d ** 2) @ J
    w, v = np.linalg.eigh(B)
    order = np.argsort(-w)[:k]
    return v[:, order] * np.sqrt(np.maximum(w[order], 0))
