"""Reading the *shape* of a source without reading the source.

A 300-page PDF is perhaps a million tokens. Planning work out of it must not
cost a million tokens, and it does not have to: what a planner needs is the
table of contents, not the book. This module turns any attached file — or a
fetched page — into three cheap primitives:

* `outline()` — the heading or bookmark tree, with an anchor on every node.
* `search()`  — grep the decoded text, returning anchors rather than pages.
* `peek()`    — a bounded excerpt at one anchor, never the whole file.

The survey agent gets exactly these three and nothing else, so "do not read
the whole document" is a property of the tool surface rather than a request in
a prompt that a model may ignore.

Decoding happens once. The text of a PDF is extracted into a sidecar under
`.dex-index/`, together with the offsets each page starts at, so the second
question about a document costs a file read instead of another parse.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("dex.sources")

#: Where decoded text and offset maps live, beside the sources themselves.
INDEX_DIR = ".dex-index"

#: Most an excerpt may return. Large enough to judge a section by, far too
#: small to walk a document through in pieces.
PEEK_LIMIT = 4000

#: Most hits one search returns. A pattern matching everything is a bad
#: pattern, and truncating says so more usefully than 10,000 lines would.
SEARCH_LIMIT = 40

#: Headings deeper than this are detail, not structure.
MAX_DEPTH = 4


# --------------------------------------------------------------------- anchors


@dataclass(frozen=True)
class Anchor:
    """Where in a source something is, in whatever terms that source has.

    A PDF has pages, a spreadsheet has sheets, a web page has a URL and a
    fragment, and a text file has lines. Rather than flatten those into one
    lowest common denominator — a byte offset means nothing to a reader and
    nothing to the viewer either — each is kept in its own terms and `label`
    carries the human reading of it.
    """

    source: str
    label: str = ""
    page: int | None = None
    end_page: int | None = None
    line: int | None = None
    end_line: int | None = None
    heading: str = ""
    sheet: str = ""
    url: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "label": self.label or self.describe()}
        for key, value in (
            ("page", self.page), ("endPage", self.end_page),
            ("line", self.line), ("endLine", self.end_line),
            ("heading", self.heading), ("sheet", self.sheet), ("url", self.url),
        ):
            if value:
                out[key] = value
        return out

    @staticmethod
    def from_json(raw: dict[str, Any]) -> "Anchor":
        return Anchor(
            source=str(raw.get("source", "")),
            label=str(raw.get("label", "")),
            page=_int_or_none(raw.get("page")),
            end_page=_int_or_none(raw.get("endPage")),
            line=_int_or_none(raw.get("line")),
            end_line=_int_or_none(raw.get("endLine")),
            heading=str(raw.get("heading", "")),
            sheet=str(raw.get("sheet", "")),
            url=str(raw.get("url", "")),
        )

    def describe(self) -> str:
        """What this reads as when nothing gave it a label."""
        if self.sheet:
            return f"sheet {self.sheet}"
        if self.page and self.end_page and self.end_page != self.page:
            return f"pp. {self.page}–{self.end_page}"
        if self.page:
            return f"p. {self.page}"
        if self.line and self.end_line and self.end_line != self.line:
            return f"lines {self.line}–{self.end_line}"
        if self.line:
            return f"line {self.line}"
        if self.heading:
            return self.heading
        return self.source or self.url

    def fragment(self) -> str:
        """The URL fragment that opens this spot in the preview pane.

        `#page=` is the PDF viewer's own parameter, which every engine with a
        built-in viewer honours; a line number is dex's convention for the text
        views. Empty when the anchor names no position.
        """
        if self.page:
            return f"#page={self.page}"
        if self.line:
            return f"#L{self.line}"
        return ""


def _int_or_none(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


# --------------------------------------------------------------------- outline


@dataclass
class Node:
    """One entry in a source's structure."""

    title: str
    anchor: Anchor
    depth: int = 0
    #: Rough size of what sits under this node, in characters of decoded text.
    #: A planner splitting a document needs to know which chapter is eighty
    #: pages and which is two paragraphs.
    chars: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "depth": self.depth,
            "chars": self.chars,
            "anchor": self.anchor.to_json(),
        }


