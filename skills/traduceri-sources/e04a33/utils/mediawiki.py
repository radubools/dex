"""Pull the readable article out of a MediaWiki page as ordered blocks.

A rendered wiki page is mostly not the article: maintenance banners, navigation
sidebars, navboxes, stylesheets and citation superscripts all sit in the same
container as the prose. `article_blocks` keeps the title, the headings, the
paragraphs, figure captions and the reference list, in reading order, and drops
the rest.

It returns blocks, not segments — it does no numbering, no chunking and no
cleaning beyond collapsing whitespace, so the caller stays in charge of how the
document is cut up. When the page yields no prose at all it raises
`ExtractionFailed` rather than handing back an empty document, because an empty
article is nearly always a parser that needs updating rather than a blank page.
"""

import re

import bs4

# Markup that sits inside the text worth keeping but is not part of it.
DROP_SELECTORS = (
    "sup.reference",        # [1] citation superscripts
    "sup.noprint",          # [dead link] and friends
    "span.mw-editsection",  # the [edit] link beside every heading
    "span.Z3988",           # invisible COinS citation metadata
    "style",
)


class ExtractionFailed(ValueError):
    """Raised when a page does not look like a MediaWiki article at all."""


def clean_text(element) -> str:
    """Return an element's visible text with non-breaking and zero-width spaces normalised."""
    text = element.get_text().replace(" ", " ").replace("​", "")
    return re.sub(r"\s+", " ", text).strip()


def heading_level(div, default: int = 2) -> int:
    """Read the heading level off a `mw-heading<N>` wrapper, falling back to `default`."""
    for name in div.get("class", []):
        match = re.fullmatch(r"mw-heading(\d)", name)
        if match:
            return int(match.group(1))
    return default


def article_blocks(html: str, drop: tuple[str, ...] = DROP_SELECTORS) -> list[dict]:
    """Return the article's title, headings, paragraphs, captions and references, in order.

    Each block is `{kind, level, section, subsection, anchor, text}`, where `kind`
    is `"heading"` or `"prose"`. `section` is the enclosing level-2 heading and
    `subsection` the nested level-3-or-deeper one, both as displayed; `anchor` is
    the heading's `id`, which on a language-converted wiki (zh-cn, sr-el) differs
    from the displayed text and is what a URL fragment has to use.
    """
    soup = bs4.BeautifulSoup(html, "html.parser")
    body = soup.select_one("#mw-content-text div.mw-parser-output")
    if body is None:
        raise ExtractionFailed("no #mw-content-text div.mw-parser-output in the page")
    for selector in drop:
        for tag in body.select(selector):
            tag.decompose()

    title = soup.select_one("h1#firstHeading")
    if title is None:
        raise ExtractionFailed("no h1#firstHeading in the page")

    where = {"section": "", "subsection": "", "anchor": ""}
    out: list[dict] = [dict(where, kind="heading", level=1, text=clean_text(title))]

    def prose(text: str) -> None:
        if text:
            out.append(dict(where, kind="prose", text=text))

    for child in body.find_all(recursive=False):
        classes = child.get("class") or []
        if "mw-heading" in classes:
            inner = child.find(["h2", "h3", "h4", "h5", "h6"])
            level = heading_level(child)
            text = clean_text(child)
            where["anchor"] = (inner.get("id") if inner is not None else "") or text
            if level <= 2:
                where["section"], where["subsection"] = text, ""
            else:
                where["subsection"] = text
            out.append(dict(where, kind="heading", level=level, text=text))
        elif child.name == "p":
            prose(clean_text(child))
        elif child.name == "figure":
            caption = child.find("figcaption")
            prose(clean_text(caption) if caption is not None else "")
        elif "reflist" in classes:
            for item in child.select("li"):
                backlink = item.select_one("span.mw-cite-backlink")
                if backlink is not None:
                    backlink.decompose()
                prose(clean_text(item))

    if not any(block["kind"] == "prose" for block in out):
        raise ExtractionFailed("the page parsed to zero prose blocks")
    return out
