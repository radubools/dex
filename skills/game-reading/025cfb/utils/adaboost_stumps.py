"""AdaBoost.M1 (ESL Algorithm 10.1) with decision stumps, numpy only: exact weighted stump search and staged train/test error."""
import numpy as np


def best_stump(x, y, w, order=None):
    """The stump s * sign(x_j - t) of least weighted error on rows x, labels y in {-1, 1}, weights w: (err, j, t, s); order = np.argsort(x, axis=0) if precomputed."""
    if order is None:
        order = np.argsort(x, axis=0)
    best = (np.inf, 0, 0.0, 1)
    total = w.sum()
    for j in range(x.shape[1]):
        o = order[:, j]
        wy = (w * y)[o]
        # Predicting +1 to the right of a cut after position i: errors are the left +1s and the right -1s.
        pos_left = np.cumsum(np.where(wy > 0, wy, 0.0))
        neg_left = np.cumsum(np.where(wy < 0, -wy, 0.0))
        neg_total = neg_left[-1]
        err_plus = np.r_[neg_total, pos_left + (neg_total - neg_left)]
        for s, err in ((1, err_plus), (-1, total - err_plus)):
            i = int(np.argmin(err))
            if err[i] < best[0]:
                xs = x[o, j]
                if i == 0:
                    t = xs[0] - 1e-9
                elif i == len(xs):
                    t = xs[-1] + 1e-9
                else:
                    t = 0.5 * (xs[i - 1] + xs[i])
                best = (float(err[i]), j, float(t), s)
    return best


def adaboost(x, y, xt, yt, M=400, checkpoints=None):
    """AdaBoost.M1 with stumps for M rounds: {m, train, test} misclassification rates at each checkpoint (default every round)."""
    keep = set(range(1, M + 1)) if checkpoints is None else set(checkpoints)
    order = np.argsort(x, axis=0)
    w = np.full(len(y), 1.0 / len(y))
    F, Ft = np.zeros(len(y)), np.zeros(len(yt))
    out = {"m": [], "train": [], "test": []}
    for m in range(1, M + 1):
        err, j, t, s = best_stump(x, y, w, order)
        err = max(err / w.sum(), 1e-12)
        alpha = np.log((1 - err) / err)
        g = s * np.where(x[:, j] > t, 1, -1)
        F += alpha * g
        Ft += alpha * s * np.where(xt[:, j] > t, 1, -1)
        w = w * np.exp(alpha * (g != y))
        w /= w.sum()
        if m in keep:
            out["m"].append(m)
            out["train"].append(float(np.mean(np.sign(F) != y)))
            out["test"].append(float(np.mean(np.sign(Ft) != yt)))
    return out
