"""Nearest-neighbour training-set reduction (ESL 13.5), numpy only: the 1-NN rule, Devijver-Kittler multi-edit and Hart's condensing."""
import numpy as np


def nn1(train_x, train_y, query):
    """Labels of the `query` rows under the 1-nearest-neighbour rule (Euclidean) on (train_x, train_y)."""
    d = ((query[:, None, :] - train_x[None, :, :]) ** 2).sum(-1)
    return train_y[d.argmin(1)]


def multi_edit(x, y, rng, s=3, quiet=10):
    """Indices kept by multi-edit: random split into s parts, part i classified by 1-NN on part (i+1) mod s, misclassified points deleted, until `quiet` rounds in a row delete nothing."""
    keep = np.arange(len(y))
    calm = 0
    while calm < quiet and len(keep) > s:
        parts = np.array_split(rng.permutation(keep), s)
        drop = []
        for i in range(s):
            test, train = parts[i], parts[(i + 1) % s]
            wrong = nn1(x[train], y[train], x[test]) != y[test]
            drop.extend(test[wrong])
        if drop:
            keep = np.setdiff1d(keep, drop)
            calm = 0
        else:
            calm += 1
    return keep


def condense(x, y, idx, rng):
    """Indices of Hart's condensed set from the points `idx`: start from one random point, add each point the current set misclassifies under 1-NN, repeat passes until one adds nothing."""
    order = rng.permutation(idx)
    store = [order[0]]
    changed = True
    while changed:
        changed = False
        for i in order:
            if i in store:
                continue
            if nn1(x[store], y[store], x[i:i + 1])[0] != y[i]:
                store.append(i)
                changed = True
    return np.array(store)
