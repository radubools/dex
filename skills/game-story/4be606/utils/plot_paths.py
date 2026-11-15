"""Paths drawn on the llm-math plot board as dotted lines of small story-spawned markers, one run of dots per straight move."""
from .plot_points import board_xh


def plot_path_dots(pts, window_x, window_y, prefix, *, gap=0.16, scale=0.32, asset="environment/marker-sphere",
                   color="highlight", z=0.03, digits=3):
    """Story spawns drawing the path pts[0] -> pts[1] -> ... (plotted values) on the board: move i's dots are `<prefix><i>-<k>`, zero-length moves skipped."""
    out = []
    for i in range(1, len(pts)):
        xa, ha = board_xh(pts[i - 1][0], pts[i - 1][1], window_x, window_y)
        xb, hb = board_xh(pts[i][0], pts[i][1], window_x, window_y)
        length = ((xb - xa) ** 2 + (hb - ha) ** 2) ** 0.5
        if length < 1e-6:
            continue
        n = max(1, round(length / gap))
        for k in range(1, n + 1):
            f = k / n
            x, h = xa + f * (xb - xa), ha + f * (hb - ha)
            # marker-sphere is 0.3 tall from its base: lower it so its centre sits on the point
            out.append({"id": f"{prefix}{i}-{k}", "asset": asset, "at": [round(x, digits), z],
                        "y": round(h - 0.15 * scale, digits), "scale": scale, "color": color})
    return out


def move_ids(spawns, prefix, i):
    """The ids of move i's dots among plot_path_dots' spawns, in order."""
    return [d["id"] for d in spawns if d["id"].startswith(f"{prefix}{i}-")]
