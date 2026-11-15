"""A small MARS (ESL Section 9.4), numpy only: forward pass over reflected pairs, backward deletion, GCV.

Written to redraw the reading's figures from data; it follows the book's description, not Friedman's
full program (no linear-only terms, no minimum span, candidate knots optionally thinned to quantiles).
"""
import numpy as np


def hinge(x, t, s):
    """(x - t)+ for s = +1, (t - x)+ for s = -1."""
    return np.maximum(s * (x - t), 0.0)


def term_column(X, factors):
    """The basis function of a term: the product of its hinge factors [(j, t, s), ...] (1 for none)."""
    col = np.ones(len(X))
    for j, t, s in factors:
        col = col * hinge(X[:, j], t, s)
    return col


def candidate_knots(X, max_knots=None):
    """Per input, the knots to try: every distinct observed value, or at most `max_knots` quantiles of them."""
    knots = []
    for j in range(X.shape[1]):
        u = np.unique(X[:, j])
        if max_knots and len(u) > max_knots:
            u = np.unique(np.quantile(X[:, j], np.linspace(0, 1, max_knots + 2)[1:-1], method="nearest"))
        knots.append(u)
    return knots


def forward(X, y, *, max_terms=21, max_degree=2, knots=None, tol=1e-9):
    """The forward pass: from the constant, add the product of a model term with the reflected pair that
    most decreases the training RSS, all coefficients refitted, until `max_terms` basis functions.

    Returns a list of terms {factors, parent, pair} in order of entry (term 0 is the constant) and the
    number of pairs (knots) added.
    """
    N, p = X.shape
    knots = knots if knots is not None else candidate_knots(X)
    var = np.concatenate([np.full(len(k), j) for j, k in enumerate(knots)])
    kt = np.concatenate(knots)
    plus = np.maximum(X[:, var] - kt, 0.0)
    minus = np.maximum(kt - X[:, var], 0.0)

    terms = [{"factors": [], "parent": None, "pair": 0}]
    Q = np.ones((N, 1)) / np.sqrt(N)
    r = y - Q @ (Q.T @ y)
    pairs = 0
    while len(terms) < max_terms:
        best = (tol * (r @ r), None)
        for ip, parent in enumerate(terms):
            if len(parent["factors"]) >= max_degree:
                continue
            used = {j for j, _, _ in parent["factors"]}
            ok = ~np.isin(var, list(used))
            h = term_column(X, parent["factors"])[:, None]
            A, B = h * plus[:, ok], h * minus[:, ok]
            PA, PB = Q.T @ A, Q.T @ B
            na, nb = (A * A).sum(0), (B * B).sum(0)
            aa = na - (PA * PA).sum(0)
            bb = nb - (PB * PB).sum(0)
            ab = -(PA * PB).sum(0)          # A * B = 0 elementwise: the pair's supports are disjoint
            ra, rb = r @ A, r @ B
            good_a = aa > 1e-10 * np.maximum(na, 1e-300)
            good_b = bb > 1e-10 * np.maximum(nb, 1e-300)
            red_a = np.where(good_a, ra ** 2 / np.where(good_a, aa, 1), 0.0)
            red_b = np.where(good_b, rb ** 2 / np.where(good_b, bb, 1), 0.0)
            det = aa * bb - ab ** 2
            both = good_a & good_b & (det > 1e-10 * np.where(good_a & good_b, aa * bb, 1))
            safe = np.where(both, det, 1)
            red2 = np.where(both, (bb * ra ** 2 - 2 * ab * ra * rb + aa * rb ** 2) / safe, 0.0)
            red = np.maximum(red2, np.maximum(red_a, red_b))
            k = int(np.argmax(red))
            if red[k] > best[0]:
                idx = np.flatnonzero(ok)[k]
                best = (red[k], (ip, int(var[idx]), float(kt[idx]), bool(good_a[k]), bool(good_b[k])))
        if best[1] is None:
            break
        ip, j, t, ga, gb = best[1]
        pairs += 1
        for s, g in ((1, ga), (-1, gb)):
            if not g or len(terms) >= max_terms:
                continue
            f = terms[ip]["factors"] + [(j, t, s)]
            col = term_column(X, f)
            for _ in range(2):
                col = col - Q @ (Q.T @ col)
            nrm = np.linalg.norm(col)
            if nrm < 1e-8 * np.linalg.norm(term_column(X, f)):
                continue
            Q = np.column_stack([Q, col / nrm])
            terms.append({"factors": f, "parent": ip, "pair": pairs})
        r = y - Q @ (Q.T @ y)
    return terms, pairs


