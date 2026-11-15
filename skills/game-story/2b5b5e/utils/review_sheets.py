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
