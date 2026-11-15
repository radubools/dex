"""Contour lines without matplotlib: zero-contour segments of a gridded field (marching squares) and their chaining into polylines."""


def marching_segments(F, g):
    """Zero-contour segments of F (F[i, j] at x = g[j], y = g[i]) as ((x0, y0), (x1, y1)) pairs, linearly interpolated."""
    segs = []
    n = len(g)
    for i in range(n - 1):
        for j in range(n - 1):
            c = [(g[j], g[i], F[i, j]), (g[j + 1], g[i], F[i, j + 1]),
                 (g[j + 1], g[i + 1], F[i + 1, j + 1]), (g[j], g[i + 1], F[i + 1, j])]
            pts = []
            for a in range(4):
                (x0, y0, f0), (x1, y1, f1) = c[a], c[(a + 1) % 4]
                if (f0 < 0) != (f1 < 0):
                    t = f0 / (f0 - f1)
                    pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
            if len(pts) == 2:
                segs.append(tuple(pts))
            elif len(pts) == 4:
                segs += [(pts[0], pts[1]), (pts[2], pts[3])]
    return segs


def marching_segments_xy(F, gx, gy):
    """Zero-contour segments of F on a rectangular grid (F[i, j] at x = gx[j], y = gy[i]), as marching_segments returns them."""
    segs = []
    for i in range(len(gy) - 1):
        for j in range(len(gx) - 1):
            c = [(gx[j], gy[i], F[i, j]), (gx[j + 1], gy[i], F[i, j + 1]),
                 (gx[j + 1], gy[i + 1], F[i + 1, j + 1]), (gx[j], gy[i + 1], F[i + 1, j])]
            pts = []
            for a in range(4):
                (x0, y0, f0), (x1, y1, f1) = c[a], c[(a + 1) % 4]
                if (f0 < 0) != (f1 < 0):
                    t = f0 / (f0 - f1)
                    pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
            if len(pts) == 2:
                segs.append(tuple(pts))
            elif len(pts) == 4:
                segs += [(pts[0], pts[1]), (pts[2], pts[3])]
    return segs


def chain(segs, digits=6):
    """Join segments sharing endpoints (rounded to `digits`) into polylines, open curves first, so a dash pattern runs along each curve."""
    key = lambda p: (round(p[0], digits), round(p[1], digits))  # noqa: E731
    ends = {}
    for i, (a, b) in enumerate(segs):
        ends.setdefault(key(a), []).append(i)
        ends.setdefault(key(b), []).append(i)
    used, polys = set(), []

    def walk(poly, i):
        while True:
            used.add(i)
            a, b = segs[i]
            nxt = b if key(a) == key(poly[-1]) else a
            poly.append(nxt)
            cand = [j for j in ends[key(nxt)] if j not in used]
            if not cand:
                return poly
            i = cand[0]

    def is_open(i):
        return len(ends[key(segs[i][0])]) == 1 or len(ends[key(segs[i][1])]) == 1

    for i in sorted(range(len(segs)), key=lambda i: not is_open(i)):
        if i in used:
            continue
        a, b = segs[i]
        start = a if len(ends[key(a)]) == 1 or len(ends[key(b)]) != 1 else b
        polys.append(walk([start], i))
    return polys
