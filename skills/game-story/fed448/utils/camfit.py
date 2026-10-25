"""Predicts where a story camera puts highlighted things on a Model Workshop screen (desktop and phone), and fits cameras that keep them clear.

It reproduces the game's own maths: lesson.js aim() and view(), the engine's orbit
camera (eye = target + d·(cos p·cos yaw, sin p, cos p·sin yaw), vertical fov 0.62,
zoom and pitch clamps), and the story runner's onScreen() limits (top bar,
bottom − 120 px, 30 px sides, the story card and the bench).

Entity positions come from `story.simulate(pkg, game_root)`: each snapshot has
`pos` (every stage entity's [x, height, z]), `points` and `lifted`. A camera is set
one microtask after its beat starts, so its aim() sees the previous beat's stage
plus this beat's spawns (`aim_snapshot`), while the highlights are where the beat
leaves them. Card and bench rectangles are estimates read off end-to-end
screenshots: a fit is a strong first guess (in one lesson every beat it called
clear passed), and the end-to-end run decides.
"""
import math

FOV = 0.62
ROOMY = 1.3
UNIT_PX = 40
ZOOM = (0.3, 3.2)
PITCH = (0.32, 1.36)

VIEWPORTS = {
    # (width, height, top bar bottom, card rect for a plain cue, extra card height per choice option, bench rect)
    "desktop": {"vw": 1280, "vh": 800, "top": 70, "card": (262, 74, 718, 205), "opt": 37, "choice_base": 45,
                "bench": (980, 80, 1272, 790)},
    "phone": {"vw": 390, "vh": 844, "top": 66, "card": (10, 70, 380, 245), "opt": 40, "choice_base": 50,
              "bench": (0, 481, 390, 844)},
}


def aim(pos, lifted_points, target):
    """lesson.js aim(): half way up raised things, pulled toward them when they stand behind the target."""
    raised = [p for k, p in pos.items() if k != "mentor" and p[1] > 0.3]
    raised += [p for p in lifted_points if p[1] > 0.3]
    if len(raised) < 3:
        return 0.0, 0.0
    ys = sorted(p[1] for p in raised)
    top = ys[int(len(ys) * 0.9)]
    z = sum(p[2] for p in raised) / len(raised)
    return max(0.0, min(2.5, top / 2)), max(-3.0, min(0.0, (z - target[1]) * 0.5))


def camera(cam, pos, lifted_points, vp):
    """The eye, look point and projection constants a story camera ends at."""
    v = VIEWPORTS[vp]
    vw, vh = v["vw"], v["vh"]
    h, dz = aim(pos, lifted_points, cam["target"])
    d = cam["distance"] * ROOMY * max(1.0, vh / vw * 0.95)
    zoom = max(ZOOM[0], min(ZOOM[1], vh / (2 * math.tan(FOV / 2) * d * UNIT_PX)))
    d = vh / (2 * math.tan(FOV / 2) * zoom * UNIT_PX)
    yaw = math.pi / 2 + math.radians(cam.get("yaw", 25))
    pitch = max(PITCH[0], min(PITCH[1], math.radians(cam.get("pitch", 38))))
    look = (cam["target"][0], h, cam["target"][1] + dz)
    eye = (look[0] + d * math.cos(pitch) * math.cos(yaw), look[1] + d * math.sin(pitch), look[2] + d * math.cos(pitch) * math.sin(yaw))
    return eye, look


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def project(point, eye, look, vp):
    """Screen pixels (x, y) of a world point (x, height, z), or None when behind the eye."""
    v = VIEWPORTS[vp]
    f = _norm(_sub(look, eye))
    right = _norm(_cross(f, (0, 1, 0)))
    up = _cross(right, f)
    rel = _sub(point, eye)
    depth = _dot(rel, f)
    if depth <= 0:
        return None
    t = math.tan(FOV / 2)
    xn = _dot(rel, right) / depth / (t * v["vw"] / v["vh"])
    yn = _dot(rel, up) / depth / t
    return ((xn + 1) / 2 * v["vw"], (1 - yn) / 2 * v["vh"])


# Story card size, read off the e2e screenshots: characters per text line,
# pixels per line, header + nav, and per choice option.
CARD = {"desktop": {"left": 262, "right": 718, "top": 74, "chars": 62, "line": 21, "base": 80, "opt": 40, "optchars": 58},
        "phone": {"left": 10, "right": 380, "top": 70, "chars": 42, "line": 25, "base": 72, "opt": 44, "optchars": 38}}


