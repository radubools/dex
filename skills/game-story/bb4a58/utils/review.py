"""Review sheets for a story's end-to-end screenshots: each beat's desktop and phone shot side by side, small enough to open."""
from pathlib import Path


def review_sheets(package, routes=("r0",), out_dir=None, width=620, quality=70):
    """Write `<route>-<nnn>-<beat>.jpg` sheets (desktop left, phone right) from the package's e2e shots into out_dir; returns their paths.

    Shots are read from e2e/shots when it has any, else from e2e_start's
    e2e/desktop/shots and e2e/phone/shots (shots.e2e_shot_dirs). At the defaults a sheet
    is about 1250 × 640 px and well under 300 KB, so a model can read it without hitting
    a message size limit. `out_dir` defaults to pkg/.review (scratch: delete it after the
    review).
    """
    from PIL import Image

    from .shots import e2e_shot_dirs

    pkg = Path(package)
    dirs = e2e_shot_dirs(pkg)
    out = Path(out_dir) if out_dir else pkg / ".review"
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for route in routes:
        for d in sorted(p for s in dirs for p in s.glob(f"desktop-{route}-*.png")):
            a = Image.open(d).convert("RGB")
            a.thumbnail((width, width))
            name = d.name.replace("desktop-", "phone-", 1)
            ph = next((s / name for s in dirs if (s / name).exists()), d.parent / name)
            b = Image.open(ph).convert("RGB") if ph.exists() else Image.new("RGB", (10, 10), "white")
            b.thumbnail((width, a.height if a.height > 640 else 640))
            sheet = Image.new("RGB", (a.width + b.width + 10, max(a.height, b.height)), "white")
            sheet.paste(a, (0, 0))
            sheet.paste(b, (a.width + 10, 0))
            path = out / (d.stem.replace("desktop-", "", 1) + ".jpg")
            sheet.save(path, quality=quality)
            made.append(path)
    return made
