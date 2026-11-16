"""Contact sheets for reviewing e2e screenshots: tile shots into labelled pages."""
from pathlib import Path

from PIL import Image, ImageDraw


def contact_sheet(files, out, cell=(640, 400), cols=3, pad=6):
    """Tile `files` (image paths) into one sheet scaled to `cell`, `cols` wide, labelled per tile.

    Writes the sheet to `out` and returns it as a Path. `files` may mix viewports;
    callers slice their shot lists into pages and call this once per page.
    """
    files = [Path(f) for f in files]
    if not files:
        raise ValueError("no files to tile")
    rows = -(-len(files) // cols)
    w, h = cell
    sheet = Image.new("RGB", (cols * (w + pad) + pad, rows * (h + pad + 18) + pad), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(files):
        im = Image.open(f).convert("RGB").resize(cell)
        x = pad + (i % cols) * (w + pad)
        y = pad + (i // cols) * (h + pad + 18)
        sheet.paste(im, (x, y))
        draw.text((x + 2, y + h + 2), f.stem, fill=(220, 220, 220))
    out = Path(out)
    sheet.save(out)
    return out


#: Card-geometry defaults per viewport for Model Workshop's story card.
_CARD_GEOM = {
    "desktop": {"top": 78, "x": (12, 388), "ywin": (92, 600)},
    "phone": {"top": 112, "x": (8, 382), "ywin": (125, 640)},
}


def _row_is_gold(px, gold, tol):
    return all(abs(int(c) - g) <= tol for c, g in zip(px, gold))


def _card_bottom(a, x0, x1, ymin, ymax, gold, tol):
    """Lowest y in [ymin, ymax) whose row is a continuous gold border across [x0, x1)."""
    best = None
    for y in range(ymin, ymax):
        hits = sum(1 for x in range(x0, x1) if _row_is_gold(a[y, x], gold, tol))
        if hits / (x1 - x0) > 0.8:
            best = y
    return best


def card_bottom(package, shot):
    """The y of the story card's bottom border in one e2e screenshot (None if not found)."""
    from PIL import Image
    import numpy as np
    vp = Path(shot).stem.split("-")[0]
    g = _CARD_GEOM.get(vp)
    if g is None:
        return None
    a = np.asarray(Image.open(shot).convert("RGB"))
    return _card_bottom(a, *g["x"], *g["ywin"], (240, 195, 90), 45)


def card_strips(package, viewports=("desktop", "phone"), face="main", out_dir=None,
                cols=3, per_page=9):
    """Crop each e2e screenshot's story card at native scale and tile the strips into
    labelled sheets; returns the sheet paths.

    `package` is a story package with an `e2e/<viewport>/shots/` tree; `face` is
    "main" (the cue card) or "more" (the ? panel). Use it to review a diff that
    only touches the card chrome - e.g. the book button a reading wiring adds -
    where tiling whole frames (`contact_sheet`) makes the change too small to see.
    """
    from PIL import Image
    import numpy as np
    package, out_dir = Path(package), Path(out_dir or (Path(package) / ".review"))
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if face == "main" else "-more"
    sheets, items = [], []
    for vp in viewports:
        g = _CARD_GEOM[vp]
        shots = sorted((package / "e2e" / vp / "shots").glob(f"{vp}-*{suffix or ''}.png"))
        if face == "main":
            shots = [f for f in shots if not f.stem.endswith("-more")]
        for f in shots:
            a = np.asarray(Image.open(f).convert("RGB"))
            cb = _card_bottom(a, *g["x"], *g["ywin"], (240, 195, 90), 45)
            cb = cb if cb and cb > g["top"] + 80 else g["top"] + 300
            strip = Image.open(f).convert("RGB").crop(
                (0, g["top"], g["x"][1] + 6, min(cb + 10, a.shape[0] - 1)))
            items.append((f"{vp}:{f.stem.replace(vp + '-', '')}", strip))
    for p in range(-(-len(items) // per_page)):
        page = items[p * per_page:(p + 1) * per_page]
        if not page:
            continue
        w = max(s.width for _, s in page)
        sheets.append(contact_sheet_with_strips(page, out_dir / f"card-strips-{p + 1}.png", w, cols))
    return sheets


def contact_sheet_with_strips(items, out, w, cols):
    """Tile pre-cropped (label, image) pairs into one labelled sheet at native scale."""
    from PIL import Image, ImageDraw
    pad = 8
    rh = max(s.height for _, s in items) + 24
    rows = -(-len(items) // cols)
    im = Image.new("RGB", (cols * (w + pad) + pad, rows * (rh + pad) + pad), (22, 22, 26))
    d = ImageDraw.Draw(im)
    for i, (label, s) in enumerate(items):
        x = pad + (i % cols) * (w + pad)
        y = pad + (i // cols) * (rh + pad)
        im.paste(s, (x, y))
        d.text((x + 2, y + s.height + 4), label, fill=(235, 235, 220))
    out = Path(out)
    im.save(out)
    return out
