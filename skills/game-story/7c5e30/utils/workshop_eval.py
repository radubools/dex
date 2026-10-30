"""Model Workshop's evaluation maths in Python (splits, folds, metrics, the station polynomial, logistic fit), line for line with the game's JS.

Mirrors <game>/src/ml/metrics.js (accuracy, confusion, precisionRecallF1,
the seeded shuffle, trainValTestSplit, kFolds), src/ml/logistic.js
(logisticFit) and src/actions/evaluation.js (ridgePolyFit and polyValue on
u = x/5 with the 1e-9 ridge, the train.curve sweep, eval.crossValidate's
per-fold MSE). Points are dicts with x, z, cls and y as the game names them.
Underdetermined polynomials (more coefficients than points) agree with the game
to about 1e-7 only; compare those as the game prints them.
"""
from __future__ import annotations

import math

from .rng import make_rng
from .workshop_math import solve

XS = 5  # evaluation.js scales x to x / 5 before taking powers


def js_round(v: float) -> int:
    """JS Math.round: halves round up (Python's round() goes to even)."""
    return math.floor(v + 0.5)


# ------------------------------------------------------------ metrics.js

def confusion(y_true, y_pred, classes: int = 2) -> list[list[int]]:
    """Counts m[true][pred], as metrics.js's confusion."""
    m = [[0] * classes for _ in range(classes)]
    for t, p in zip(y_true, y_pred):
        m[t][p] += 1
    return m


def precision_recall_f1(m, positive: int = 1) -> tuple[float, float, float]:
    """(precision, recall, F1) of one class from a confusion matrix, 0 when undefined, as precisionRecallF1."""
    tp = m[positive][positive]
    predicted = sum(row[positive] for row in m)
    actual = sum(m[positive])
    precision = tp / predicted if predicted else 0
    recall = tp / actual if actual else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    return precision, recall, f1


def accuracy(y_true, y_pred) -> float:
    """Share of matching labels, 0 for none, as metrics.js's accuracy."""
    return sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true) if y_true else 0


def shuffled(items, seed: int) -> list:
    """The game's seeded Fisher–Yates shuffle (makeRng(seed)), same order as metrics.js."""
    rng = make_rng(seed)
    a = list(items)
    for i in range(len(a) - 1, 0, -1):
        j = math.floor(rng() * (i + 1))
        a[i], a[j] = a[j], a[i]
    return a


def train_val_test_split(items, train: float = 0.6, val: float = 0.2, test: float = 0.2, seed: int = 1) -> tuple[list, list, list]:
    """(train, val, test) as eval.split draws them: rounded shares of the shuffle, the test takes the rest."""
    a = shuffled(items, seed)
    total = train + val + test
    n_train = js_round(len(a) * train / total)
    n_val = js_round(len(a) * val / total)
    return a[:n_train], a[n_train:n_train + n_val], a[n_train + n_val:]


def split_points(items, train: float = 0.6, val: float = 0.2, test: float = 0.2, seed: int = 1,
                 stratify: bool = False) -> tuple[list, list, list]:
    """(train, val, test) of point dicts exactly as eval.split draws them (evaluation.js splitPoints): stratified splits each "cls" with seed + 101·i, then reshuffles each group."""
    if not stratify:
        return train_val_test_split(items, train, val, test, seed=seed)
    groups: dict[str, list] = {}
    for p in items:
        key = "" if p.get("cls") is None else str(p["cls"])
        groups.setdefault(key, []).append(p)
    out = ([], [], [])
    for i, key in enumerate(sorted(groups)):
        for acc, part in zip(out, train_val_test_split(groups[key], train, val, test, seed=seed + 101 * i)):
            acc.extend(part)
    return tuple(train_val_test_split(g, 1, 0, 0, seed=seed)[0] for g in out)


def class_counts(train, val, test) -> dict:
    """Points per class in each group, as eval.split's `counts`: {cls: {"train", "val", "test"}} over labelled point dicts."""
    out: dict = {}
    for role, group in zip(("train", "val", "test"), (train, val, test)):
        for p in group:
            if p.get("cls") is None:
                continue
            out.setdefault(p["cls"], {"train": 0, "val": 0, "test": 0})[role] += 1
    return out


