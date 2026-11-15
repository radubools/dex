"""A small supersampled line chart drawn with PIL, for reading figures where matplotlib is not installed."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/Library/Fonts/Arial Unicode.ttf"
INK = "#1F2328"


class Plot:
    """A line chart drawn with PIL: data-space to pixels, a framed axis box with ticks, lines, points, legend."""

    def __init__(self, xr, yr, title, w=1200, h=640, *, font=FONT, ss=3):
        self.w, self.h, self.xr, self.yr, self.ss = w, h, xr, yr, ss
        self.im = Image.new("RGB", (w * ss, h * ss), "white")
        self.d = ImageDraw.Draw(self.im)
        self.f = ImageFont.truetype(font, 22 * ss)
        self.small = ImageFont.truetype(font, 19 * ss)
        self.box = (95, 60, w - 30, h - 75)  # left, top, right, bottom (px at 1x)
        self.d.text((self.box[0] * ss, 16 * ss), title, font=self.f, fill=INK)

    def px(self, x, y):
        """Data coordinates to supersampled pixel coordinates."""
        l, t, r, b = self.box
        X = l + (x - self.xr[0]) / (self.xr[1] - self.xr[0]) * (r - l)
        Y = b - (y - self.yr[0]) / (self.yr[1] - self.yr[0]) * (b - t)
        return X * self.ss, Y * self.ss

    def axes(self, xticks, yticks, xlabel, ylabel):
        """Frame the plot area and draw tick marks, tick labels and axis labels."""
        S, (l, t, r, b) = self.ss, self.box
        self.d.rectangle((l * S, t * S, r * S, b * S), outline=INK, width=2 * S)
        for v in xticks:
            X, _ = self.px(v, self.yr[0])
            self.d.line((X, b * S, X, (b + 8) * S), fill=INK, width=2 * S)
            self.d.text((X, (b + 12) * S), f"{v:g}", font=self.small, fill=INK, anchor="mt")
        for v in yticks:
            _, Y = self.px(self.xr[0], v)
            self.d.line(((l - 8) * S, Y, l * S, Y), fill=INK, width=2 * S)
            self.d.text(((l - 12) * S, Y), f"{v:g}", font=self.small, fill=INK, anchor="rm")
        self.d.text((((l + r) / 2) * S, (b + 42) * S), xlabel, font=self.f, fill=INK, anchor="mt")
        self.d.text((20 * S, ((t + b) / 2) * S), ylabel, font=self.f, fill=INK, anchor="lm")

    def line(self, x, y, color, width=3, dash=False):
        """A polyline through (x, y) in data coordinates, solid or dashed."""
        S = self.ss
        pts = [self.px(a, b) for a, b in zip(x, y)]
        if not dash:
            self.d.line(pts, fill=color, width=width * S, joint="curve")
            return
        for k in range(0, len(pts) - 1, 8):
            self.d.line(pts[k:k + 5], fill=color, width=width * S)

    def points(self, x, y, color, r=8):
        """Filled, outlined dots at (x, y) in data coordinates."""
        S = self.ss
        for a, b in zip(x, y):
            X, Y = self.px(a, b)
            self.d.ellipse((X - r * S, Y - r * S, X + r * S, Y + r * S), fill=color, outline=INK, width=S)

    def legend(self, items, at):
        """Legend rows (label, color, kind) from pixel `at`; kind "point" draws a dot, an int a line of that width."""
        S, (x0, y0) = self.ss, at
        for k, (label, color, kind) in enumerate(items):
            y = (y0 + 30 * k) * S
            if kind == "point":
                self.d.ellipse(((x0 + 12) * S - 7 * S, y - 7 * S, (x0 + 12) * S + 7 * S, y + 7 * S), fill=color, outline=INK)
            else:
                self.d.line((x0 * S, y, (x0 + 26) * S, y), fill=color, width=(kind or 3) * S)
            self.d.text(((x0 + 36) * S, y), label, font=self.small, fill=INK, anchor="lm")

    def save(self, path):
        """Downsample to the final size and write a PNG; returns the path."""
        path = Path(path)
        self.im.resize((self.w, self.h), Image.LANCZOS).save(path)
        return path