def basis(X, terms):
    """The N x M matrix of the terms' basis functions."""
    return np.column_stack([term_column(X, t["factors"]) for t in terms])


def gcv(rss, N, r, c=3.0):
    """GCV (9.20) for a model of r basis functions, M = r + c K with K = (r - 1)/2 knots (one per pair)."""
    M = r + c * (r - 1) / 2
    return rss / (1 - M / N) ** 2 if M < N else np.inf


def backward(B, y, c=3.0):
    """Backward deletion: drop, one at a time, the term (never the constant) whose removal least increases
    the RSS. Returns [{keep, beta, rss, gcv}] for every size from all terms down to the constant."""
    N, M = B.shape
    keep = list(range(M))
    path = []
    while True:
        Bk = B[:, keep]
        G = np.linalg.inv(Bk.T @ Bk)
        beta = G @ (Bk.T @ y)
        res = y - Bk @ beta
        rss = float(res @ res)
        path.append({"keep": list(keep), "beta": beta, "rss": rss, "gcv": gcv(rss, N, len(keep), c)})
        if len(keep) == 1:
            return path
        inc = beta[1:] ** 2 / np.diag(G)[1:]
        keep.pop(1 + int(np.argmin(inc)))


def fit(X, y, *, max_terms=21, max_degree=2, knots=None, c=3.0):
    """Forward pass, backward pass, and the GCV choice: {terms, path, best} (best indexes path)."""
    terms, _ = forward(X, y, max_terms=max_terms, max_degree=max_degree, knots=knots)
    path = backward(basis(X, terms), y, c=c)
    best = int(np.argmin([s["gcv"] for s in path]))
    return {"terms": terms, "path": path, "best": best}


def predict(X, terms, step):
    """Predictions of a backward-path model `step` = {keep, beta}."""
    return basis(X, [terms[i] for i in step["keep"]]) @ step["beta"]


def pair_rss_sweep(x, y):
    """RSS of y on the reflected pair at every knot t (every distinct x but the largest), knots moved from
    right to left with O(1) updates (ESL p. 325). The pair spans {1, x, (x - t)+}. Returns (knots, rss)."""
    o = np.argsort(-x, kind="stable")
    xs, ys = x[o], y[o]
    n = len(x)
    S1, Sx, Sy = n, xs.sum(), ys.sum()
    Sxx, Sxy, Syy = xs @ xs, xs @ ys, ys @ ys
    u = np.unique(xs)[::-1]                  # distinct values, largest first
    # right set: points with x >= t; sums of b = (x - t)+ over it
    nR = 0
    sRx = sRy = 0.0
    sb = sbb = sbx = sby = 0.0
    i = 0
    out_t, out_rss = [], []
    prev = u[0]
    for t in u:
        d = prev - t                         # knot moved left by d: b grows by d on the right set
        sbb += 2 * d * sb + nR * d * d
        sb += nR * d
        sbx += d * sRx
        sby += d * sRy
        while i < n and xs[i] >= t:          # points at the new knot join the right set with b = 0
            nR += 1
            sRx += xs[i]
            sRy += ys[i]
            i += 1
        prev = t
        if t == u[0]:
            continue                         # the rightmost value: (x - t)+ is identically 0
        G = np.array([[S1, Sx, sb], [Sx, Sxx, sbx], [sb, sbx, sbb]])
        v = np.array([Sy, Sxy, sby])
        beta = np.linalg.solve(G, v)
        out_t.append(t)
        out_rss.append(Syy - beta @ v)
    return np.array(out_t), np.array(out_rss)


def pair_rss_brute(x, y, knots):
    """The same RSS by a fresh least-squares fit of y on [1, (x - t)+, (t - x)+] at every knot."""
    out = []
    for t in knots:
        A = np.column_stack([np.ones_like(x), hinge(x, t, 1), hinge(x, t, -1)])
        beta, *_ = np.linalg.lstsq(A, y, rcond=None)
        e = y - A @ beta
        out.append(e @ e)
    return np.array(out)
