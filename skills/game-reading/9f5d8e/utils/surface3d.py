"""Perspective plots of a surface z = f(X1, X2) with pycairo (no matplotlib): a shaded mesh in a wire box, painted back to front."""
import math

import numpy as np

from . import cairo_plot as cp

TOP = "#56B4E9"      # the upper side of the surface
UNDER = "#E69F00"    # its underside, seen where the surface is steep and faces away
EDGE = "#555555"
HZ = 0.35            # half-height of the box, in units of its width


def projector(az_deg: float, el_deg: float):
    """Orthographic view: (proj(x, y, z) -> (screen right, screen up, depth towards viewer), unit vector towards the viewer); az = 45 puts the corner of smallest x and y nearest."""
    a, e = math.radians(az_deg), math.radians(el_deg)
    ca, sa, ce, se = math.cos(a), math.sin(a), math.cos(e), math.sin(e)

    def proj(x, y, z):
        right = ca * x - sa * y
        away = sa * x + ca * y
        return right, z * ce + away * se, -away * ce + z * se
    return proj, np.array([-sa * ce, -ca * ce, se])


def _arrow(c, x0, y0, x1, y1, width=1.6, head=10):
    cp.line(c, x0, y0, x1, y1, colour=cp.INK, width=width)
    ln = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
    for s in (1, -1):
        cp.line(c, x1, y1, x1 - ux * head - s * uy * head * 0.5, y1 - uy * head + s * ux * head * 0.5,
                colour=cp.INK, width=width)


def _label(c, x, y, name, size):
    parts = [name[:-1], (name[-1], "sub")] if name[-1].isdigit() else [name]
    cp.text(c, x, y + size * 0.35, parts, size=size, align="centre", italic=True)


def surface(c, box, f, xr=(0.0, 1.0), yr=(0.0, 1.0), zr=None, n=40, az=45.0, el=28.0,
            xlabel="X1", ylabel="X2", zlabel="g(V)", font_size=22, top=TOP, under=UNDER):
    """Draw z = f(X1, X2) (f takes meshgrid arrays) over xr x yr, heights zr (default the data's range), in the pixel box (left, top, right, bottom) of cairo context c; axis names ending in a digit get it as a subscript."""
    gx = np.linspace(*xr, n + 1)
    gy = np.linspace(*yr, n + 1)
    X, Y = np.meshgrid(gx, gy, indexing="ij")
    Z = f(X, Y)
    lo, hi = zr if zr else (float(Z.min()), float(Z.max()))
    wx = (X - xr[0]) / (xr[1] - xr[0]) - 0.5
    wy = (Y - yr[0]) / (yr[1] - yr[0]) - 0.5
    wz = ((Z - lo) / (hi - lo) - 0.5) * 2 * HZ
    proj, toward = projector(az, el)

    corners = [(sx, sy, sz) for sx in (-0.5, 0.5) for sy in (-0.5, 0.5) for sz in (-HZ, HZ)]
    us = [proj(*p)[0] for p in corners]
    vs = [proj(*p)[1] for p in corners]
    L, T, R, B = box
    scale = min((R - L) / (max(us) - min(us)), (B - T) / (max(vs) - min(vs)))
    cu, cv = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2

    def px(p):
        u, v, _ = proj(*p)
        return (L + R) / 2 + (u - cu) * scale, (T + B) / 2 - (v - cv) * scale

    edges = [(p, q) for i, p in enumerate(corners) for q in corners[i + 1:]
             if sum(a != b for a, b in zip(p, q)) == 1]
    near_bottom = max((p for p in corners if p[2] < 0), key=lambda p: proj(*p)[2])
    near_top = (near_bottom[0], near_bottom[1], HZ)
    front = [e for e in edges if near_bottom in e or near_top in e]

    def edge(e):
        (x0, y0), (x1, y1) = px(e[0]), px(e[1])
        cp.line(c, x0, y0, x1, y1, colour=cp.rgb(EDGE), width=1.2)

    for e in edges:
        if e not in front:
            edge(e)

    light = np.array([-0.3, -0.6, 0.75])
    light /= np.linalg.norm(light)
    quads = []
    for i in range(n):
        for j in range(n):
            P = np.array([(wx[a, b], wy[a, b], wz[a, b])
                          for a, b in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))])
            nrm = np.cross(P[2] - P[0], P[3] - P[1])
            nrm /= np.linalg.norm(nrm)
            quads.append((sum(proj(*p)[2] for p in P) / 4, P, nrm))
    quads.sort(key=lambda q: q[0])
    up_col, down_col = np.array(cp.rgb(top)), np.array(cp.rgb(under))
    for _, P, nrm in quads:
        base = up_col if np.dot(nrm, toward) > 0 else down_col
        col = base * (0.5 + 0.5 * abs(float(np.dot(nrm, light))))
        c.new_path()
        for k, p in enumerate(P):
            (c.move_to if k == 0 else c.line_to)(*px(p))
        c.close_path()
        c.set_source_rgb(*col)
        c.fill_preserve()
        c.set_source_rgb(*(col * 0.6))
        c.set_line_width(0.6)
        c.stroke()

    for e in front:
        edge(e)

    # axis arrows below the two near floor edges, pointing the way each input increases
    x_end = (-near_bottom[0], near_bottom[1], near_bottom[2])
    y_end = (near_bottom[0], -near_bottom[1], near_bottom[2])
    sign_x = 1 if near_bottom[0] < 0 else -1
    sign_y = 1 if near_bottom[1] < 0 else -1
    for end, name, sgn in ((x_end, xlabel, sign_x), (y_end, ylabel, sign_y)):
        start, stop = (near_bottom, end) if sgn > 0 else (end, near_bottom)
        (x0, y0), (x1, y1) = px(start), px(stop)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = x1 - x0, y1 - y0
        ox, oy = -dy, dx
        if oy < 0:
            ox, oy = -ox, -oy
        on = math.hypot(ox, oy)
        ox, oy = ox / on * 30, oy / on * 30
        _arrow(c, mx - dx * 0.2 + ox, my - dy * 0.2 + oy, mx + dx * 0.2 + ox, my + dy * 0.2 + oy)
        _label(c, mx + ox * 1.9, my + oy * 1.9, name, font_size)

    # the vertical axis beside the leftmost vertical edge
    left = min(corners, key=lambda p: px(p)[0])
    (xb, yb), (xt, yt) = px((left[0], left[1], -HZ)), px((left[0], left[1], HZ))
    xa, ym = xb - 16, (yb + yt) / 2
    _arrow(c, xa, ym + 24, xa, ym - 24)
    cp.text(c, xa - 10, ym + font_size * 0.35, [zlabel], size=font_size, align="right", italic=True)
