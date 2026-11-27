"""Display-math metrics through the game engine's MathJax: parse errors and rendered widths.

The reading pane typesets each $$…$$ display with MathJax into one SVG and clips
overflow, so a display line wider than the pane loses its tail — something only
the game's e2e bookcheck would otherwise see. This renders the same way (the
engine's own MathJax bundle, through node) and returns the facts: per display,
its TeX error (if any) and its width and height in ex. It judges nothing; the
caller decides what is too wide.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

ENGINE = Path(os.path.expanduser("~/games/engine"))
BUNDLE = "src/ui/mathjax-bundle.js"

_JS = r"""
// One node process, many displays: JSON array on stdin, JSON array on stdout.
import { readFileSync } from 'node:fs';
const bundle = process.argv[2];
const displays = JSON.parse(readFileSync(0, 'utf8'));
const { mathjax, TeX, SVG, liteAdaptor, RegisterHTMLHandler, AllPackages } =
  await import('file://' + bundle);
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const doc = mathjax.document('', {
  InputJax: new TeX({ packages: AllPackages.filter((p) => p !== 'bussproofs') }),
  OutputJax: new SVG({ fontCache: 'none' }),
});
const out = [];
for (const tex of displays) {
  let rec = { error: null, width: null, height: null, lines: tex.split('\\\\').length };
  try {
    const html = adaptor.outerHTML(doc.convert('$$\\displaystyle ' + tex + '$$'));
    const err = /data-mjx-error="([^"]*)"/.exec(html);
    if (err) rec.error = err[1];
    const w = /width="([0-9.]+)ex"/.exec(html), h = /height="([0-9.]+)ex"/.exec(html);
    if (w) rec.width = parseFloat(w[1]);
    if (h) rec.height = parseFloat(h[1]);
  } catch (e) {
    rec.error = String((e && e.message) || e);
  }
  rec.tex = tex;
  out.push(rec);
}
process.stdout.write(JSON.stringify(out, null, 1));
"""


def displays_of(markdown: str) -> list[str]:
    """The inner TeX of every $$…$$ display block of a reading markdown, in order."""
    out: list[str] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("$$"):
            rest = stripped[2:]
            if rest != "" and rest.endswith("$$") and "$$" not in rest[:-2]:
                out.append(rest[:-2].strip())
                i += 1
                continue
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().endswith("$$"):
                body.append(lines[i])
                i += 1
            if i < len(lines):
                last = lines[i].strip()[:-2].strip()
                if last:
                    body.append(last)
                out.append("\n".join(body).strip())
            i += 1
            continue
        i += 1
    return out


def display_metrics(display_tex: str | Sequence[str], *, engine: str | Path = ENGINE,
                    timeout: float = 60.0) -> list[dict]:
    """Render each display TeX in the engine's MathJax: [{tex, error, width, height, lines}].

    width/height are in ex of the engine's SVG output, lines counts `\\\\` breaks + 1;
    error is MathJax's message, or None when the TeX parses. Nothing is judged —
    the reading pane is roughly 40 ex wide at phone size, and each package sets
    its own limit.
    """
    displays = [display_tex] if isinstance(display_tex, str) else list(display_tex)
    if not displays:
        return []
    bundle = Path(engine).expanduser() / BUNDLE
    if not bundle.is_file():
        raise FileNotFoundError(f"engine MathJax bundle not found at {bundle}")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(_JS)
        js = fh.name
    try:
        proc = subprocess.run(["node", js, str(bundle.resolve())], input=json.dumps(displays),
                              capture_output=True, text=True, timeout=timeout)
    finally:
        os.unlink(js)
    if proc.returncode != 0:
        raise RuntimeError(f"node MathJax render failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout)


def markdown_metrics(markdown: str, **kw) -> list[dict]:
    """display_metrics for every $$…$$ block of a markdown — the pre-export reading check."""
    return display_metrics(displays_of(markdown), **kw)
