"""Story catalogs: which lessons a game has, how they are grouped, laid out and ordered.

A catalog is one package of kind "catalog" (`catalog.json` + `manifest.json`)
per game. It is the game's master view: **sections** (a game's kinds of
lesson: "Machine learning", "Statistics", "Algorithms and data structures"),
each with a **layout** and **tiles**. A tile points at a story
(`story/<name>`) and carries what the game shows for it: a display title, an
icon, a one-line blurb and a thumbnail.

| layout | how the tiles sit | order |
|---|---|---|
| `grid` | at `at: [col, row]` (or filling row by row, `columns` wide) | `after` lists, or none |
| `linear` | a winding path, `perRow` to a row, turning back each row | each tile after the one before |
| `tree` | mind maps: each hub (a tile with no `parent`) centres its own cluster, branches radiating out | a branch after its parent |

The sections themselves are laid out the same way on the master view, by the
catalog's own `layout`.

`navigation` says how the player reaches the stories:

| navigation | the hierarchy |
|---|---|
| `map` (default) | the master view of sections, then a section's map |
| `subjects` | main menu -> subject -> classroom -> story: each section is a subject picked from the menu, and opens a classroom of only its own tiles |

Under `subjects` no section has `after` or `parent` (subjects are chosen
freely and do not nest), and each has an icon and a blurb for the menu.

A tile whose story the game does not have yet is a **placeholder**: the game
shows it ("coming soon") but cannot play it. That is how a new set of lessons
starts: the catalog gets its section and placeholder tiles first, the game
pulls it, and the stories follow, each turning its tile live in the game as
soon as it is installed (no second catalog change needed). Mark such tiles
`"placeholder": true` so the check expects their stories to be missing;
`mark_ready` tidies the flags away the next time the catalog is edited.

The game's engine owns the format (`@games/engine/story`, `validateCatalog`,
`layoutSection`): `validate_catalog(..., game_root)` runs the engine's own
check through node (catalogcheck.mjs), so a catalog is valid here exactly when
the game would accept it. Without a game root only the basic structure is
checked.

    from utils import story_catalog as sc
    cat = sc.installed_catalog(GAME) or sc.new_catalog("model-workshop", "Model Workshop")
    sc.add_section(cat, sc.section("statistics", "Statistics", layout="linear", icon="📊",
                                   tiles=[sc.placeholder("mean", "The mean"), ...]))
    sc.write_catalog(TASK_DIR, cat)
    print(sc.validate_catalog(TASK_DIR, GAME))
    sc.preview_catalog(TASK_DIR, GAME)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

KIND = "catalog"
CATALOG_FILE = "catalog.json"
SCHEMA = 1
LAYOUTS = ("linear", "grid", "tree")
#: How the player reaches the stories: the master map, or a main menu of subjects.
NAVIGATIONS = ("map", "subjects")
EXPORT_SUBDIR = Path("public") / "assets"
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STORY = re.compile(r"^story/[a-z0-9]+(?:-[a-z0-9]+)*$")


# ------------------------------------------------------------------ building


def new_catalog(id: str, title: str, *, layout: str = "linear", blurb: str = "",
                navigation: str = "map", **fields: Any) -> dict:
    """An empty catalog: the game's id (kebab-case), title, how its sections are laid out, and its
    `navigation` (`map`, or `subjects`: each section a subject on the main menu, opening a classroom)."""
    cat = {"schema": SCHEMA, "id": id, "title": title, "blurb": blurb, "layout": layout, "sections": [], **fields}
    if navigation != "map":
        cat["navigation"] = navigation
    return cat


def tile(id: str, title: str, story: str | None = None, **fields: Any) -> dict:
    """A tile: `story` defaults to `story/<id>`. Fields: blurb, icon, thumbnail, at, parent, after, placeholder."""
    return {"id": id, "title": title, "story": story or f"story/{id}", **fields}


def placeholder(id: str, title: str, story: str | None = None, **fields: Any) -> dict:
    """A tile for a story that does not exist yet: shown as coming soon."""
    return tile(id, title, story, placeholder=True, **fields)


def section(id: str, title: str, *, layout: str = "grid", tiles: Iterable[dict] = (), **fields: Any) -> dict:
    """A section. Fields: blurb, icon, thumbnail, columns (grid), perRow (linear), variants, placeholder, at, after."""
    return {"id": id, "title": title, "layout": layout, **fields, "tiles": list(tiles)}


def find_section(cat: dict, section_id: str) -> dict | None:
    return next((s for s in cat.get("sections", []) if s.get("id") == section_id), None)


def add_section(cat: dict, sec: dict, *, replace: bool = False) -> dict:
    """Append a section; one with the same id is kept unless `replace`. Returns the catalog's section."""
    found = find_section(cat, sec["id"])
    if found and not replace:
        return found
    if found:
        cat["sections"][cat["sections"].index(found)] = sec
    else:
        cat.setdefault("sections", []).append(sec)
    return sec


