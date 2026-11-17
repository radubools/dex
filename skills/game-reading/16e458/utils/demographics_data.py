"""ESL's demographic (marketing) data of Table 14.1 as complete cases, rule support/confidence/lift, and permuted (product-of-marginals) reference samples, numpy only."""
from pathlib import Path

import numpy as np

DATA = Path.home() / "dex/datasets/ml/eslr/marketing.data"

COLS = ["income", "sex", "marital", "age", "education", "occupation", "years", "dual",
        "household", "children", "householder", "home", "ethnic", "language"]
CATEGORICAL = {"sex", "marital", "occupation", "dual", "householder", "home", "ethnic", "language"}


def load(path=DATA):
    """Rows of marketing.data with no NA as an int array (rows, 14), columns in COLS order (6876 rows, as ESL 14.2.3 uses)."""
    rows = []
    for line in Path(path).read_text().split("\n"):
        f = line.split()
        if len(f) == 14 and "NA" not in f:
            rows.append([int(v) for v in f])
    return np.array(rows)


def col(X, name):
    """The column of X named `name` (one of COLS)."""
    return X[:, COLS.index(name)]


def rule_stats(A, B):
    """(support T(A=>B), confidence (ESL 14.8), lift) of the rule A => B from boolean masks over the same rows."""
    t = np.mean(A & B)
    conf = t / np.mean(A)
    return t, conf, conf / np.mean(B)


def permuted_reference(X, seed=0):
    """A sample from the product of the column marginals (ESL 14.15): each column of X permuted independently."""
    r = np.random.default_rng(seed)
    return np.stack([r.permutation(X[:, j]) for j in range(X.shape[1])], 1)