@dataclass
class Outline:
    source: str
    kind: str
    nodes: list[Node] = field(default_factory=list)
    #: Whole-source facts: page count, row count, sheet names, and so on.
    facts: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "facts": self.facts,
            "note": self.note,
            "nodes": [n.to_json() for n in self.nodes],
        }

    def render(self, limit: int = 300) -> str:
        """The outline as text, for a prompt.

        Indented rather than JSON: a model reads a tree far better as a tree,
        and this is the single largest thing the survey puts in its context.
        """
        head = [f"{self.source} — {self.kind}"]
        if self.facts:
            head.append("  " + ", ".join(f"{k}: {v}" for k, v in self.facts.items()))
        if self.note:
            head.append(f"  note: {self.note}")
        if not self.nodes:
            head.append("  (no headings found)")
        for node in self.nodes[:limit]:
            size = f"  ~{node.chars:,} chars" if node.chars else ""
            head.append(
                f"{'  ' * (node.depth + 1)}- {node.title}"
                f"  [{node.anchor.describe()}]{size}"
            )
        if len(self.nodes) > limit:
            head.append(f"  … and {len(self.nodes) - limit} more entries")
        return "\n".join(head)


# ------------------------------------------------------------------- the index


@dataclass
class Decoded:
    """A source's text, plus where each page or line begins in it."""

    text: str
    #: Character offset at which each page starts, 0-based list, 1-based pages.
    page_starts: list[int] = field(default_factory=list)

    def page_of(self, offset: int) -> int | None:
        if not self.page_starts:
            return None
        lo, hi = 0, len(self.page_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.page_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def line_of(self, offset: int) -> int:
        return self.text.count("\n", 0, offset) + 1

    def slice_for(self, anchor: Anchor) -> tuple[int, int]:
        """The character range an anchor points at."""
        if anchor.page and self.page_starts:
            start = self.page_starts[min(anchor.page, len(self.page_starts)) - 1]
            last = anchor.end_page or anchor.page
            end = (
                self.page_starts[last]
                if last < len(self.page_starts)
                else len(self.text)
            )
            return start, end
        if anchor.line:
            lines = self.text.splitlines(keepends=True)
            start = sum(len(x) for x in lines[: anchor.line - 1])
            last = anchor.end_line or len(lines)
            end = sum(len(x) for x in lines[:last])
            return start, end
        return 0, len(self.text)


def index_dir(project_dir: Path) -> Path:
    return project_dir / INDEX_DIR


def decode(project_dir: Path, name: str) -> Decoded:
    """A source's text, from the sidecar when one exists and by parsing when not."""
    target = project_dir / name
    cache = index_dir(project_dir) / f"{name}.txt"
    meta = index_dir(project_dir) / f"{name}.json"
    if cache.exists() and target.exists() and cache.stat().st_mtime >= target.stat().st_mtime:
        starts: list[int] = []
        if meta.exists():
            try:
                starts = json.loads(meta.read_text())["pageStarts"]
            except Exception:
                starts = []
        return Decoded(cache.read_text(encoding="utf-8", errors="replace"), starts)

    decoded = _decode_uncached(target)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(decoded.text, encoding="utf-8")
        meta.write_text(json.dumps({"pageStarts": decoded.page_starts}))
    except OSError:
        # An unwritable data directory is not a reason to fail the survey; it
        # only means the next question re-parses.
        log.warning("could not cache the decoding of %s", name)
    return decoded


def _decode_uncached(target: Path) -> Decoded:
    ext = target.suffix.lower().lstrip(".")
    if ext == "pdf":
        return _decode_pdf(target)
    if ext == "docx":
        return Decoded("\n".join(t for t, _ in _docx_paragraphs(target)))
    if ext in {"html", "htm"}:
        return Decoded(_html_text(target.read_bytes()))
    if ext in {"xlsx", "xlsm"}:
        return Decoded(_xlsx_text(target))
    try:
        return Decoded(target.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return Decoded("")


def _decode_pdf(target: Path) -> Decoded:
    from pypdf import PdfReader

    reader = PdfReader(str(target))
    parts: list[str] = []
    starts: list[int] = []
    total = 0
    for page in reader.pages:
        starts.append(total)
        try:
            text = page.extract_text() or ""
        except Exception:
            # One malformed page must not lose the other 299.
            text = ""
        block = text + "\n\n"
        parts.append(block)
        total += len(block)
    return Decoded("".join(parts), starts)


# ------------------------------------------------------------------ dispatch


def outline(project_dir: Path, name: str) -> Outline:
    """The structure of one attached source."""
    target = project_dir / name
    if not target.is_file():
        return Outline(source=name, kind="missing", note="no such file")
    ext = target.suffix.lower().lstrip(".")
    try:
        if ext == "pdf":
            return _outline_pdf(project_dir, name, target)
        if ext == "docx":
            return _outline_docx(project_dir, name, target)
        if ext in {"xlsx", "xlsm"}:
            return _outline_xlsx(name, target)
        if ext in {"html", "htm"}:
            return _outline_html(name, target.read_bytes())
        if ext in {"csv", "tsv"}:
            return _outline_table(name, target)
        if ext in {"md", "markdown"}:
            return _outline_markdown(project_dir, name)
        return _outline_text(project_dir, name, ext)
    except Exception as exc:
        log.exception("could not outline %s", name)
        return Outline(source=name, kind=ext or "file", note=f"could not be read: {exc}")


def _outline_pdf(project_dir: Path, name: str, target: Path) -> Outline:
    """Bookmarks when the PDF has them, page starts when it does not."""
    from pypdf import PdfReader

    reader = PdfReader(str(target))
    decoded = decode(project_dir, name)
    pages = len(reader.pages)
    nodes: list[Node] = []

    def walk(items: Iterable[Any], depth: int) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
                title = str(item.title).strip()
            except Exception:
                continue
            if title and depth <= MAX_DEPTH:
                nodes.append(
                    Node(title=title, depth=depth, anchor=Anchor(
                        source=name, page=page, heading=title,
                        label=f"p. {page} · {title}",
                    ))
                )

    try:
        walk(reader.outline, 0)
    except Exception:
        log.warning("%s has an unreadable bookmark tree", name)

    note = ""
    if not nodes:
        # No bookmarks: the first non-empty line of each page is the only
        # structure there is. Capped, because a 900-page scan listed page by
        # page is not an outline, it is the document again.
        note = "no bookmarks; listing pages by their first line"
        for n in range(1, min(pages, 120) + 1):
            start, end = decoded.slice_for(Anchor(source=name, page=n))
            first = next(
                (ln.strip() for ln in decoded.text[start:end].splitlines() if ln.strip()),
                "",
            )
            nodes.append(
                Node(title=first[:90] or f"page {n}", depth=0, anchor=Anchor(
                    source=name, page=n, label=f"p. {n}",
                ))
            )
        if pages > 120:
            note += f"; first 120 of {pages} pages"

    _size_nodes(nodes, decoded, name)
    facts: dict[str, Any] = {"pages": pages, "characters": len(decoded.text)}
    if len(decoded.text) < pages * 40:
        # Almost no extractable text: a scan. Saying so is far more useful than
        # an empty outline, because it changes what the work has to be.
        facts["text"] = "little or none — likely a scan needing OCR"
    return Outline(source=name, kind="pdf", nodes=nodes, facts=facts, note=note)


def _size_nodes(nodes: list[Node], decoded: Decoded, name: str) -> None:
    """Fill in how much text sits between each node and the next."""
    offsets: list[int] = []
    for node in nodes:
        start, _ = decoded.slice_for(node.anchor)
        offsets.append(start)
    for i, node in enumerate(nodes):
        nxt = next((o for o in offsets[i + 1:] if o > offsets[i]), len(decoded.text))
        node.chars = max(0, nxt - offsets[i])
        if node.anchor.page and i + 1 < len(nodes) and nodes[i + 1].anchor.page:
            end = nodes[i + 1].anchor.page - 1
            if end > node.anchor.page:
                # `Anchor` is frozen, so the node takes a new one rather than
                # the field being written behind the dataclass's back.
                node.anchor = replace(node.anchor, end_page=end)


def _docx_paragraphs(target: Path) -> list[tuple[str, str]]:
    """Every paragraph of a .docx as (text, style), via the XML it really is.

    Reading the zip directly rather than through python-docx: the two things
    needed here are the text and the `pStyle`, and that is forty lines of
    stdlib against another dependency.
    """
    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(target) as zf:
        try:
            xml = zf.read("word/document.xml")
        except KeyError:
            return out
    root = ET.fromstring(xml)
    for para in root.iter(f"{{{ns['w']}}}p"):
        text = "".join(t.text or "" for t in para.iter(f"{{{ns['w']}}}t"))
        style_el = para.find(f".//{{{ns['w']}}}pStyle")
        style = style_el.get(f"{{{ns['w']}}}val", "") if style_el is not None else ""
        out.append((text, style))
    return out


def _outline_docx(project_dir: Path, name: str, target: Path) -> Outline:
    paragraphs = _docx_paragraphs(target)
    decoded = decode(project_dir, name)
    nodes: list[Node] = []
    line = 0
    for text, style in paragraphs:
        line += 1
        match = re.fullmatch(r"Heading(\d)", style or "", re.IGNORECASE)
        if match and text.strip():
            depth = min(int(match.group(1)) - 1, MAX_DEPTH)
            nodes.append(
                Node(title=text.strip()[:120], depth=depth, anchor=Anchor(
                    source=name, line=line, heading=text.strip()[:120],
                    label=text.strip()[:60],
                ))
            )
    _size_nodes(nodes, decoded, name)
    return Outline(
        source=name, kind="docx", nodes=nodes,
        facts={"paragraphs": len(paragraphs), "characters": len(decoded.text)},
        note="" if nodes else "no Heading styles; the document is unstructured",
    )


def _sheet_names(target: Path) -> list[str]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(target) as zf:
        try:
            root = ET.fromstring(zf.read("xl/workbook.xml"))
        except KeyError:
            return []
    return [s.get("name", "") for s in root.iter(f"{{{ns['m']}}}sheet")]


def _xlsx_text(target: Path) -> str:
    """Shared strings only — enough to grep a workbook without a parser."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(target) as zf:
        try:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        except KeyError:
            return ""
    return "\n".join(
        "".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
        for si in root.iter(f"{{{ns['m']}}}si")
    )


def _outline_xlsx(name: str, target: Path) -> Outline:
    sheets = _sheet_names(target)
    nodes = [
        Node(title=sheet, depth=0, anchor=Anchor(
            source=name, sheet=sheet, label=f"sheet {sheet}",
        ))
        for sheet in sheets if sheet
    ]
    return Outline(
        source=name, kind="xlsx", nodes=nodes, facts={"sheets": len(sheets)},
    )


def _html_text(raw: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _outline_html(name: str, raw: bytes, url: str = "") -> Outline:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    nodes: list[Node] = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        title = tag.get_text(" ", strip=True)
        if not title:
            continue
        depth = min(int(tag.name[1]) - 1, MAX_DEPTH)
        frag = tag.get("id") or ""
        nodes.append(
            Node(title=title[:120], depth=depth, anchor=Anchor(
                source=name, heading=title[:120], url=f"{url}#{frag}" if url and frag else url,
                label=title[:60],
            ))
        )
    title_tag = soup.find("title")
    facts: dict[str, Any] = {"headings": len(nodes)}
    if title_tag:
        facts["title"] = title_tag.get_text(strip=True)[:120]
    return Outline(source=name, kind="html", nodes=nodes, facts=facts)


def _outline_table(name: str, target: Path) -> Outline:
    delim = "\t" if target.suffix.lower() == ".tsv" else ","
    with target.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter=delim)
        try:
            header = next(reader)
        except StopIteration:
            return Outline(source=name, kind="table", note="empty")
        rows = sum(1 for _ in reader)
    nodes = [
        Node(title=col.strip() or f"column {i + 1}", depth=0, anchor=Anchor(
            source=name, label=f"column “{col.strip()}”",
        ))
        for i, col in enumerate(header[:60])
    ]
    return Outline(
        source=name, kind="table", nodes=nodes,
        facts={"rows": rows, "columns": len(header)},
        note="nodes are columns, not sections",
    )


def _outline_markdown(project_dir: Path, name: str) -> Outline:
    decoded = decode(project_dir, name)
    nodes: list[Node] = []
    fenced = False
    for n, raw in enumerate(decoded.text.splitlines(), start=1):
        if raw.lstrip().startswith("```"):
            fenced = not fenced
        if fenced:
            continue
        match = re.match(r"(#{1,6})\s+(.*)", raw)
        if match:
            nodes.append(
                Node(title=match.group(2).strip()[:120],
                     depth=min(len(match.group(1)) - 1, MAX_DEPTH),
                     anchor=Anchor(source=name, line=n,
                                   heading=match.group(2).strip()[:120],
                                   label=match.group(2).strip()[:60]))
            )
    _size_nodes(nodes, decoded, name)
    return Outline(
        source=name, kind="markdown", nodes=nodes,
        facts={"lines": decoded.text.count("\n") + 1, "characters": len(decoded.text)},
    )


def _outline_text(project_dir: Path, name: str, ext: str) -> Outline:
    decoded = decode(project_dir, name)
    lines = decoded.text.splitlines()
    return Outline(
        source=name, kind=ext or "text", nodes=[],
        facts={"lines": len(lines), "characters": len(decoded.text)},
        note="plain text; use search to find structure",
    )


# ----------------------------------------------------------------- search/peek


@dataclass
class Hit:
    anchor: Anchor
    excerpt: str

    def to_json(self) -> dict[str, Any]:
        return {"anchor": self.anchor.to_json(), "excerpt": self.excerpt}


def search(
    project_dir: Path, name: str, pattern: str, limit: int = SEARCH_LIMIT
) -> list[Hit]:
    """Every match of `pattern` in a source's decoded text, as anchors.

    The excerpt is one line, not the surrounding page: a search that returned
    context would be a way to read the document a hit at a time, which is the
    thing this module exists to avoid.
    """
    decoded = decode(project_dir, name)
    try:
        rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        raise ValueError(f"bad pattern: {exc}") from exc

    hits: list[Hit] = []
    for match in rx.finditer(decoded.text):
        start = decoded.text.rfind("\n", 0, match.start()) + 1
        end = decoded.text.find("\n", match.end())
        line = decoded.text[start: end if end != -1 else len(decoded.text)]
        page = decoded.page_of(match.start())
        hits.append(Hit(
            anchor=Anchor(
                source=name, page=page,
                line=None if page else decoded.line_of(match.start()),
            ),
            excerpt=line.strip()[:200],
        ))
        if len(hits) >= limit:
            break
    return hits


def peek(project_dir: Path, name: str, anchor: Anchor, limit: int = PEEK_LIMIT) -> str:
    """A bounded excerpt at an anchor — never more than `limit` characters."""
    decoded = decode(project_dir, name)
    start, end = decoded.slice_for(anchor)
    text = decoded.text[start:end]
    if len(text) > limit:
        return text[:limit] + f"\n… truncated at {limit:,} characters"
    return text


def sources_in(project_dir: Path) -> list[str]:
    """Attached files, newest first; the index and dotfiles are not sources."""
    if not project_dir.is_dir():
        return []
    files = [
        f for f in project_dir.iterdir()
        if f.is_file() and not f.name.startswith(".")
    ]
    return [f.name for f in sorted(files, key=lambda f: -f.stat().st_mtime)]
