"""Produce the source side of this widget's fixtures from real inputs.

The two `.bitext.json` files beside this script are real: their `source`
segments came out of a live crawl and a real PDF, run by this script. Only the
`target`, `note` and `suggestion` fields were written by hand afterwards, which
is what a translation task does anyway.

    python3 extract.py url  https://en.wikipedia.org/wiki/Bucharest
    python3 extract.py pdf  /path/to/book.pdf 8 8

It prints numbered segments; it does not overwrite the fixtures. Re-run it when
a fixture needs refreshing and paste the source side in.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import httpx
from bs4 import BeautifulSoup

UA = "dex-traduceri/0.1 (+https://github.com/; widget fixture extraction)"

#: Footnote and reference markers Wikipedia leaves in the flow: [12], [a].
MARKER = re.compile(r"\[\s*[0-9a-z]{1,3}\s*\]")


def tidy(text: str) -> str:
    """Collapse whitespace and drop inline reference markers."""
    return re.sub(r"\s+", " ", MARKER.sub("", text)).strip()


def from_url(url: str) -> tuple[str, list[dict]]:
    """Crawl one page into (title, blocks) in the order they are read."""
    r = httpx.get(url, timeout=30, follow_redirects=True, headers={"user-agent": UA})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # The widest `.mw-parser-output` is the article; the narrow ones are
    # hatnotes and infobox fragments that carry the same class.
    body = max(soup.select("div.mw-parser-output"), key=lambda n: len(n.get_text()))
    title = tidy(soup.select_one("h1").get_text())
    blocks: list[dict] = []
    for el in body.find_all(["h2", "h3", "p"]):
        # No separator: `get_text(' ')` pads every inline <a> and <sup>, which
        # turns "Bucharest[a]" into "Bucharest [ a ]" and survives tidying.
        text = tidy(el.get_text())
        if not text or (el.name == "p" and len(text) < 40):
            continue
        blocks.append({"kind": "heading" if el.name != "p" else "paragraph", "text": text, "ref": url})
    return title, blocks


def from_pdf(path: str, first: int, last: int, width: int | None = None) -> list[dict]:
    """Extract one page range as blocks, one per paragraph, page by page.

    `width` crops to a left-hand column. A two-column page extracted whole
    comes back with the columns interleaved line by line, which reads as
    nonsense and chunks as nonsense; crop each column and run twice.
    """
    blocks: list[dict] = []
    for page in range(first, last + 1):
        cmd = ["pdftotext", "-layout", "-f", str(page), "-l", str(page)]
        if width:
            cmd += ["-x", "0", "-y", "0", "-W", str(width), "-H", "10000"]
        out = subprocess.run([*cmd, path, "-"], capture_output=True, text=True, check=True).stdout
        for para in re.split(r"\n\s*\n", out):
            text = tidy(para)
            if len(text) < 40:
                continue
            blocks.append({"kind": "paragraph", "text": text, "ref": f"p{page}"})
    return blocks


def main() -> None:
    mode, *rest = sys.argv[1:]
    if mode == "url":
        title, blocks = from_url(rest[0])
    elif mode == "pdf":
        crop = int(rest[3]) if len(rest) > 3 else None
        title, blocks = rest[0].rsplit("/", 1)[-1], from_pdf(rest[0], int(rest[1]), int(rest[2]), crop)
    else:
        raise SystemExit(__doc__)
    print(json.dumps({"title": title}, ensure_ascii=False))
    for n, block in enumerate(blocks, 1):
        print(json.dumps({"n": n, **block}, ensure_ascii=False))


if __name__ == "__main__":
    main()
