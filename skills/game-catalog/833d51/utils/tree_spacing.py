"""How close a tree section's cards come, per hub cluster, from positions such as `story_catalog.layout`'s."""
import math


def closest_cards(tiles, pos):
    """Per hub: (hub id, direct branches, cards, closest card distance in cells, closest pair); `pos` maps id -> [x, y]."""
    parent = {t["id"]: t.get("parent") for t in tiles}

    def hub(i):
        while parent.get(i):
            i = parent[i]
        return i

    clusters = {}
    for t in tiles:
        clusters.setdefault(hub(t["id"]), []).append(t["id"])
    out = []
    for h, ids in clusters.items():
        best = (math.inf, None)
        for n, a in enumerate(ids):
            for b in ids[n + 1:]:
                d = math.dist(pos[a], pos[b])
                if d < best[0]:
                    best = (d, (a, b))
        out.append((h, sum(parent[i] == h for i in ids), len(ids), best[0], best[1]))
    return out