def card_rect(vp, beat=None, options=0):
    """The story card's rectangle for a beat: its cue, prompt and options wrapped to the card width."""
    c = CARD[vp]
    if beat is None:
        return (c["left"], c["top"], c["right"], c["top"] + c["base"] + 2 * c["line"] + options * c["opt"])
    lines = -(-len(beat.get("say", "")) // c["chars"])
    h = c["base"] + lines * c["line"]
    ch = beat.get("wait", {}).get("choice") if isinstance(beat.get("wait"), dict) else None
    if ch:
        h += 6 + -(-len(ch["prompt"]) // c["chars"]) * c["line"]
        h += sum(c["opt"] + (c["line"] if len(o["label"]) > c["optchars"] else 0) for o in ch["options"])
    return (c["left"], c["top"], c["right"], c["top"] + h)


def clear(sxy, vp, beat=None, bench=True):
    """Pixels to the nearest limit onScreen() applies (negative: covered or off screen)."""
    if sxy is None:
        return -999
    v = VIEWPORTS[vp]
    x, y = sxy
    m = min(x - 30, v["vw"] - 30 - x, y - (v["top"] + 10), (v["vh"] - 120 - 10) - y)
    rects = [card_rect(vp, beat)] + ([v["bench"]] if bench else [])
    for (l, t, r, b) in rects:
        if l < x < r and t < y < b:
            m = min(m, -min(x - l, r - x, y - t, b - y))
    return m


# A ring this much closer to the card or bench than to the screen's other limits still
# counts as covered in the game: on desktop one 16 px below the card failed, on phone 15 px passed.
EDGE_GAP = {"desktop": 35, "phone": 15}


def edge_clear(sxy, vp, beat=None, bench=True, gap=None):
    """clear(), also capped by the distance from outside to the card and bench minus a gap (EDGE_GAP per viewport), so a point just past their edge is not clear."""
    m = clear(sxy, vp, beat, bench)
    if sxy is None or m < 0:
        return m
    g = EDGE_GAP[vp] if gap is None else gap
    x, y = sxy
    rects = [card_rect(vp, beat)] + ([VIEWPORTS[vp]["bench"]] if bench else [])
    for (l, t, r, b) in rects:
        m = min(m, math.hypot(max(l - x, 0, x - r), max(t - y, 0, y - b)) - g)
    return round(m)


def aim_snapshot(trace, beats, beat_id):
    """The stage a beat's camera aims at: the previous beat's snapshot plus this beat's spawns (trace from story.simulate)."""
    ids = [s["id"] for s in trace]
    i = ids.index(beat_id)
    prev = trace[i - 1] if i else {"pos": {}, "points": [], "lifted": False}
    beat = next(b for b in beats if b["id"] == beat_id)
    spawned = {sp["id"]: [sp["at"][0], 0, sp["at"][1]] for sp in beat.get("spawn", [])}
    return {**prev, "pos": {**prev.get("pos", {}), **spawned}}


def check(cam, aim_snap, snap, ids, beat=None, bench=True):
    """{viewport: {id: margin px}}: the camera's aim from `aim_snap` (the stage when the camera is set),
    the highlights' positions from `snap` (the stage once the beat has settled)."""
    lifted = [[p["x"], p.get("y", 0), p["z"]] for p in aim_snap["points"]] if aim_snap.get("lifted") else []
    out = {}
    for vp in VIEWPORTS:
        eye, look = camera(cam, aim_snap["pos"], lifted, vp)
        out[vp] = {}
        for i in ids:
            p = snap["pos"].get(i)
            out[vp][i] = None if p is None else round(clear(project(p, eye, look, vp), vp, beat, bench))
    return out


def fit(aim_snap, snap, ids, base, beat=None, bench=True, xs=None, zs=None,
        dists=(9, 10, 11, 12, 13, 14, 16), pitches=(30, 35, 40, 45, 50, 55, 60)):
    """The camera near `base` whose worst highlight margin over both viewports is largest: (margin, camera, margins)."""
    best = None
    xs = xs or [base["target"][0] + k * 0.5 for k in range(-6, 7)]
    zs = zs or [base["target"][1] + k * 0.5 for k in range(-6, 9)]
    for x in xs:
        for z in zs:
            for d in dists:
                for p in pitches:
                    cam = {**base, "target": [round(x, 2), round(z, 2)], "distance": d, "pitch": p}
                    m = check(cam, aim_snap, snap, ids, beat, bench)
                    worst = min((v for vp in m.values() for v in vp.values() if v is not None), default=-999)
                    if best is None or worst > best[0]:
                        best = (worst, cam, m)
    return best


def highlight_ids(beat):
    """The entity ids a beat's highlight rings (single object or list; `entity` and `entities`)."""
    hl = beat.get("highlight")
    hl = [hl] if isinstance(hl, dict) else (hl or [])
    return [h["entity"] for h in hl if "entity" in h] + [i for h in hl for i in h.get("entities", [])]


def fit_beats(package, game_root, beat_ids=None, base=None, **fit_args):
    """Fit a camera for every beat that rings entities, on story.explore's playthroughs: {beat id: {ids, now, fit}}.

    `now` is check() of the beat's fixed camera (None when it has none or frames), `fit` is fit()'s
    (worst margin, camera, margins) near that camera, or near `base` for a beat without one.
    Each beat is measured on the first playthrough that reaches it (main line, then goto routes).
    """
    import json
    from pathlib import Path

    from . import story

    pkg = Path(package)
    beats = json.loads((pkg / story.STORY_FILE).read_text())["beats"]
    seen = {}
    for run in story.explore(pkg, game_root):
        for snap in run["trace"]:
            seen.setdefault(snap["id"], (run["trace"], snap))
    base = base or {"target": [0, 0], "distance": 12, "yaw": 10, "pitch": 30, "duration": 1.5}
    out = {}
    for b in beats:
        ids = highlight_ids(b)
        if not ids or b["id"] not in seen or (beat_ids and b["id"] not in beat_ids):
            continue
        trace, snap = seen[b["id"]]
        aim_snap = aim_snapshot(trace, beats, b["id"])
        cam = b.get("camera") if isinstance(b.get("camera"), dict) and "target" in b["camera"] else None
        out[b["id"]] = {
            "ids": ids,
            "now": check(cam, aim_snap, snap, ids, b) if cam else None,
            "fit": fit(aim_snap, snap, ids, cam or base, b, **fit_args),
        }
    return out


def input_as_choice(beat):
    """A copy of a beat whose wait.input is modelled as a one-option choice, so card_rect/check/fit count the input form."""
    w = beat.get("wait")
    if isinstance(w, dict) and isinstance(w.get("input"), dict):
        return {**beat, "wait": {"choice": {"prompt": w["input"].get("prompt", ""), "options": [{"label": "x"}]}}}
    return beat