def add_tiles(cat: dict, section_id: str, tiles: Iterable[dict], *, replace: bool = False) -> list[dict]:
    """Add tiles to a section (tiles whose id it has are kept, unless `replace`). Returns the section's tiles."""
    sec = find_section(cat, section_id)
    if sec is None:
        raise KeyError(f"no section {section_id!r}")
    have = {t["id"]: i for i, t in enumerate(sec.setdefault("tiles", []))}
    for t in tiles:
        if t["id"] in have:
            if replace:
                sec["tiles"][have[t["id"]]] = t
        else:
            sec["tiles"].append(t)
            have[t["id"]] = len(sec["tiles"]) - 1
    return sec["tiles"]


def story_keys(cat: dict) -> list[str]:
    """Every story the catalog points at, variants included (`story/knn`, `story/knn-medium`, …)."""
    out: list[str] = []
    for sec in cat.get("sections", []):
        suffixes = [v.get("suffix", "") for v in sec.get("variants") or []] or [""]
        for t in sec.get("tiles", []):
            out += [f"{t.get('story')}{s}" for s in suffixes]
    return out


def mark_ready(cat: dict, known: Iterable[str]) -> list[str]:
    """Clear `placeholder` on tiles whose story (every variant's) is in `known`; a section whose
    tiles are all live loses its flag too. Returns the tile ids changed."""
    known = set(known)
    changed = []
    for sec in cat.get("sections", []):
        suffixes = [v.get("suffix", "") for v in sec.get("variants") or []] or [""]
        for t in sec.get("tiles", []):
            if t.get("placeholder") and all(f"{t.get('story')}{s}" in known for s in suffixes):
                t.pop("placeholder")
                changed.append(f"{sec['id']}/{t['id']}")
        if sec.get("placeholder") and sec.get("tiles") and not any(t.get("placeholder") for t in sec["tiles"]):
            sec.pop("placeholder")
    return changed


# --------------------------------------------------------------- read/write


def read_catalog(package_or_dict: str | Path | dict) -> dict:
    if isinstance(package_or_dict, dict):
        return package_or_dict
    return json.loads((Path(package_or_dict) / CATALOG_FILE).read_text())


def installed_catalog(game_root: str | Path, name: str | None = None) -> dict | None:
    """The catalog the game has installed now (the one named `name`, or its only one): start edits from it."""
    root = Path(game_root).expanduser() / EXPORT_SUBDIR / KIND
    dirs = [root / name] if name else sorted(p for p in root.glob("*") if p.is_dir() and not p.name.startswith("."))
    for d in dirs:
        if (d / CATALOG_FILE).is_file():
            return json.loads((d / CATALOG_FILE).read_text())
    return None


def known_assets(game_root: str | Path) -> set[str]:
    """What the game has installed now: its index's `kind/name` keys."""
    index = Path(game_root).expanduser() / EXPORT_SUBDIR / "index.json"
    return set(json.loads(index.read_text()).get("assets", {})) if index.is_file() else set()


