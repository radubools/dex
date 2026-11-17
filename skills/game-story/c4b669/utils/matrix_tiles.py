"""A square matrix (proximities, dissimilarities, affinities) as a heat map of tinted blocks on the data floor: story spawns."""
import numpy as np

# ink → vermillion → highlight yellow (story.PALETTE colours): dark is low, bright is high.
RAMP = ((0.0, (0x1F, 0x23, 0x28)), (0.55, (0xD5, 0x5E, 0x00)), (1.0, (0xF0, 0xE4, 0x42)))


def ramp_color(t, ramp=RAMP):
    """The '#rrggbb' at t in [0, 1] (clamped) along a ramp of (t, (r, g, b)) stops."""
    t = min(1.0, max(0.0, float(t)))
    for (t0, c0), (t1, c1) in zip(ramp, ramp[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return "#" + "".join(f"{round(a + (b - a) * u):02x}" for a, b in zip(c0, c1))
    return "#" + "".join(f"{v:02x}" for v in ramp[-1][1])


def cell_positions(n, groups=None, *, step=0.68, gap=0.22):
    """Station-local coordinates of n row/column slots `step` apart, centred on 0, with an extra `gap` between
    consecutive groups (sizes summing to n); rows use them as z (row 0 at the far edge), columns as x."""
    breaks, acc = set(), 0
    for size in (groups or [n])[:-1]:
        acc += size
        breaks.add(acc)
    pos, at = [], 0.0
    for s in range(n):
        if s in breaks:
            at += gap
        pos.append(at)
        at += step
    mid = (pos[0] + pos[-1]) / 2
    return [round(p - mid, 3) for p in pos]


def matrix_tiles(M, prefix, order=None, groups=None, *, step=0.68, scale=0.9, gap=0.22, lo=None, hi=None,
                 asset="environment/value-block", ramp=RAMP):
    """Spawns {id: '<prefix>-<row>-<col>', asset, at, scale, color} drawing M[order][:, order] as a heat map;
    the colour spans [lo, hi] (default: the off-diagonal minimum and maximum), row 0 at the far edge."""
    M = np.asarray(M, float)
    n = M.shape[0]
    order = list(range(n)) if order is None else list(order)
    off = M[~np.eye(n, dtype=bool)] if n > 1 else M.ravel()
    lo = float(off.min()) if lo is None else float(lo)
    hi = float(off.max()) if hi is None else float(hi)
    span = (hi - lo) or 1.0
    pos = cell_positions(len(order), groups, step=step, gap=gap)
    return [{"id": f"{prefix}-{r}-{c}", "asset": asset, "at": [pos[c], pos[r]], "scale": scale,
             "color": ramp_color((M[i, j] - lo) / span, ramp)}
            for r, i in enumerate(order) for c, j in enumerate(order)]
