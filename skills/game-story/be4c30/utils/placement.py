"""Where to put story spawns on the data floor so they don't sit on the data."""
import math


def free_spot(points, near, r=0.55, step=0.25, limit=4.7, reach=8):
    """The grid spot [x, z] nearest `near` at least `r` from every point ([x, z, ...] rows), within ±limit; None if there is none."""
    best = None
    for i in range(-reach, reach + 1):
        for j in range(-reach, reach + 1):
            x, z = near[0] + i * step, near[1] + j * step
            if abs(x) > limit or abs(z) > limit:
                continue
            if points and min(math.hypot(x - p[0], z - p[1]) for p in points) < r:
                continue
            d = math.hypot(x - near[0], z - near[1])
            if best is None or d < best[0]:
                best = (d, [round(x, 2), round(z, 2)])
    return best[1] if best else None
