"""Shared graph-visualization utilities for the game-reading chart kit (pycairo).

Functions for drawing graph figures: laplacian / path_A / dumbbell_A matrix
constructors plus node, link, draw_edges Cairo drawing helpers.

Imported by reading-llm-graph-laplacian and reading-llm-graphs-history.
"""
from __future__ import annotations

import numpy as np

from utils.chart import INK, hexrgb  # noqa: E402

# -- colour constants (used as default parameter values) --

BLUE = hexrgb("#0072B2")
ORANGE = hexrgb("#E69F00")
VERMILLION = hexrgb("#D55E00")
TEAL = hexrgb("#56B4E9")
GREEN = hexrgb("#0072B2")
GREY = hexrgb("#9A9A9A")

# =====================================================================
# graph-adjacency helpers
# =====================================================================


def laplacian(A):
    """Return D - A (combinatorial Laplacian) from adjacency matrix A."""
    return np.diag(A.sum(axis=1)) - A


def path_A(n):
    """Adjacency matrix for the path graph P_n."""
    A = np.zeros((n, n))
    for i in range(n - 1):
        A[i, i + 1] = A[i + 1, i] = 1
    return A


def dumbbell_A(m):
    """Adjacency matrix: two K_m cliques joined by one bridge edge."""
    n = 2 * m
    A = np.zeros((n, n))
    for i in range(m):
        for j in range(i + 1, m):
            A[i, j] = A[j, i] = 1
    for i in range(m, n):
        for j in range(i + 1, n):
            A[i, j] = A[j, i] = 1
    A[m - 1, m] = A[m, m - 1] = 1
    return A


# =====================================================================
# Cairo drawing helpers
# =====================================================================


def node(cv, x, y, r=15, fill=BLUE, edge=INK):
    """Draw a filled circle node at (x, y)."""
    c = cv.c
    c.new_path()
    c.arc(x, y, r, 0, 6.2832)
    c.set_source_rgb(*fill)
    c.fill_preserve()
    c.set_line_width(1.6)
    c.set_source_rgb(*edge)
    c.stroke()


def link(cv, x0, y0, x1, y1, color=INK, width=2.0):
    """Draw a line segment from (x0, y0) to (x1, y1)."""
    c = cv.c
    c.new_path()
    c.move_to(x0, y0)
    c.line_to(x1, y1)
    c.set_line_width(width)
    c.set_source_rgb(*color)
    c.stroke()


def draw_edges(cv, edges, pos, width=2.0, color=INK):
    """Draw multiple edge line segments from edges using positions pos."""
    for i, j in edges:
        link(cv, pos[i][0], pos[i][1], pos[j][0], pos[j][1], color=color, width=width)
