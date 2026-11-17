"""Figures without matplotlib: a small pycairo chart kit (canvas, axes panels, ticks, lines, bands, rugs, points, legends) writing PNGs."""
import cairo

INK = (0x1F / 255, 0x23 / 255, 0x28 / 255)
GREY = (0.45, 0.45, 0.45)
FONT = "Helvetica"


def hexrgb(h):
    """'#RRGGBB' to an (r, g, b) tuple of floats in [0, 1], the colour form every drawing call takes."""
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def stair(step_fn, eta, tmax):
    """Staircase polyline of an iterate recurrence: value step_fn(k) held over [k*eta, (k+1)*eta), returned as (xs, ys) for Panel.line."""
    xs, ys = [], []
    k, t = 0, 0.0
    while t < tmax - 1e-12:
        t1 = min(t + eta, tmax)
        v = step_fn(k)
        xs += [t, t1]
        ys += [v, v]
        t, k = t1, k + 1
    return xs, ys


class Canvas:
    """A white canvas of w x h points, written at `scale` pixels per point."""

    def __init__(self, w, h, scale=2.0):
        self.w, self.h, self.scale = w, h, scale
        self.surf = cairo.ImageSurface(cairo.FORMAT_RGB24, int(w * scale), int(h * scale))
        self.c = cairo.Context(self.surf)
        self.c.scale(scale, scale)
        self.c.set_source_rgb(1, 1, 1)
        self.c.paint()

    def text(self, x, y, s, size=11, color=INK, anchor="lt", bold=False, italic=False, rotate=0.0):
        """Write `s` at canvas point (x, y); `anchor` is horizontal l/c/r then vertical t/m/b; `rotate` in radians."""
        c = self.c
        c.save()
        c.select_font_face(FONT, cairo.FONT_SLANT_ITALIC if italic else cairo.FONT_SLANT_NORMAL,
                           cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        c.set_font_size(size)
        ext = c.text_extents(s)
        c.translate(x, y)
        c.rotate(rotate)
        dx = {"l": 0, "c": -ext.x_advance / 2, "r": -ext.x_advance}[anchor[0]]
        dy = {"t": size * 0.75, "m": size * 0.35, "b": 0}[anchor[1]]
        c.move_to(dx, dy)
        c.set_source_rgb(*color)
        c.show_text(s)
        c.restore()

    def save(self, path):
        """Write the canvas as a PNG."""
        self.surf.write_to_png(str(path))


class Panel:
    """Axes in a box (left, top, right, bottom) of the canvas, mapping data coordinates to points."""

    def __init__(self, cv, box, xlim, ylim):
        self.cv, self.c = cv, cv.c
        self.l, self.t, self.r, self.b = box
        self.xlim, self.ylim = xlim, ylim

    def px(self, x, y):
        """Data coordinates to canvas points."""
        (x0, x1), (y0, y1) = self.xlim, self.ylim
        return (self.l + (x - x0) / (x1 - x0) * (self.r - self.l),
                self.b - (y - y0) / (y1 - y0) * (self.b - self.t))

    def frame(self, xticks, yticks, xlabel="", ylabel="", xfmt="{:g}", yfmt="{:g}", title="", box=True, size=10):
        """Draw the axes box (or left and bottom axes), tick marks with labels, axis labels and a title."""
        c = self.c
        c.set_source_rgb(*INK)
        c.set_line_width(0.9)
        if box:
            c.rectangle(self.l, self.t, self.r - self.l, self.b - self.t)
        else:
            c.move_to(self.l, self.t)
            c.line_to(self.l, self.b)
            c.line_to(self.r, self.b)
        c.stroke()
        for x in xticks:
            p = self.px(x, self.ylim[0])[0]
            c.move_to(p, self.b)
            c.line_to(p, self.b + 4)
            c.stroke()
            self.cv.text(p, self.b + 6, x if isinstance(x, str) else xfmt.format(x), size, anchor="ct")
        for y in yticks:
            p = self.px(self.xlim[0], y)[1]
            c.move_to(self.l - 4, p)
            c.line_to(self.l, p)
            c.stroke()
            self.cv.text(self.l - 6, p, yfmt.format(y), size, anchor="rm")
        if xlabel:
            self.cv.text((self.l + self.r) / 2, self.b + 22, xlabel, size + 1, anchor="ct")
        if ylabel:
            self.cv.text(self.l - 44, (self.t + self.b) / 2, ylabel, size + 1, anchor="cb", rotate=-1.5707963)
        if title:
            self.cv.text((self.l + self.r) / 2, self.t - 8, title, size + 2, anchor="cb", bold=True)

    def clip(self):
        """Clip drawing to the panel's box until unclip()."""
        self.c.save()
        self.c.rectangle(self.l, self.t, self.r - self.l, self.b - self.t)
        self.c.clip()

    def unclip(self):
        """Undo clip()."""
        self.c.restore()

    def line(self, xs, ys, color, width=2.0, dash=None, alpha=1.0):
        """A polyline through data points, clipped to the panel; `dash` is a cairo dash list."""
        c = self.c
        self.clip()
        c.set_source_rgba(*color, alpha)
        c.set_line_width(width)
        c.set_line_join(cairo.LINE_JOIN_ROUND)
        c.set_dash(dash or [])
        for i, (x, y) in enumerate(zip(xs, ys)):
            (c.move_to if i == 0 else c.line_to)(*self.px(x, y))
        c.stroke()
        c.set_dash([])
        self.unclip()

    def band(self, xs, lo, hi, color, alpha=0.2):
        """A translucent filled band between the curves lo(x) and hi(x)."""
        c = self.c
        self.clip()
        c.set_source_rgba(*color, alpha)
        for i, (x, y) in enumerate(zip(xs, hi)):
            (c.move_to if i == 0 else c.line_to)(*self.px(x, y))
        for x, y in zip(xs[::-1], lo[::-1]):
            c.line_to(*self.px(x, y))
        c.close_path()
        c.fill()
        self.unclip()

    def hline(self, y, color=INK, width=1.0, dash=(4, 3)):
        """A dashed horizontal reference line across the panel at data height y."""
        self.line(self.xlim, (y, y), color, width, list(dash))

    def vline(self, x, color=INK, width=1.0, dash=(4, 3)):
        """A dashed vertical reference line across the panel at data position x."""
        self.line((x, x), self.ylim, color, width, list(dash))

    def rug(self, xs, color, top=False, length=6, width=0.8):
        """Tick marks at each observation along the bottom (or top) edge of the panel."""
        c = self.c
        c.set_source_rgb(*color)
        c.set_line_width(width)
        for x in xs:
            p = self.px(x, 0)[0]
            y = self.t if top else self.b
            c.move_to(p, y)
            c.line_to(p, y + length if top else y - length)
        c.stroke()

    def points(self, xs, ys, color, r=3.0, shape="o"):
        """Filled markers at data points: 'o' circles or 's' squares of radius r points."""
        c = self.c
        for x, y in zip(xs, ys):
            px, py = self.px(x, y)
            if shape == "s":
                c.rectangle(px - r, py - r, 2 * r, 2 * r)
            else:
                c.arc(px, py, r, 0, 6.2832)
            c.set_source_rgb(*color)
            c.fill()

    def legend(self, items, x, y, size=10):
        """items: (label, color, kind) with kind 'line', 'dash', 'o' or 's'; (x, y) is the top-left in points."""
        c = self.c
        for i, (label, color, kind) in enumerate(items):
            yy = y + i * (size + 6) + size / 2
            c.set_source_rgb(*color)
            if kind in ("line", "dash"):
                c.set_line_width(2)
                c.set_dash([4, 3] if kind == "dash" else [])
                c.move_to(x, yy)
                c.line_to(x + 22, yy)
                c.stroke()
                c.set_dash([])
            elif kind == "s":
                c.rectangle(x + 8, yy - 3.5, 7, 7)
                c.fill()
            else:
                c.arc(x + 11, yy, 3.5, 0, 6.2832)
                c.fill()
            self.cv.text(x + 28, yy, label, size, anchor="lm")
