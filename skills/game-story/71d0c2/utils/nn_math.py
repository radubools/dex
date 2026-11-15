"""Model Workshop's neural-network maths in Python (MLP start, forward, backprop, per-layer gradient sizes), checkable against the game's src/ml/nn.js.

Mirrors <game>/src/ml/nn.js: MLP(sizes, activation, seed, init) draws
Xavier-uniform × init weights row by row from the engine's makeRng(seed),
biases 0, a sigmoid output with cross-entropy (so the output delta is
out − t), and layerGradientSizes (the mean |dW| per weight layer that
nn.gradients reports). The station feeds a floor point as (x/5, z/5) and
src/actions/nn.js builds every net with seed 4.
"""
from __future__ import annotations

import math

from .rng import make_rng


def sigmoid(t: float) -> float:
    """1 / (1 + e^-t), 0 where JavaScript's Math.exp would overflow."""
    if t < -700:
        return 0.0
    return 1 / (1 + math.exp(-t))


ACT = {
    "sigmoid": (sigmoid, lambda s, a: a * (1 - a)),
    "tanh": (math.tanh, lambda s, a: 1 - a * a),
    "relu": (lambda s: s if s > 0 else 0.0, lambda s, a: 1.0 if s > 0 else 0.0),
    "linear": (lambda s: s, lambda s, a: 1.0),
}


class MLP:
    """nn.js MLP: weights W[l][i][j] (one row per neuron), biases b[l][i], hidden `activation`, sigmoid output."""

    def __init__(self, sizes, activation="sigmoid", seed=4, init=1.0):
        rng = make_rng(seed)
        self.sizes, self.activation = list(sizes), activation
        self.W, self.b = [], []
        for l in range(1, len(sizes)):
            lim = init * math.sqrt(6 / (sizes[l - 1] + sizes[l]))
            self.W.append([[(2 * rng() - 1) * lim for _ in range(sizes[l - 1])] for _ in range(sizes[l])])
            self.b.append([0.0] * sizes[l])

    def act(self, l):
        """(f, f') of weight layer l's outputs: the hidden activation, sigmoid on the last."""
        return ACT["sigmoid"] if l == len(self.W) - 1 else ACT[self.activation]

    def forward(self, inp):
        """(activations, pre): activations[0] is the input, pre[l] the sums feeding layer l + 1."""
        acts, pre, a = [list(inp)], [], inp
        for l, (W, bias) in enumerate(zip(self.W, self.b)):
            s = [sum((w * x for w, x in zip(row, a)), start=bi) for row, bi in zip(W, bias)]
            f = self.act(l)[0]
            a = [f(v) for v in s]
            pre.append(s)
            acts.append(a)
        return acts, pre

    def backward(self, inp, t):
        """(dW, db, activations) for one input against target t (number), as nn.js backward()."""
        acts, pre = self.forward(inp)
        n = len(self.W)
        delta = [o - t for o in acts[n]]
        dW, db = [None] * n, [None] * n
        for l in range(n - 1, -1, -1):
            a = acts[l]
            dW[l] = [[d * x for x in a] for d in delta]
            db[l] = list(delta)
            if l > 0:
                dfn = self.act(l - 1)[1]
                delta = [dfn(pre[l - 1][j], x) * sum(d * self.W[l][i][j] for i, d in enumerate(delta))
                         for j, x in enumerate(a)]
        return dW, db, acts

    def batch_gradients(self, points):
        """Mean dW over floor points [x, z, cls] (target 1 for class 1, else 0), as nn.js batchGradients."""
        tot = None
        for x, z, c in points:
            dW, _, _ = self.backward([x / 5, z / 5], 1 if c == 1 else 0)
            if tot is None:
                tot = [[[0.0] * len(r) for r in W] for W in dW]
            for l, W in enumerate(dW):
                for i, r in enumerate(W):
                    for j, v in enumerate(r):
                        tot[l][i][j] += v / len(points)
        return tot


def layer_gradient_sizes(dW):
    """Mean |gradient| of each weight layer, input to output: what nn.gradients returns as `sizes`."""
    return [sum(abs(v) for r in W for v in r) / sum(len(r) for r in W) for W in dW]