def write_catalog(package: str | Path, cat: dict, *, name: str | None = None, title: str = "",
                  files: Sequence[str] = (), tags: Sequence[str] = ()) -> Path:
    """Write `catalog.json` and `manifest.json` into `package` (refuses a catalog with structural errors).

    `name` defaults to the catalog's id (one catalog per game, named after it);
    `files` adds files of the package beside catalog.json (thumbnails, as
    `thumbs/<id>.png`). The manifest is tagged `type:catalog` (plus `tags`, each `group:value`).
    """
    name = name or cat.get("id")
    if not NAME.match(name or ""):
        raise ValueError(f"name {name!r} must be kebab-case")
    rep = _basic_checks(cat)
    if rep["errors"]:
        raise ValueError("catalog does not validate: " + "; ".join(rep["errors"]))
    pkg = Path(package)
    pkg.mkdir(parents=True, exist_ok=True)
    missing = [f for f in files if not (pkg / f).is_file()]
    if missing:
        raise ValueError(f"files not in the package: {missing}")
    (pkg / CATALOG_FILE).write_text(json.dumps(cat, indent=2, ensure_ascii=False) + "\n")
    stats = rep["stats"]
    man = {
        "name": name,
        "kind": KIND,
        "title": title or cat.get("title") or name,
        "files": [CATALOG_FILE, *files],
        "schema": cat.get("schema", SCHEMA),
        "sections": [s.get("id") for s in cat.get("sections", [])],
        "tiles": stats["tiles"],
        "placeholders": stats["placeholders"],
        "stories": story_keys(cat),
        "tags": list(dict.fromkeys(["type:catalog", *tags])),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = pkg / "manifest.json"
    path.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------- validation


def _basic_checks(cat: Any) -> dict:
    """The structure, without the engine: enough to write a package, not to call it valid."""
    errors: list[str] = []
    tiles = placeholders = 0
    if not isinstance(cat, dict):
        return {"errors": ["not an object"], "stats": {"sections": 0, "tiles": 0, "placeholders": 0}}
    if cat.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if not NAME.match(str(cat.get("id", ""))):
        errors.append("id must be kebab-case")
    if cat.get("layout", "linear") not in LAYOUTS:
        errors.append(f"layout must be one of {LAYOUTS}")
    navigation = cat.get("navigation", "map")
    if navigation not in NAVIGATIONS:
        errors.append(f"navigation must be one of {NAVIGATIONS}")
    sections = cat.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("no sections")
        sections = []
    seen = set()
    for s in sections:
        if not isinstance(s, dict):
            errors.append("a section is not an object")
            continue
        sid = s.get("id")
        if not NAME.match(str(sid or "")) or sid in seen:
            errors.append(f"section id {sid!r} must be kebab-case and unique")
        seen.add(sid)
        if s.get("layout", "grid") not in LAYOUTS:
            errors.append(f"section {sid}: layout must be one of {LAYOUTS}")
        if navigation == "subjects":
            # A subject is chosen from the menu: it waits for nothing and holds no other.
            if s.get("after"):
                errors.append(f"section {sid}: a subject is chosen from the menu, so it comes after nothing (drop its after)")
            if "parent" in s:
                errors.append(f"section {sid}: subjects do not nest (drop its parent)")
        ids = set()
        for t in s.get("tiles") or []:
            tiles += 1
            placeholders += bool(t.get("placeholder"))
            if not NAME.match(str(t.get("id", ""))) or t.get("id") in ids:
                errors.append(f"section {sid}: tile id {t.get('id')!r} must be kebab-case and unique")
            ids.add(t.get("id"))
            if not STORY.match(str(t.get("story", ""))):
                errors.append(f"section {sid}: {t.get('id')}'s story must be story/<name>")
    return {"errors": errors, "stats": {"sections": len(sections), "tiles": tiles, "placeholders": placeholders}}


def has_engine(game_root: str | Path) -> bool:
    """Whether the game has the engine (with its catalog module) and node to run it."""
    root = Path(game_root).expanduser()
    return bool(shutil.which("node")) and any(
        (p / "@games" / "engine" / "src" / "story" / "catalog.js").exists()
        for p in (root / "node_modules", root.parent / "node_modules"))


def _engine(cat: dict, game_root: str | Path, known: Iterable[str] | None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / CATALOG_FILE
        path.write_text(json.dumps(cat, ensure_ascii=False))
        cmd = ["node", str(Path(__file__).with_name("catalogcheck.mjs")), str(path),
               str(Path(game_root).expanduser().resolve())]
        if known is not None:
            kp = Path(tmp) / "known.json"
            kp.write_text(json.dumps(sorted(known)))
            cmd.append(str(kp))
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or "").strip().splitlines()[-3:]
            return {"ok": False, "errors": ["the game's engine could not check the catalog: " + " / ".join(tail)],
                    "warnings": [], "stats": {}, "layout": None}
    return json.loads(out)


def validate_catalog(package_or_dict: str | Path | dict, game_root: str | Path | None = None, *,
                     known: Iterable[str] | None = None) -> dict:
    """Check a catalog: {ok, errors, warnings, stats, engine}.

    With `game_root` (a game with the engine) it is the engine's own check;
    `known` defaults to what that game has installed, so tiles pointing at
    stories it lacks are reported (a warning: they show as coming soon, which
    is right for a placeholder and worth a look for anything else). Without a
    game, only the structure is checked and `engine` is False.
    """
    try:
        cat = read_catalog(package_or_dict)
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"cannot read {CATALOG_FILE}: {exc}"], "warnings": [], "stats": {}, "engine": False}
    errors: list[str] = []
    if not isinstance(package_or_dict, dict):
        pkg = Path(package_or_dict)
        try:
            man = json.loads((pkg / "manifest.json").read_text())
        except (OSError, ValueError):
            man = None
        if not man:
            errors.append("no manifest.json (write_catalog writes it)")
        elif man.get("kind") != KIND:
            errors.append(f"manifest kind is {man.get('kind')!r}, not {KIND!r}")
        else:
            errors += [f"manifest lists {f}, which is missing" for f in man.get("files", []) if not (pkg / f).is_file()]
    if game_root is not None and has_engine(game_root):
        rep = _engine(cat, game_root, known if known is not None else known_assets(game_root))
        return {"ok": rep["ok"] and not errors, "errors": errors + rep["errors"], "warnings": rep["warnings"],
                "stats": rep.get("stats", {}), "engine": True}
    basic = _basic_checks(cat)
    return {"ok": not (errors + basic["errors"]), "errors": errors + basic["errors"],
            "warnings": ["checked without the game's engine: pass game_root for the full check"],
            "stats": basic["stats"], "engine": False}


