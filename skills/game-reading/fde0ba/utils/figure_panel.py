"""Draw simple line and scatter charts with PIL (no matplotlib): axes, ticks, lines, fills, points and legends, supersampled."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = "/Library/Fonts/Arial Unicode.ttf"
INK = "#1F2328"
S = 3  # supersampling: draw at S times the size, then shrink in save(), for smooth lines


def font(size, *, scale=S, path=FONT):
    """A TrueType font at `size` points on a canvas drawn at `scale`."""
    return ImageFont.truetype(path, size * scale)


def canvas(w, h, *, scale=S, background="white"):
    """A blank RGB image of w x h final pixels, drawn at `scale` times that size."""
    return Image.new("RGB", (w * scale, h * scale), background)


def save(img, path, *, scale=S):
    """Shrink a supersampled canvas to its final size and write it as a PNG; returns (width, height)."""
    img = img.resize((img.width // scale, img.height // scale), Image.LANCZOS)
    img.save(Path(path), optimize=True)
    return img.size


class Panel:
    """One set of axes on a canvas (box in final pixels): data to pixels, lines, fills, points, then frame() for ticks and labels.

    A tick may be a number or a (value, label) pair.
    """

    def __init__(self, img, box, xlim, ylim, xticks, yticks, xlabel="", ylabel="", title="", *,
                 scale=S, ink=INK, font_path=FONT):
        self.img, self.d, self.s, self.ink, self.font_path = img, ImageDraw.Draw(img), scale, ink, font_path
        self.l, self.t, self.r, self.b = (v * scale for v in box)
        self.xlim, self.ylim = xlim, ylim
        self.xticks, self.yticks, self.xlabel, self.ylabel, self.title = xticks, yticks, xlabel, ylabel, title

    def _font(self, size):
        return font(size, scale=self.s, path=self.font_path)

    def px(self, x, y):
        """Canvas pixel of the data point (x, y)."""
        (x0, x1), (y0, y1) = self.xlim, self.ylim
        return (self.l + (x - x0) / (x1 - x0) * (self.r - self.l),
                self.b - (y - y0) / (y1 - y0) * (self.b - self.t))

    def frame(self):
        """Box, ticks, axis labels and title; call it last so the data never covers them."""
        d, s, f = self.d, self.s, self._font(13)
        d.rectangle([self.l, self.t, self.r, self.b], outline=self.ink, width=s)
        for x in self.xticks:
            x, lab = x if isinstance(x, tuple) else (x, f"{x:g}")
            px = self.px(x, self.ylim[0])[0]
            d.line([(px, self.b), (px, self.b + 6 * s)], fill=self.ink, width=s)
            d.text((px, self.b + 9 * s), lab, font=f, fill=self.ink, anchor="ma")
        for y in self.yticks:
            y, lab = y if isinstance(y, tuple) else (y, f"{y:g}")
            py = self.px(self.xlim[0], y)[1]
            d.line([(self.l - 6 * s, py), (self.l, py)], fill=self.ink, width=s)
            d.text((self.l - 9 * s, py), lab, font=f, fill=self.ink, anchor="rm")
        if self.xlabel:
            d.text(((self.l + self.r) / 2, self.b + 30 * s), self.xlabel, font=self._font(14), fill=self.ink, anchor="ma")
        if self.title:
            d.text(((self.l + self.r) / 2, self.t - 10 * s), self.title, font=self._font(15), fill=self.ink, anchor="md")
        if self.ylabel:
            f14 = self._font(14)
            lab = Image.new("RGBA", (int(d.textlength(self.ylabel, font=f14)) + 4 * s, 22 * s), (255, 255, 255, 0))
            ImageDraw.Draw(lab).text((2 * s, 2 * s), self.ylabel, font=f14, fill=self.ink)
            lab = lab.rotate(90, expand=True)
            self.img.paste(lab, (int(self.l - 62 * s), int((self.t + self.b) / 2 - lab.height / 2)), lab)

    def line(self, xs, ys, color, width=2.0, breaks=None):
        """A polyline clipped to the y range; `breaks` (one bool per gap between points) leaves a gap where a curve jumps."""
        ys = np.clip(ys, *self.ylim)
        pts = [self.px(x, y) for x, y in zip(xs, ys)]
        start = 0
        cuts = [] if breaks is None else list(np.flatnonzero(breaks) + 1)
        for end in cuts + [len(pts)]:
            if end - start > 1:
                self.d.line(pts[start:end], fill=color, width=int(width * self.s), joint="curve")
            start = end

    def fill(self, xs, lo, hi, color):
        """The area between the curves lo and hi over xs."""
        top = [self.px(x, y) for x, y in zip(xs, hi)]
        bot = [self.px(x, y) for x, y in zip(np.asarray(xs)[::-1], np.asarray(lo)[::-1])]
        self.d.polygon(top + bot, fill=color)

    def circles(self, xs, ys, color, r=4.0, width=1.3):
        """Open circles of radius r (final pixels) at the points."""
        s = self.s
        for x, y in zip(xs, ys):
            px, py = self.px(x, y)
            self.d.ellipse([px - r * s, py - r * s, px + r * s, py + r * s], outline=color, width=int(width * s))

    def dot(self, x, y, color, r=6.0):
        """One filled dot of radius r (final pixels)."""
        px, py = self.px(x, y)
        s = self.s
        self.d.ellipse([px - r * s, py - r * s, px + r * s, py + r * s], fill=color)

    def vline(self, x, color, width=1.0, y=None):
        """A vertical line at x, over the whole y range or over y = (y0, y1)."""
        y0, y1 = y if y else self.ylim
        self.d.line([self.px(x, y0), self.px(x, y1)], fill=color, width=max(1, int(width * self.s)))

    def text(self, x, y, s, color=None, size=14, anchor="la"):
        """Text anchored at the data point (x, y)."""
        self.d.text(self.px(x, y), s, font=self._font(size), fill=color or self.ink, anchor=anchor)

    def legend(self, items, at):
        """Legend rows (label, color, kind) with kind 'line', 'box' or 'circle', from `at` (final pixels) downward."""
        s = self.s
        x, y = at[0] * s, at[1] * s
        for label, color, kind in items:
            if kind == "line":
                self.d.line([(x, y), (x + 26 * s, y)], fill=color, width=3 * s)
            elif kind == "box":
                self.d.rectangle([x, y - 7 * s, x + 26 * s, y + 7 * s], fill=color)
            else:
                self.d.ellipse([x + 9 * s, y - 4 * s, x + 17 * s, y + 4 * s], outline=color, width=int(1.3 * s))
            self.d.text((x + 34 * s, y), label, font=self._font(13), fill=self.ink, anchor="lm")
            y += 21 * s


def dashed(panel, xs, ys, color, width=1.6, on=7, off=5):
    """A dashed polyline on `panel`, clipped to its y range; dash and gap lengths `on`, `off` in final pixels."""
    sc = panel.s
    pts = [panel.px(x, y) for x, y in zip(xs, np.clip(ys, *panel.ylim))]
    draw, carry = True, 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = float(np.hypot(x1 - x0, y1 - y0))
        pos = 0.0
        while pos < seg:
            step = min((on if draw else off) * sc - carry, seg - pos)
            a, b = pos / seg, (pos + step) / seg
            if draw:
                panel.d.line([(x0 + a * (x1 - x0), y0 + a * (y1 - y0)), (x0 + b * (x1 - x0), y0 + b * (y1 - y0))],
                             fill=color, width=int(width * sc))
            pos += step
            carry += step
            if carry >= (on if draw else off) * sc - 1e-9:
                draw, carry = not draw, 0.0


def errbars(panel, xs, mid, se, color, cap=3.0, width=1.2):
    """Vertical bars from mid - se to mid + se at each x on `panel`, with caps of half-width `cap` final pixels."""
    sc = panel.s
    for x, m, s in zip(xs, mid, se):
        (cx, top), (_, bot) = panel.px(x, m + s), panel.px(x, m - s)
        panel.d.line([(cx, top), (cx, bot)], fill=color, width=int(width * sc))
        for y in (top, bot):
            panel.d.line([(cx - cap * sc, y), (cx + cap * sc, y)], fill=color, width=int(width * sc))
