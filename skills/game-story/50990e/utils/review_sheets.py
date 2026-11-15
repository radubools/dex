"""Contact sheets of a story's e2e screenshots, so the visual review of every beat at both sizes reads a few images instead of hundreds."""
from __future__ import annotations

from pathlib import Path


def contact_sheets(shots_dir: str | Path, pattern: str, out_dir: str | Path, *, per: int = 6, cols: int = 2,
                   width: int = 800, include_more: bool = False) -> list[Path]:
    """Tile the screenshots matching `pattern` in `shots_dir` into labelled JPEG sheets in `out_dir` (`-more` shots only with include_more); returns the sheet paths."""
    from PIL import Image, ImageDraw

    shots, out = Path(shots_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in shots.glob(pattern) if include_more or not f.stem.endswith("-more"))
    stem = pattern.replace("*", "_").replace("?", "_").replace("/", "_")
    sheets = []
    for k in range(0, len(files), per):
        group = files[k:k + per]
        ims = [Image.open(f).convert("RGB") for f in group]
        ims = [im.resize((width, int(im.height * width / im.width))) for im in ims]
        h = max(im.height for im in ims)
        rows = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * width, rows * (h + 24)), "white")
        d = ImageDraw.Draw(sheet)
        for i, (im, f) in enumerate(zip(ims, group)):
            x, y = (i % cols) * width, (i // cols) * (h + 24)
            d.text((x + 6, y + 4), f.stem, fill="black")
            sheet.paste(im, (x, y + 24))
        name = out / f"{stem}-{k // per:02d}.jpg"
        sheet.save(name, quality=80)
        sheets.append(name)
    return sheets
