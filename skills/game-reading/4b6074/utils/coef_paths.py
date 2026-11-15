"""Coefficient paths of linear-model fitting procedures (LAR, forward stepwise, forward stagewise, incremental stagewise) and their L1 arc length, numpy only; the lasso path is lasso_path.lasso_path."""
import numpy as np


def standardize(X, y):
    """Centre the columns of X and scale them to unit norm (ESL Algorithm 3.2, step 1), centre y; returns (X, y, column norms)."""
    X = X - X.mean(0)
    norms = np.linalg.norm(X, axis=0)
    return X / norms, y - y.mean(), norms


def lar_path(X, y, tol=1e-9):
    """Breakpoints of least angle regression (ESL Algorithm 3.2) on unit-norm X: (betas (steps+1, p), entered variable per breakpoint, None at the end)."""
    N, p = X.shape
    beta = np.zeros(p)
    active = [int(np.argmax(np.abs(X.T @ y)))]
    betas, entered = [beta.copy()], [active[0]]
    while True:
        r = y - X @ beta
        c = X.T @ r
        C = np.abs(c[active]).max()
        XA = X[:, active]
        delta = np.linalg.solve(XA.T @ XA, XA.T @ r)        # step direction (3.55)
        a = X.T @ (XA @ delta)
        alpha, enter = 1.0, None
        if len(active) < min(N - 1, p):
            for j in range(p):                              # look-ahead: first inactive variable to tie
                if j in active:
                    continue
                for num, den in ((C - c[j], C - a[j]), (C + c[j], C + a[j])):
                    if abs(den) > 1e-14 and tol < num / den < alpha:
                        alpha, enter = num / den, j
        beta = beta.copy()
        beta[active] += alpha * delta
        betas.append(beta)
        entered.append(enter)
        if enter is None:
            return np.array(betas), entered
        active.append(enter)


def forward_stepwise_path(X, y, steps=None):
    """Forward stepwise selection (ESL Section 3.3.2): least-squares fits on a growing active set; returns (betas (steps+1, p), entry order)."""
    N, p = X.shape
    steps = min(p, N - 1) if steps is None else steps
    active, betas = [], [np.zeros(p)]
    for _ in range(steps):
        best, best_rss, best_b = None, np.inf, None
        for j in range(p):
            if j in active:
                continue
            A = active + [j]
            b = np.linalg.lstsq(X[:, A], y, rcond=None)[0]
            rss = float(np.sum((y - X[:, A] @ b) ** 2))
            if rss < best_rss:
                best, best_rss, best_b = j, rss, b
        active.append(best)
        beta = np.zeros(p)
        beta[active] = best_b
        betas.append(beta)
    return np.array(betas), active


def forward_stagewise_path(X, y, max_steps=4000, tol=1e-6):
    """Forward stagewise (ESL Section 3.3.3) on unit-norm X: each step adds <x_j, r> to the most correlated variable's coefficient; betas per step."""
    beta = np.zeros(X.shape[1])
    r = y.copy()
    betas = [beta.copy()]
    for _ in range(max_steps):
        c = X.T @ r
        j = int(np.argmax(np.abs(c)))
        if abs(c[j]) < tol:
            break
        beta = beta.copy()
        beta[j] += c[j]
        r = r - c[j] * X[:, j]
        betas.append(beta)
    return np.array(betas)


def incremental_stagewise_path(X, y, eps=0.01, max_steps=60000, tol=None, keep_every=20):
    """Incremental forward stagewise FS_eps (ESL Algorithm 3.4) on unit-norm X: steps of eps toward the top correlation's sign; every keep_every-th beta."""
    tol = eps if tol is None else tol
    beta = np.zeros(X.shape[1])
    r = y.copy()
    betas = [beta.copy()]
    for n in range(max_steps):
        c = X.T @ r
        j = int(np.argmax(np.abs(c)))
        if abs(c[j]) < tol:
            break
        d = eps * np.sign(c[j])
        beta[j] += d
        r = r - d * X[:, j]
        if n % keep_every == 0:
            betas.append(beta.copy())
    betas.append(beta.copy())
    return np.array(betas)


def arc_length(betas):
    """Cumulative L1 arc length at each row of a piecewise-linear coefficient path (ESL footnote 2, p. 74)."""
    betas = np.asarray(betas)
    return np.concatenate([[0.0], np.cumsum(np.abs(np.diff(betas, axis=0)).sum(1))])


def densify(betas, per_segment=60):
    """Points along a piecewise-linear path, per_segment per segment: (arc lengths, coefficient rows)."""
    betas = np.asarray(betas)
    s = arc_length(betas)
    S, B = [], []
    for i in range(len(betas) - 1):
        for t in np.linspace(0, 1, per_segment, endpoint=False):
            S.append(s[i] + t * (s[i + 1] - s[i]))
            B.append(betas[i] + t * (betas[i + 1] - betas[i]))
    S.append(s[-1])
    B.append(betas[-1])
    return np.array(S), np.array(B)


def at_fraction(betas, fracs):
    """Coefficients at the given fractions of a path's total L1 arc length, linear between rows: array (len(fracs), p)."""
    betas = np.asarray(betas)
    s = arc_length(betas)
    s = s / s[-1]
    return np.array([[np.interp(f, s, betas[:, j]) for j in range(betas.shape[1])] for f in fracs])
