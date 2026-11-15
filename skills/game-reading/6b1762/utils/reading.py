"""Readings: the original text behind a lesson, for the games in ~/games.

A reading is a package of kind "reading", one per story and named after it
(`reading/llm-svd` for `story/llm-svd`): the passages of the book or paper the
lesson retells, as written there, with their equations in LaTeX, their figures
and the publications they cite, each linked. The game shows it behind the
story card's book button (engine design/27_reading.md); a beat names the
passage it is about.

    from utils import reading as rd
    rd.write_reading(pkg, name="llm-svd", story="story/llm-svd", work={...},
                     passages=[{"id", "title", "where", "markdown", "refs"}], references={...})
    rd.validate_reading(pkg)          # {ok, errors, warnings}: what "done" means
    rd.check_links(pkg)               # every reference URL fetched: [{id, url, status, ok}]
    rd.pdf_figure(pdf, page, out, crop=(x0, y0, x1, y1))   # a figure cut from a PDF page
    rd.outline(pkg)                   # the passages as one markdown file, for review
    rd.export_reading(pkg, game_root) # what the game's pull does: install and index

reading.json:
    {schema 1, name, story, work: {title, authors, year, edition?, publisher?, url?},
     passages: [{id, title, where?, markdown, refs: [refId]}],
     references: {refId: {text, url}}}
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

KIND = "reading"
FILE = "reading.json"
FIGURES = "figures"
SCHEMA = 1
EXPORT_SUBDIR = Path("public") / "assets"

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REF_ID = re.compile(r"^[a-z0-9]+(?:[-_.][a-z0-9]+)*$", re.I)
STORY = re.compile(r"^story/[a-z0-9]+(-[a-z0-9]+)*$")
#: Markdown images and links: ![alt](path "title") and [text](href "title").
IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
LINK = re.compile(r"(?<!!)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
FIGURE_TYPES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp", b"<svg": ".svg", b"<?xm": ".svg"}
FIGURE_MAX = 2_000_000
#: A passage is an excerpt, not a chapter.
WORDS_SOFT = 1500
UA = "dex-game-reading/1 (checking citation links)"


# ---------------------------------------------------------------- the package

def _figures_of(passages: Iterable[dict]) -> list[str]:
    seen: list[str] = []
    for p in passages:
        for _alt, src in IMAGE.findall(_without_code(p.get("markdown") or "")):
            if src not in seen:
                seen.append(src)
    return seen


def write_reading(package: str | Path, *, name: str, story: str, work: dict, passages: list[dict],
                  references: dict[str, dict], tags: Iterable[str] = ()) -> dict:
    """Write reading.json and manifest.json; returns the manifest.

    The figures the passages show must already be in the package (under
    figures/); they are listed in the manifest's files. Refuses a reading
    that does not validate.
    """
    pkg = Path(package)
    pkg.mkdir(parents=True, exist_ok=True)
    body = {"schema": SCHEMA, "name": name, "story": story, "work": work,
            "passages": [{"id": p["id"], "title": p.get("title", ""), **({"where": p["where"]} if p.get("where") else {}),
                          "markdown": p.get("markdown", ""), "refs": list(p.get("refs") or [])} for p in passages],
            "references": references}
    (pkg / FILE).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    figures = [f for f in _figures_of(body["passages"]) if (pkg / f).is_file()]
    tagged = {t for t in tags if ":" in t} | {"type:reading", f"story:{name}"}
    man = {
        "name": name, "kind": KIND, "title": f"Reading — {work.get('title', name)}", "story": story,
        "files": [FILE, *figures], "passages": len(body["passages"]), "references": len(references),
        "reading": hashlib.sha256((pkg / FILE).read_bytes()).hexdigest(),
        "tags": sorted(tagged), "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (pkg / "manifest.json").write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")
    rep = validate_reading(pkg)
    if not rep["ok"]:
        raise ValueError("the reading does not validate: " + "; ".join(rep["errors"][:8]))
    return man


def read(package: str | Path) -> dict:
    """The package's reading.json."""
    return json.loads((Path(package) / FILE).read_text())


def passage_ids(package: str | Path) -> list[str]:
    """The ids of a reading's passages, in order: what a story's beats may name."""
    return [p["id"] for p in read(package).get("passages", [])]


# ---------------------------------------------------------------- validation

def _without_code(md: str) -> str:
    """The markdown with fenced and inline code taken out (its $ and [ ] are code)."""
    md = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1\s*$", "", md)
    return re.sub(r"`[^`\n]*`", "", md)


