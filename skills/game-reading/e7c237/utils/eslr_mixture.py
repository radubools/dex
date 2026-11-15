"""ESL's simulated two-class mixture (Section 2.3.3, the data of Figures 2.1-2.5 and 12.2) and its Bayes rule, numpy only."""
import math

import numpy as np

SD = math.sqrt(1 / 5)


def draw(rng, means, n, sd=SD):
    """n points: each picks one of `means` (rows) with equal probability, then adds N(0, sd^2 I) noise."""
    return means[rng.integers(0, len(means), n)] + rng.normal(0, sd, (n, means.shape[1]))


def simulate(seed=22, n_per_class=100, n_test=10_000, sd=SD):
    """Means (10 per class from N((1,0), I) for BLUE, N((0,1), I) for ORANGE), then train and test draws: (blue_means, orange_means, x, y, tx, ty), y 0 = BLUE, 1 = ORANGE."""
    rng = np.random.default_rng(seed)
    blue_means = rng.normal([1, 0], 1, (10, 2))
    orange_means = rng.normal([0, 1], 1, (10, 2))
    x = np.vstack([draw(rng, blue_means, n_per_class, sd), draw(rng, orange_means, n_per_class, sd)])
    tx = np.vstack([draw(rng, blue_means, n_test // 2, sd), draw(rng, orange_means, n_test // 2, sd)])
    y = np.repeat([0, 1], n_per_class)
    ty = np.repeat([0, 1], n_test // 2)
    return blue_means, orange_means, x, y, tx, ty


def class_density(q, means, sd=SD):
    """A class's density at the rows of q, up to a constant shared by both classes: the mean of exp(-|q - m|^2 / 2 sd^2) over its means."""
    d2 = ((q[:, None, :] - means[None, :, :]) ** 2).sum(-1)
    return np.exp(-d2 / (2 * sd * sd)).mean(1)


def bayes_field(q, blue_means, orange_means, sd=SD):
    """Pr(ORANGE | x) - 1/2 at the rows of q under the true mixture with equal priors: positive where the Bayes rule says ORANGE."""
    b, o = class_density(q, blue_means, sd), class_density(q, orange_means, sd)
    return o / (b + o) - 0.5
