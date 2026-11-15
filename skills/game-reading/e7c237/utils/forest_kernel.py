"""A fitted random forest as a weighted nearest-neighbour rule (ESL 15.4.3): the weight each training point gets at a target point."""
import numpy as np


def forest_weights(forest, X, x0):
    """Weights w_i(x0) of the training rows X under a fitted sklearn forest (bootstrap=True): per tree, in-bag count in x0's leaf over the leaf's in-bag total, averaged over trees; they sum to 1 and sum(w * y) is the forest's prediction."""
    X = np.asarray(X, float)
    x0 = np.asarray(x0, float).reshape(1, -1)
    w = np.zeros(len(X))
    for tree, idx in zip(forest.estimators_, forest.estimators_samples_):
        counts = np.bincount(idx, minlength=len(X)).astype(float)
        inbag = counts * (tree.apply(X) == tree.apply(x0)[0])
        w += inbag / inbag.sum()
    return w / len(forest.estimators_)