def math_problems(md: str) -> list[str]:
    """Unbalanced maths in a passage: a $$ or a single $ left open."""
    text = _without_code(md).replace("\\$", "")
    problems = []
    if text.count("$$") % 2:
        problems.append("a $$ is not closed")
    singles = re.sub(r"\$\$[\s\S]*?\$\$", "", text)
    # Paired as the game pairs them: a $ before a non-space opens, the next $
    # after a non-space closes. "$5 and $6" pairs nothing and is money; a $
    # before a letter or a backslash that nothing closes is math left open.
    i = 0
    while i < len(singles):
        if singles[i] != "$":
            i += 1
            continue
        j = singles.find("$", i + 1)
        if j > i + 1 and not singles[i + 1].isspace() and not singles[j - 1].isspace():
            i = j + 1
            continue
        if i + 1 < len(singles) and (singles[i + 1].isalpha() or singles[i + 1] == "\\"):
            problems.append(f"a single $ is not closed: {singles[i:i + 30]!r}")
            break
        i += 1
    return problems


def _figure_type(path: Path) -> str | None:
    head = path.read_bytes()[:5]
    for magic, ext in FIGURE_TYPES.items():
        if head.startswith(magic):
            return ext
    return None


def validate_reading(package: str | Path) -> dict:
    """{ok, errors, warnings}: a reading the game can show, every citation resolvable."""
    pkg = Path(package)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        man = json.loads((pkg / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"manifest.json: {exc}"], "warnings": []}
    if man.get("kind") != KIND:
        errors.append(f"kind is {man.get('kind')!r}, not {KIND!r}")
    name = str(man.get("name", ""))
    if not NAME.match(name):
        errors.append("name must be kebab-case")
    if not any(str(t).startswith("type:") for t in man.get("tags", [])):
        errors.append("tags need a type: tag (type:reading)")
    try:
        r = read(pkg)
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": errors + [f"{FILE}: {exc}"], "warnings": warnings}
    if man.get("reading") != hashlib.sha256((pkg / FILE).read_bytes()).hexdigest():
        errors.append(f"{FILE} changed since manifest.json was written (write_reading again)")
    if r.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if r.get("name") != name:
        errors.append(f"{FILE} name {r.get('name')!r} is not the manifest's {name!r}")
    if not STORY.match(str(r.get("story", ""))):
        errors.append("story must be story/<name>")
    work = r.get("work")
    if not isinstance(work, dict) or not str(work.get("title", "")).strip():
        errors.append("work needs a title")
    elif not work.get("authors"):
        warnings.append("work names no authors")
    refs = r.get("references") or {}
    if not isinstance(refs, dict):
        errors.append("references must be an object of id -> {text, url}")
        refs = {}
    for rid, ref in refs.items():
        if not REF_ID.match(rid):
            errors.append(f"reference id {rid!r} must look like 'golub-kahan-1965'")
        if not isinstance(ref, dict) or not str(ref.get("text", "")).strip():
            errors.append(f"reference {rid}: needs its citation text")
            continue
        url = str(ref.get("url") or "")
        if not url:
            warnings.append(f"reference {rid}: no url yet (find the publication's DOI, arXiv or publisher page)")
        elif not re.match(r"^https?://", url):
            errors.append(f"reference {rid}: url must be http(s)")
    passages = r.get("passages")
    if not isinstance(passages, list) or not passages:
        errors.append("needs at least one passage")
        passages = []
    ids: set[str] = set()
    cited: set[str] = set()
    files = set(man.get("files", []))
    for i, p in enumerate(passages):
        at = f"passages[{i}]" + (f" ({p.get('id')})" if isinstance(p, dict) and p.get("id") else "")
        if not isinstance(p, dict):
            errors.append(f"{at}: must be an object")
            continue
        pid = p.get("id")
        if not isinstance(pid, str) or not NAME.match(pid):
            errors.append(f"{at}: id must be kebab-case")
        elif pid in ids:
            errors.append(f"{at}: duplicate id")
        ids.add(str(pid))
        if not str(p.get("title", "")).strip():
            errors.append(f"{at}: needs a title")
        md = p.get("markdown")
        if not isinstance(md, str) or not md.strip():
            errors.append(f"{at}: markdown is empty")
            continue
        errors += [f"{at}: {m}" for m in math_problems(md)]
        words = len(re.findall(r"\w+", _without_code(md)))
        if words > WORDS_SOFT:
            warnings.append(f"{at}: {words} words; a passage is an excerpt (aim for at most {WORDS_SOFT})")
        plain = _without_code(md)
        for _alt, src in IMAGE.findall(plain):
            f = pkg / src
            if re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I) or src.startswith("/") or ".." in src.split("/"):
                errors.append(f"{at}: image {src} must be a path inside the package (figures/…)")
            elif not f.is_file():
                errors.append(f"{at}: image {src} is missing")
            elif src not in files:
                errors.append(f"{at}: image {src} is not in the manifest's files (write_reading again)")
            elif _figure_type(f) is None:
                errors.append(f"{at}: image {src} is not a PNG, JPEG, WebP or SVG")
            elif f.stat().st_size > FIGURE_MAX:
                errors.append(f"{at}: image {src} is {f.stat().st_size // 1000} KB (max {FIGURE_MAX // 1000} KB)")
        for _text, href in LINK.findall(plain):
            if href.startswith("#ref:"):
                rid = href[5:]
                cited.add(rid)
                if rid not in refs:
                    errors.append(f"{at}: cites #ref:{rid}, which is not in references")
            elif not re.match(r"^(https?:|mailto:|#)", href):
                errors.append(f"{at}: link {href} must be http(s), mailto, #ref:<id> or #anchor")
        for rid in p.get("refs") or []:
            cited.add(rid)
            if rid not in refs:
                errors.append(f"{at}: refs names {rid}, which is not in references")
    for rid in refs:
        if rid not in cited:
            warnings.append(f"reference {rid}: no passage cites it")
    for f in man.get("files", []):
        if not (pkg / f).is_file():
            errors.append(f"{f}: listed but missing")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------- links

