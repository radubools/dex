"""Beats and e2e assertions for history lessons told over a timeline (timeline.show / timeline.focus)."""
from __future__ import annotations

import re

_LINK = re.compile(r"\[\[story/([a-z0-9-]+)")


def event_index(events: list[dict], year, title_start: str = "") -> int:
    """The index of the one event of `year` whose title starts with `title_start`; raises if not exactly one."""
    hits = [i for i, e in enumerate(events) if e["year"] == year and e["title"].startswith(title_start)]
    if len(hits) != 1:
        raise ValueError(f"expected one event for {year!r} {title_start!r}, found {hits}")
    return hits[0]


def timeline_event_beat(bid: str, index: int, say: str, more, callout: str, *, section: str | None = None) -> dict:
    """A narration beat that runs timeline.focus on event `index` (stored as `ev<index>`), rings its plaque and waits for next."""
    beat = {"id": bid}
    if section:
        beat["section"] = section
    beat.update({
        "say": say,
        "more": more,
        "act": {"action": "timeline.focus", "args": {"index": index}, "as": f"ev{index}"},
        "highlight": {"entity": f"timeline:event-{index}", "callout": callout},
        "wait": "next",
    })
    return beat


def link_selector(name: str) -> str:
    """The card element a [[story/<name>]] link becomes (an <a> once installed, a greyed span while coming soon)."""
    return f'.eng-link[data-story="story/{name}"]'


def history_e2e_beats(beats: list[dict], events: list[dict], *, show_as: str = "line", show_beat: str | None = None,
                      eras: int = 0) -> dict:
    """e2e.json beat specs: the timeline.show count and pieces, each focused event's year, id and visibility, and every cue link on the card."""
    out: dict = {}
    n = len(events)
    if show_beat:
        expect = [f"{show_as}.count == {n}", "$stage.timeline:rail.exists",
                  "$stage.timeline:event-0.exists", f"$stage.timeline:event-{n - 1}.exists"]
        if eras:
            expect.append(f"$stage.timeline:era-{eras - 1}.exists")
        out[show_beat] = {"expect": expect}
    for b in beats:
        act = b.get("act")
        if isinstance(act, dict) and act.get("action") == "timeline.focus":
            i = act["args"]["index"]
            spec = out.setdefault(b["id"], {})
            spec["expect"] = [f"ev{i}.year == {events[i]['year']}", f"ev{i}.id == 'timeline:event-{i}'",
                              f"$result.timeline.focus.index == {i}"]
            spec["visible"] = [f"timeline:event-{i}"]
        links = _LINK.findall(b.get("say", ""))
        if links:
            out.setdefault(b["id"], {})["ui"] = [link_selector(x) for x in links]
    return out