def layout(package_or_dict: str | Path | dict, game_root: str | Path) -> dict:
    """Where everything sits, from the engine: {master, sections: {id: {pos: {id: [x, y]}, edges, width, height}}}."""
    rep = _engine(read_catalog(package_or_dict), game_root, None)
    if not rep.get("layout"):
        raise ValueError("catalog does not validate: " + "; ".join(rep["errors"]))
    return rep["layout"]


# ------------------------------------------------------------------ reading


def outline(package_or_dict: str | Path | dict, game_root: str | Path | None = None) -> str:
    """The catalog as indented text: its navigation, sections (under `subjects`, each a subject and its
    classroom), their layout, tiles and their state (✓ in the game, … placeholder)."""
    cat = read_catalog(package_or_dict)
    have = known_assets(game_root) if game_root else None
    subjects = cat.get("navigation", "map") == "subjects"
    if subjects:
        lines = [f"{cat.get('title')} ({cat.get('id')}), navigation subjects: "
                 "main menu -> subject -> classroom -> story"]
    else:
        lines = [f"{cat.get('title')} ({cat.get('id')}), navigation map, sections laid out {cat.get('layout', 'linear')}"]
    for s in cat.get("sections", []):
        extra = "".join(f", {k} {s[k]}" for k in ("columns", "perRow") if k in s)
        if subjects:
            n = len(s.get("tiles", []))
            lines.append(f"- subject {s.get('icon', '')} {s['title']} [{s['id']}]: {s.get('blurb', '')}".rstrip(": "))
            lines.append(f"  classroom: {n} {'story' if n == 1 else 'stories'}, {s.get('layout', 'grid')}{extra}"
                         + (" (placeholder)" if s.get("placeholder") else ""))
        else:
            lines.append(f"- {s.get('icon', '')} {s['title']} [{s['id']}] {s.get('layout', 'grid')}{extra}"
                         + (" (placeholder)" if s.get("placeholder") else ""))
        if s.get("variants"):
            lines.append("  variants: " + ", ".join(f"{v['id']} ({v.get('suffix', '') or 'no suffix'})" for v in s["variants"]))
        suffixes = [v.get("suffix", "") for v in s.get("variants") or []] or [""]
        for t in s.get("tiles", []):
            depth = 0
            p = t.get("parent")
            byid = {x["id"]: x for x in s.get("tiles", [])}
            while p and depth < 8:
                depth += 1
                p = byid.get(p, {}).get("parent")
            state = "…" if t.get("placeholder") else ""
            if have is not None:
                state = "✓" if all(f"{t['story']}{x}" in have for x in suffixes) else ("…" if t.get("placeholder") else "✗ missing")
            where = f" at {t['at']}" if "at" in t else ""
            after = f" after {', '.join(t['after'])}" if t.get("after") else ""
            lines.append(f"  {'  ' * depth}{state:>2} {t.get('icon', '')} {t['title']} [{t['id']}] -> {t['story']}{where}{after}")
    return "\n".join(lines)