def check_links(package: str | Path, *, timeout: float = 15.0) -> list[dict]:
    """Fetch every reference URL (and the work's): [{id, url, status, ok, final}].

    A citation must lead to the publication. A DOI that resolves, an arXiv
    abstract, the publisher's or the author's page all count; a 404 or a
    search page does not. Some publishers refuse scripts (403): open those
    with your web fetch tool before you trust or drop them.
    """
    r = read(package)
    targets = [("work", (r.get("work") or {}).get("url"))] + [(rid, ref.get("url")) for rid, ref in (r.get("references") or {}).items()]
    out = []
    for rid, url in targets:
        if not url:
            continue
        status, final = None, None
        for method in ("HEAD", "GET"):
            req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "*/*"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status, final = resp.status, resp.geturl()
                break
            except urllib.error.HTTPError as exc:
                status, final = exc.code, url
                if method == "HEAD" and exc.code in (403, 405, 501):
                    continue
                break
            except Exception as exc:  # noqa: BLE001 — reported, the rest go on
                status, final = f"error: {str(exc)[:80]}", url
                break
        out.append({"id": rid, "url": url, "status": status, "ok": isinstance(status, int) and status < 400, "final": final})
    return out


# ---------------------------------------------------------------- figures

def pdf_page_png(pdf: str | Path, page: int, out: str | Path, *, dpi: int = 150) -> Path:
    """One PDF page (1-based, the PDF's own numbering) rendered to a PNG, to look at or crop."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(tmp) / "page"
        subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile", str(pdf), str(stem)],
                       check=True, capture_output=True)
        shutil.move(str(stem.with_suffix(".png")), out)
    return out


def pdf_text(pdf: str | Path, page: int) -> str:
    """A PDF page's text, to find the page a passage or figure is on."""
    res = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
                         check=True, capture_output=True, text=True)
    return res.stdout


def pdf_pages_text(pdf: str | Path, first: int, last: int, *, drop_lines: Iterable[str] = ()) -> str:
    """PDF pages first..last (the PDF's numbering) as one NFC-normalised line of prose, page-number lines and `drop_lines` (running heads) removed."""
    import unicodedata
    res = subprocess.run(["pdftotext", "-f", str(first), "-l", str(last), str(pdf), "-"],
                         check=True, capture_output=True, text=True)
    drop = {d.strip() for d in drop_lines}
    lines = [ln for ln in res.stdout.splitlines() if ln.strip() not in drop and not re.fullmatch(r"\d{1,4}", ln.strip())]
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", " ".join(lines)))


