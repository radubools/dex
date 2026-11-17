"""Single-hidden-layer neural networks of ESL (11.5) with weight decay (11.16) or weight elimination (11.17): back-propagation gradients, BFGS fits and epoch-by-epoch batch gradient descent, numpy + scipy.

Weights are packed as one vector theta = [alpha (p+1, M), beta (M+1, K)], row 0 of
each block the intercepts.  The penalty lambda * J(theta) covers the non-intercept
weights only.
"""
import numpy as np
from scipy.optimize import minimize


def sigmoid(v):
    """1 / (1 + e^-v), elementwise, with v clipped to [-60, 60] so it never overflows."""
    return 1.0 / (1.0 + np.exp(-np.clip(v, -60, 60)))


def unpack(theta, p, M, K):
    """The weight vector theta as (alpha (p+1, M), beta (M+1, K)), row 0 of each the intercepts."""
    na = (p + 1) * M
    return theta[:na].reshape(p + 1, M), theta[na:].reshape(M + 1, K)


def start(rng, p, M, K, scale=0.7):
    """Random starting weights, uniform on [-scale, scale] (ESL 11.5.3), as one theta vector."""
    return rng.uniform(-scale, scale, (p + 1) * M + (M + 1) * K)


def forward(theta, X, M, K):
    """Hidden activations Z (N, M) and output scores T (N, K) of the rows of X, as in (11.5)."""
    p = X.shape[1]
    A, B = unpack(theta, p, M, K)
    Z = sigmoid(A[0] + X @ A[1:])
    return Z, B[0] + Z @ B[1:]


def predict(theta, X, M, K, out="linear"):
    """The network's outputs at the rows of X: scores T for out='linear', softmax probabilities (11.6) for out='softmax'."""
    _, T = forward(theta, X, M, K)
    if out == "softmax":
        T = T - T.max(1, keepdims=True)
        E = np.exp(T)
        return E / E.sum(1, keepdims=True)
    return T


def loss_grad(theta, X, Y, M, K, lam, out="linear", penalty="decay"):
    """R(theta) + lam J(theta) and its gradient by back-propagation: R squared error (out='linear') or cross-entropy with one-hot Y (out='softmax'); J (11.16) for penalty='decay', (11.17) for 'elimination'."""
    p = X.shape[1]
    A, B = unpack(theta, p, M, K)
    Z = sigmoid(A[0] + X @ A[1:])
    T = B[0] + Z @ B[1:]
    if out == "softmax":
        T = T - T.max(1, keepdims=True)
        P = np.exp(T)
        P /= P.sum(1, keepdims=True)
        R = -np.sum(Y * np.log(np.maximum(P, 1e-300)))
        D = P - Y                                   # dR/dT
    else:
        D = -2.0 * (Y - T)
        R = np.sum((Y - T) ** 2)
    gB = np.vstack([D.sum(0), Z.T @ D])
    S = (D @ B[1:].T) * Z * (1 - Z)                 # back-propagated errors s_mi (11.15)
    gA = np.vstack([S.sum(0), X.T @ S])
    w = np.concatenate([A[1:].ravel(), B[1:].ravel()])
    if penalty == "elimination":
        J = np.sum(w ** 2 / (1 + w ** 2))
        dJ = lambda u: 2 * u / (1 + u ** 2) ** 2  # noqa: E731
    else:
        J = np.sum(w ** 2)
        dJ = lambda u: 2 * u  # noqa: E731
    gA[1:] += lam * dJ(A[1:])
    gB[1:] += lam * dJ(B[1:])
    return R + lam * J, np.concatenate([gA.ravel(), gB.ravel()])


def fit(X, Y, M, lam, rng, out="linear", maxiter=3000, penalty="decay", theta0=None):
    """Minimise R + lam J with L-BFGS from theta0 or random starting weights; Y is (N, K); returns theta."""
    K = Y.shape[1]
    th = start(rng, X.shape[1], M, K) if theta0 is None else theta0
    res = minimize(loss_grad, th, args=(X, Y, M, K, lam, out, penalty), jac=True,
                   method="L-BFGS-B", options={"maxiter": maxiter, "maxfun": maxiter * 2})
    return res.x


def gd_path(X, Y, M, lam, rng, epochs, gamma, out="linear", every=1, monitor=None, scale=0.7):
    """Batch back-propagation (11.13), theta <- theta - gamma * grad, for `epochs` epochs: (monitored epochs, monitor(theta) at each, final theta), monitoring at epoch 0 and every `every`."""
    K = Y.shape[1]
    th = start(rng, X.shape[1], M, K, scale)
    ep, vals = [0], [monitor(th)]
    for e in range(1, epochs + 1):
        _, g = loss_grad(th, X, Y, M, K, lam, out)
        th = th - gamma * g
        if e % every == 0:
            ep.append(e)
            vals.append(monitor(th))
    return np.array(ep), vals, th