def catalog_packages(directory: str | Path) -> list[Path]:
    """Every catalog package at or below `directory`."""
    out = []
    for m in sorted(Path(directory).rglob("manifest.json")):
        try:
            if json.loads(m.read_text()).get("kind") == KIND:
                out.append(m.parent)
        except (OSError, ValueError):
            continue
    return out


# ------------------------------------------------------------------ preview


def preview_catalog(package: str | Path, game_root: str | Path, *, cell: int = 150) -> list[Path]:
    """Draw the master view and each section, as the engine lays them out, into `previews/`.

    One PNG per view (`previews/catalog.png`, `previews/<section>.png`): the
    review in dex, not what the game shows (the game draws its own map).
    Placeholders are dashed and grey, hubs round, the order's lines drawn.
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    pkg = Path(package)
    cat = read_catalog(pkg)
    lay = layout(cat, game_root)
    out_dir = pkg / "previews"
    out_dir.mkdir(exist_ok=True)
    try:
        font = ImageFont.load_default(size=15)
        small = ImageFont.load_default(size=12)
        big = ImageFont.load_default(size=22)
    except TypeError:  # an older Pillow
        font = small = big = ImageFont.load_default()

    def draw(name: str, title: str, items: list[dict], lay_: dict, *, hubs: set[str] = frozenset(),
             sub: dict[str, str] | None = None) -> Path:
        pad, head = 24, 48
        w = max(320, int(lay_["width"] * cell) + 2 * pad)
        h = int(lay_["height"] * cell) + 2 * pad + head
        img = Image.new("RGB", (w, h), (15, 25, 19))
        d = ImageDraw.Draw(img)
        d.text((pad, 14), title, fill=(238, 243, 238), font=big)
        at = lambda i: (pad + (lay_["pos"][i][0] + 0.5) * cell, head + pad + (lay_["pos"][i][1] + 0.5) * cell)  # noqa: E731
        for e in lay_["edges"]:
            colour = (190, 160, 60) if e["kind"] == "path" else (110, 125, 115)
            d.line([at(e["from"]), at(e["to"])], fill=colour, width=4 if e["kind"] == "path" else 3)
        for it in items:
            x, y = at(it["id"])
            r = cell * (0.43 if it["id"] in hubs else 0.39)
            box = [x - r, y - r, x + r, y + r]
            ph = it.get("placeholder")
            fill = (34, 44, 38) if ph else (24, 40, 31)
            outline_ = (120, 130, 125) if ph else (255, 210, 63)
            if it["id"] in hubs:
                d.ellipse(box, fill=fill, outline=outline_, width=3)
            else:
                d.rounded_rectangle(box, radius=14, fill=fill, outline=outline_, width=2 if ph else 3)
            words, lines_, cur = str(it.get("title", it["id"])).split(), [], ""
            for wd in words:
                if len(cur) + len(wd) + 1 > 14 and cur:
                    lines_.append(cur)
                    cur = wd
                else:
                    cur = f"{cur} {wd}".strip()
            lines_.append(cur)
            ty = y - 9 * len(lines_[:3])
            for ln in lines_[:3]:
                d.text((x, ty), ln, fill=(160, 170, 165) if ph else (238, 243, 238), font=font, anchor="mt")
                ty += 18
            note = (sub or {}).get(it["id"]) or ("coming soon" if ph else "")
            if note:
                d.text((x, y + r * 0.55), note, fill=(150, 160, 155), font=small, anchor="mt")
        path = out_dir / f"{name}.png"
        img.save(path)
        return path

    made = []
    secs = cat.get("sections", [])
    counts = {s["id"]: f"{sum(1 for t in s.get('tiles', []) if not t.get('placeholder'))} of {len(s.get('tiles', []))} live"
              for s in secs}
    if cat.get("navigation", "map") == "subjects":
        # The main menu: the subjects side by side, no order between them.
        menu = {"pos": {s["id"]: [i, 0] for i, s in enumerate(secs)}, "edges": [],
                "width": max(1, len(secs)), "height": 1}
        made.append(draw("catalog", f"{cat.get('title', '')} · main menu", secs, menu, sub=counts))
    else:
        made.append(draw("catalog", cat.get("title", ""), secs, lay["master"], sub=counts))
    for s in secs:
        hubs = {t["id"] for t in s.get("tiles", []) if not t.get("parent")} if s.get("layout") == "tree" else set()
        if s.get("tiles"):
            made.append(draw(s["id"], f"{s['title']} · {s.get('layout', 'grid')}", s["tiles"], lay["sections"][s["id"]], hubs=hubs))
    return made


# ------------------------------------------------------------------- export


def export_catalog(package: str | Path, game_root: str | Path) -> dict:
    """Copy a valid catalog package into `<game_root>/public/assets/catalog/<name>/` and rebuild the index.

    Refuses a catalog the game's engine rejects. Atomic like
    `catalog.export`: staged, then swapped in by rename. Returns the entry.
    """
    pkg = Path(package)
    rep = validate_catalog(pkg, game_root)
    if not rep["ok"]:
        raise ValueError("catalog does not validate: " + "; ".join(rep["errors"]))
    man = json.loads((pkg / "manifest.json").read_text())
    root = Path(game_root) / EXPORT_SUBDIR
    dest = root / KIND / man["name"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{man['name']}.{uuid.uuid4().hex}.tmp"
    staging.mkdir()
    for rel in man["files"]:
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pkg / rel, target)
    entry = {k: v for k, v in man.items() if k != "created"}
    entry["base"] = f"assets/{KIND}/{man['name']}/"
    entry["exported"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (staging / "manifest.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
    if dest.exists():
        old = dest.parent / f".{man['name']}.{uuid.uuid4().hex}.old"
        dest.rename(old)
        staging.rename(dest)
        shutil.rmtree(old, ignore_errors=True)
    else:
        staging.rename(dest)
    from . import catalog  # game-mesh, enabled alongside this skill

    catalog.rebuild_index(game_root)
    return entry
