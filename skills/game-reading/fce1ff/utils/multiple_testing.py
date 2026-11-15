"""Multiple testing with permutation nulls (ESL 18.7), numpy only: two-sample t, pooled p-values, BH, plug-in FDR, pi0, SAM cuts."""
import numpy as np


def two_sample_t(X, g2):
    """Two-sample t-statistics (ESL 18.38-18.39), pooled SE, of every row of X (genes x samples) for each 0/1 column of g2 (samples x B) marking group 2: (genes, B)."""
    X = np.asarray(X, float)
    g2 = np.asarray(g2, float)
    g1 = 1.0 - g2
    n2 = g2.sum(axis=0)
    n1 = g1.sum(axis=0)
    m1, m2 = (X @ g1) / n1, (X @ g2) / n2
    ss = ((X ** 2) @ g1 - n1 * m1 ** 2) + ((X ** 2) @ g2 - n2 * m2 ** 2)
    sigma = np.sqrt(ss / (n1 + n2 - 2))
    return (m2 - m1) / (sigma * np.sqrt(1 / n1 + 1 / n2))


def random_labellings(rng, n, n2, K):
    """K random relabellings of n samples with n2 in group 2, as an (n, K) 0/1 matrix for two_sample_t."""
    g = np.zeros((n, K))
    for k in range(K):
        g[rng.choice(n, n2, replace=False), k] = 1
    return g


def pooled_pvalues(t, tk):
    """Pooled permutation p-values (ESL 18.41): for each t_j, the share of all permuted |t| (array tk, any shape) strictly above |t_j|."""
    null = np.sort(np.abs(np.asarray(tk)).ravel())
    return 1.0 - np.searchsorted(null, np.abs(np.asarray(t)), side="right") / null.size


def bh_count(p, alpha):
    """Benjamini-Hochberg L (ESL 18.44): the largest j with p_(j) < alpha j / M, 0 if none; reject the L smallest p-values."""
    ps = np.sort(np.asarray(p))
    below = np.flatnonzero(ps < alpha * np.arange(1, ps.size + 1) / ps.size)
    return int(below[-1] + 1) if below.size else 0


def plugin_fdr(t, tk, C):
    """Plug-in FDR (ESL Algorithm 18.3) at cut C on |t|, counting values at or above C: (R_obs, E(V) hat, FDR hat or nan if R_obs = 0); tk is (M, K)."""
    tk = np.asarray(tk)
    R = int((np.abs(t) >= C).sum())
    EV = float((np.abs(tk) >= C).sum() / tk.shape[1])
    return R, EV, (EV / R if R else float("nan"))


def pi0_hat(t, tk):
    """Storey's share of true nulls (ESL Ex. 18.19c): #{t_j in (q.25, q.75) of the permuted t} / (0.5 M), capped at 1."""
    q25, q75 = np.quantile(tk, [0.25, 0.75])
    t = np.asarray(t)
    return min(float(((t > q25) & (t < q75)).sum() / (0.5 * t.size)), 1.0)


def expected_order_stats(tk):
    """SAM's expected order statistics: each permutation column of tk (M, K) sorted, then averaged across permutations."""
    return np.sort(np.asarray(tk), axis=0).mean(axis=1)


def sam_cuts(ts, tt, delta):
    """SAM cut-points (ESL 18.7.2) for sorted observed ts against expected tt and band delta: (C_low, C_hi, last called index below, first called index above); None/inf when a side calls nothing."""
    d = np.asarray(ts) - np.asarray(tt)
    zero = int(np.searchsorted(tt, 0.0))
    up = np.flatnonzero(d[zero:] > delta)
    lo = np.flatnonzero(d[:zero][::-1] < -delta)
    hi_i = zero + int(up[0]) if up.size else None
    lo_i = zero - 1 - int(lo[0]) if lo.size else None
    return (ts[lo_i] if lo_i is not None else -np.inf, ts[hi_i] if hi_i is not None else np.inf, lo_i, hi_i)