def span(text: str, start: str, end: str) -> str:
    """The verbatim text from marker `start` through the first `end` after it; ValueError if `start` is not found exactly once or `end` is missing."""
    if text.count(start) != 1:
        raise ValueError(f"start marker found {text.count(start)} times: {start!r}")
    i = text.index(start)
    j = text.find(end, i)
    if j < 0:
        raise ValueError(f"end marker not found after start: {end!r}")
    return text[i:j + len(end)]


def numbered_equations(md: str) -> dict[str, str]:
    """Display equations `$$… (9.4)$$` in a markdown text, by number ('9.4' -> the LaTeX inside the $$); the first wins."""
    out: dict[str, str] = {}
    for m in re.finditer(r"\$\$(.+?)\$\$", md, re.S):
        num = re.search(r"\((\d+\.\d+)\)\s*$", m.group(1))
        if num and num.group(1) not in out:
            out[num.group(1)] = m.group(1).strip()
    return out


def pdf_figure(pdf: str | Path, page: int, out: str | Path, *, crop: tuple[float, float, float, float] | None = None,
               dpi: int = 150, max_width: int = 1400, trim: bool = True) -> Path:
    """A figure cut from a PDF page into a PNG at `out` (figures/<name>.png).

    `crop` is (x0, y0, x1, y1) as fractions of the page (0..1, from the top
    left): render the page with `pdf_page_png`, look at it, and give the
    figure's box. `trim` removes the white margin left around it. Keep the
    caption out of the image: it goes in the markdown, as the image's alt text.
    """
    from PIL import Image, ImageChops  # noqa: PLC0415

    out = Path(out)
    with tempfile.TemporaryDirectory() as tmp:
        page_png = pdf_page_png(pdf, page, Path(tmp) / "p.png", dpi=dpi)
        img = Image.open(page_png).convert("RGB")
        if crop:
            w, h = img.size
            x0, y0, x1, y1 = crop
            img = img.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
        if trim:
            bg = Image.new("RGB", img.size, (255, 255, 255))
            box = ImageChops.difference(img, bg).convert("L").point(lambda v: 255 if v > 12 else 0).getbbox()
            if box:
                pad = 8
                img = img.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad)))
        if img.width > max_width:
            img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, optimize=True)
    return out


# ---------------------------------------------------------------- review and install

def outline(package: str | Path) -> str:
    """The reading as one markdown document: each passage, then its references. Write it to previews/outline.md."""
    r = read(package)
    work = r.get("work") or {}
    lines = [f"# Reading for {r.get('story')}", "", f"*{work.get('title', '')}*, {', '.join(work.get('authors') or [])} ({work.get('year', '')})", ""]
    refs = r.get("references") or {}
    for p in r.get("passages", []):
        lines += [f"## {p.get('title')}  (`{p.get('id')}`)", ""]
        if p.get("where"):
            lines += [f"_{p['where']}_", ""]
        lines += [p.get("markdown", ""), ""]
        cited = list(dict.fromkeys([*(p.get("refs") or []), *[h[5:] for _t, h in LINK.findall(p.get("markdown", "")) if h.startswith("#ref:")]]))
        if cited:
            lines += ["References:", ""] + [f"- {refs.get(c, {}).get('text', c)} <{refs.get(c, {}).get('url', '')}>" for c in cited] + [""]
    return "\n".join(lines)


def export_reading(package: str | Path, game_root: str | Path) -> dict:
    """Install a reading into `<game_root>/public/assets/reading/<name>/` and rebuild the index."""
    pkg = Path(package)
    rep = validate_reading(pkg)
    if not rep["ok"]:
        raise ValueError("reading does not validate: " + "; ".join(rep["errors"][:5]))
    man = json.loads((pkg / "manifest.json").read_text())
    dest = Path(game_root) / EXPORT_SUBDIR / KIND / man["name"]
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel in man["files"]:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pkg / rel, dest / rel)
    entry = {k: v for k, v in man.items() if k != "created"}
    entry["base"] = f"assets/{KIND}/{man['name']}/"
    entry["hashes"] = {rel: hashlib.sha256((dest / rel).read_bytes()).hexdigest()[:12] for rel in man["files"]}
    entry["exported"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = dest / f".manifest.{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(dest / "manifest.json")
    from . import catalog  # game-mesh, enabled alongside this skill  # noqa: PLC0415

    catalog.rebuild_index(game_root)
    return entry


__all__ = [
    "KIND", "FILE", "FIGURES", "SCHEMA", "write_reading", "read", "passage_ids", "validate_reading", "math_problems",
    "check_links", "pdf_page_png", "pdf_text", "pdf_figure", "outline", "export_reading",
]