def k_folds(items, k: int, seed: int = 1) -> list[tuple[list, list]]:
    """[(val, train)] per fold: fold f holds the shuffled positions i with i % k == f, as kFolds."""
    a = shuffled(items, seed)
    folds = [[p for i, p in enumerate(a) if i % k == f] for f in range(k)]
    return [(val, [p for g, fold in enumerate(folds) if g != f for p in fold]) for f, val in enumerate(folds)]


# ------------------------------------------- evaluation.js, the polynomial

def ridge_poly_fit(points, degree: int, lam: float = 0.0) -> list[float]:
    """Coefficients of (x/5)^k by ridge least squares (intercept unpenalised, 1e-9 on the diagonal), as ridgePolyFit."""
    m = max(0, js_round(degree)) + 1
    A = [[0.0] * m for _ in range(m)]
    r = [0.0] * m
    for p in points:
        u = p["x"] / XS
        pw = [1.0]
        for k in range(1, m):
            pw.append(pw[k - 1] * u)
        for i in range(m):
            r[i] += pw[i] * p["y"]
            for j in range(m):
                A[i][j] += pw[i] * pw[j]
    for i in range(m):
        A[i][i] += (max(0, lam) if i else 0) + 1e-9
    return solve(A, r)


def poly_value(coefs, x: float) -> float:
    """The station polynomial at x (Horner on u = x/5), as polyValue."""
    u = x / XS
    y = 0.0
    for c in reversed(coefs):
        y = y * u + c
    return y


def mse_of(coefs, points) -> float:
    """Mean squared error of the polynomial over points (0 for none)."""
    return sum((poly_value(coefs, p["x"]) - p["y"]) ** 2 for p in points) / (len(points) or 1)


def poly_sweep(train, val, max_deg: int = 11, lam: float = 0.0) -> list[tuple[float, float]]:
    """[(train MSE, val MSE)] for degrees 0..max_deg fitted on train: train.curve's sweep."""
    out = []
    for d in range(max_deg + 1):
        c = ridge_poly_fit(train, d, lam)
        out.append((mse_of(c, train), mse_of(c, val)))
    return out


def cross_validate(pool, folds: int, degree: int, lam: float = 0.0, seed: int = 1) -> tuple[list[tuple[list, list, float]], float, float]:
    """eval.crossValidate on heights: ([(val ids, coefs, fold MSE)], mean, population sd) over the clamped k folds."""
    k = max(2, min(js_round(folds) or 5, min(10, len(pool))))
    rows = []
    for val, train in k_folds(pool, k, seed):
        c = ridge_poly_fit(train, degree, lam)
        rows.append(([p["id"] for p in val], c, mse_of(c, val)))
    mean = sum(r[2] for r in rows) / len(rows)
    sd = math.sqrt(sum((r[2] - mean) ** 2 for r in rows) / len(rows))
    return rows, mean, sd


# ----------------------------------------------------------- logistic.js

def sigmoid(t: float) -> float:
    """1 / (1 + e^−t)."""
    return 1 / (1 + math.exp(-t))


def logistic_fit(points, lr: float = 0.5, steps: int = 600) -> tuple[float, float, float]:
    """(w1, w2, b) after batch gradient steps on log-loss from zero, as logisticFit (the game's first fit uses lr 0.5, 600 steps)."""
    w1 = w2 = b = 0.0
    n = len(points)
    for _ in range(steps):
        g1 = g2 = gb = 0.0
        for p in points:
            e = sigmoid(w1 * p["x"] + w2 * p["z"] + b) - (1 if p["cls"] == 1 else 0)
            g1 += e * p["x"]
            g2 += e * p["z"]
            gb += e
        w1, w2, b = w1 - lr * g1 / n, w2 - lr * g2 / n, b - lr * gb / n
    return w1, w2, b


def logistic_prob(m, p) -> float:
    """p(class 1) of point p under model (w1, w2, b)."""
    return sigmoid(m[0] * p["x"] + m[1] * p["z"] + m[2])
