"""Simulate Model Workshop's bench beat by beat: which panels a story leaves up, and which ui.hide misses."""
from __future__ import annotations

from typing import Iterable

#: Panels an action draws on the bench, as the game keys them, from its args.
#: Model actions that draw their own charts are listed with the chart they draw.
PANEL_ACTIONS = {
    "ui.steps": lambda a: f"steps:{a.get('id', 'steps')}",
    "ui.table": lambda a: f"table:{a.get('id', 'table')}",
    "ui.chart": lambda a: f"chart:{a.get('id', 'chart')}",
    "ui.note": lambda a: a.get("id", "note") if a.get("text") else None,
    "data.table": lambda a: f"table:{a.get('id', 'data')}",
    "model.params": lambda a: "params",
    "model.explain": lambda a: "explain",
    "data.values": lambda a: "values" if a.get("show", True) else None,
    "regression.fitStep": lambda a: "chart:reg-loss",
    "regression.robust": lambda a: "chart:reg-robust",
    "gd.ball": lambda a: "chart:gd-loss",
    "gd.step": lambda a: "chart:gd-loss",
}

_PREFIXES = ("chart", "slider", "tool", "meter")


def _hidden_keys(hide_id: str) -> tuple[str, ...]:
    # general.js ui.hide: strip one chart:/slider:/tool:/meter: prefix, then the
    # bench hides the key itself, its chart:/meter:/… forms, and table:/steps:.
    head, _, rest = hide_id.partition(":")
    key = rest if rest and head in _PREFIXES else hide_id
    key = "formula" if key == "plaque" else key
    return (key, f"chart:{key}", f"meter:{key}", f"slider:{key}", f"table:{key}", f"steps:{key}")


def _walk(beats: Iterable[dict], list_ids: bool = False):
    up: dict[str, str | None] = {}
    for beat in beats:
        act = beat.get("act") or {}
        name, args = act.get("action"), act.get("args") or {}
        missed = False
        if name == "ui.hide":
            hid = args.get("id")
            if isinstance(hid, str):
                keys = _hidden_keys(hid)
            elif list_ids and isinstance(hid, list):
                keys = tuple(k for h in hid if isinstance(h, str) for k in _hidden_keys(h))
            else:
                keys = ()
            gone = [k for k in up if k in keys]
            missed = not gone
            for k in gone:
                del up[k]
        elif name == "data.values" and not args.get("show", True):
            up.pop("values", None)
        elif name == "data.clear":
            # data.clear drops the value labels with the points (data.js clearMarks).
            up.pop("values", None)
        elif name in PANEL_ACTIONS:
            key = PANEL_ACTIONS[name](args)
            if key:
                title = args.get("title")
                if name == "ui.steps" and args.get("append") and title is None:
                    title = up.get(key)
                up[key] = title
        yield beat.get("id"), dict(up), missed


def bench_trace(beats: Iterable[dict], *, list_ids: bool = False) -> list[tuple[str, dict[str, str | None]]]:
    """The panels up after each beat: [(beat id, {panel key: title or None})]; list_ids=True lets a ui.hide list hide each key."""
    return [(bid, up) for bid, up, _ in _walk(beats, list_ids)]


def ui_hide_misses(beats: Iterable[dict], *, list_ids: bool = False) -> list[str]:
    """Ids of beats whose ui.hide removes nothing (nothing up under it, or a list id unless list_ids=True, as the game now accepts)."""
    return [bid for bid, _, missed in _walk(beats, list_ids) if missed]


def _up(snap: dict) -> set[str]:
    # A story.simulate snapshot's bench: panel keys as they are, charts, meters and sliders
    # prefixed, the plaque as "formula", and "values" while value labels (val:) are on the floor.
    up = (set(snap.get("panels") or {}) | {f"chart:{k}" for k in snap.get("charts") or {}}
          | {f"meter:{k}" for k in snap.get("meters") or {}}
          | {f"slider:{k}" for k in snap.get("sliders") or {}})
    if snap.get("plaque"):
        up.add("formula")
    if (snap.get("entities") or {}).get("val:") or any(str(k).startswith("val:") for k in snap.get("labels") or {}):
        up.add("values")
    return up


def trace_hide_misses(trace: Iterable[dict], beats: Iterable[dict]) -> list[tuple[str, str]]:
    """(beat id, hide id) for each ui.hide id that removed nothing, read off a story.simulate trace (sees panels game actions draw)."""
    out: list[tuple[str, str]] = []
    prev: set[str] = set()
    # Snapshots carry their beat's id: a branching story's trace skips and repeats beats.
    by_id = {b.get("id"): b for b in beats if isinstance(b, dict)}
    for snap in trace:
        beat = by_id.get(snap.get("id"), {})
        now = _up(snap)
        raw = beat.get("act")
        acts = []
        for e in raw if isinstance(raw, list) else ([raw] if raw else []):
            acts += e["parallel"] if isinstance(e, dict) and isinstance(e.get("parallel"), list) else [e]
        gone = prev - now
        for act in acts:
            if not isinstance(act, dict) or act.get("action") != "ui.hide":
                continue
            hid = (act.get("args") or {}).get("id")
            for one in hid if isinstance(hid, list) else [hid]:
                if not isinstance(one, str) or not gone & set(_hidden_keys(one)):
                    out.append((beat.get("id"), str(one)))
        prev = now
    return out
