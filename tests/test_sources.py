"""Reading a source's shape: outlines, anchors, search and bounded peeks.

The point of the module under test is that a huge document costs almost
nothing to plan from, so these check both halves of that: that the structure
comes out right, and that nothing hands back more than it promised.
"""

from __future__ import annotations

import zipfile

import pytest

from dex import sources


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


# ------------------------------------------------------------------- anchors


def test_an_anchor_describes_itself_in_the_source_s_own_terms():
    assert sources.Anchor(source="a.pdf", page=3).describe() == "p. 3"
    assert sources.Anchor(source="a.pdf", page=3, end_page=9).describe() == "pp. 3–9"
    assert sources.Anchor(source="a.md", line=12).describe() == "line 12"
    assert sources.Anchor(source="b.xlsx", sheet="Q3").describe() == "sheet Q3"


def test_an_anchor_survives_a_round_trip_through_json():
    original = sources.Anchor(
        source="a.pdf", label="pp. 3-9 \u00b7 Ch 1", page=3, end_page=9, heading="Ch 1"
    )
    assert sources.Anchor.from_json(original.to_json()) == original


def test_serialising_an_unlabelled_anchor_gives_it_one():
    """The UI prints `label` and nothing else, so it must never be empty."""
    original = sources.Anchor(source="a.pdf", page=3, end_page=9)
    assert original.to_json()["label"] == "pp. 3\u20139"
    # Everything that says *where* still round-trips untouched.
    back = sources.Anchor.from_json(original.to_json())
    assert (back.source, back.page, back.end_page) == ("a.pdf", 3, 9)


def test_a_json_anchor_tolerates_what_a_model_actually_sends():
    """Page numbers as strings, and zero standing in for "no page"."""
    anchor = sources.Anchor.from_json({"source": "a.pdf", "page": "7", "endPage": 0})
    assert anchor.page == 7
    assert anchor.end_page is None


def test_an_anchor_becomes_the_fragment_the_viewer_opens():
    assert sources.Anchor(source="a.pdf", page=4).fragment() == "#page=4"
    assert sources.Anchor(source="a.md", line=40).fragment() == "#L40"
    assert sources.Anchor(source="a.txt").fragment() == ""


# ------------------------------------------------------------------ markdown


MARKDOWN = """\
# Title

intro

## First part

body of the first part

### Detail

more

## Second part

body
"""


def test_markdown_headings_become_a_tree_with_line_anchors(data):
    (data / "doc.md").write_text(MARKDOWN)
    outline = sources.outline(data, "doc.md")

    assert outline.kind == "markdown"
    assert [(n.title, n.depth) for n in outline.nodes] == [
        ("Title", 0), ("First part", 1), ("Detail", 2), ("Second part", 1),
    ]
    assert [n.anchor.line for n in outline.nodes] == [1, 5, 9, 13]


def test_a_heading_inside_a_fence_is_not_a_heading(data):
    """A `#` in a shell block is a comment, and listing it as a section is noise."""
    (data / "doc.md").write_text("# Real\n\n```bash\n# not a heading\n```\n\n## Also real\n")
    titles = [n.title for n in sources.outline(data, "doc.md").nodes]
    assert titles == ["Real", "Also real"]


def test_each_node_carries_how_much_sits_under_it(data):
    (data / "doc.md").write_text(MARKDOWN)
    nodes = sources.outline(data, "doc.md").nodes
    assert all(n.chars > 0 for n in nodes[:-1])


# --------------------------------------------------------------------- tables


def test_a_csv_reports_its_columns_and_row_count(data):
    (data / "t.csv").write_text("name,role,city\nada,eng,london\nalan,math,cambridge\n")
    outline = sources.outline(data, "t.csv")
    assert outline.facts == {"rows": 2, "columns": 3}
    assert [n.title for n in outline.nodes] == ["name", "role", "city"]


def test_an_empty_csv_says_so_rather_than_raising(data):
    (data / "t.csv").write_text("")
    assert sources.outline(data, "t.csv").note == "empty"


# ----------------------------------------------------------------------- html


def test_html_headings_become_a_tree(data):
    (data / "p.html").write_text(
        "<title>Doc</title><h1>One</h1><p>x</p><h2>Two</h2><script>ignored()</script>"
    )
    outline = sources.outline(data, "p.html")
    assert [(n.title, n.depth) for n in outline.nodes] == [("One", 0), ("Two", 1)]
    assert outline.facts["title"] == "Doc"


# --------------------------------------------------------------------- office


def _docx(path, paragraphs):
    """A minimal .docx: the one part `sources` reads, and nothing else."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
        for text, style in paragraphs
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')


def test_docx_heading_styles_become_the_outline(data):
    _docx(data / "d.docx", [
        ("Chapter One", "Heading1"),
        ("some body text", "Normal"),
        ("A subsection", "Heading2"),
        ("more body", "Normal"),
    ])
    outline = sources.outline(data, "d.docx")
    assert [(n.title, n.depth) for n in outline.nodes] == [
        ("Chapter One", 0), ("A subsection", 1),
    ]


def test_a_docx_with_no_heading_styles_says_it_is_unstructured(data):
    _docx(data / "d.docx", [("just text", "Normal"), ("more", "Normal")])
    outline = sources.outline(data, "d.docx")
    assert outline.nodes == []
    assert "unstructured" in outline.note


# --------------------------------------------------------------------- search


def test_search_returns_anchors_and_one_line_each(data):
    (data / "doc.md").write_text("alpha\nbeta gamma\ndelta\nbeta again\n")
    hits = sources.search(data, "doc.md", "beta")
    assert [h.anchor.line for h in hits] == [2, 4]
    assert hits[0].excerpt == "beta gamma"


def test_search_never_returns_more_than_its_limit(data):
    """A pattern matching everything must not become a way to read the file."""
    (data / "doc.md").write_text("x\n" * 5000)
    hits = sources.search(data, "doc.md", "x")
    assert len(hits) == sources.SEARCH_LIMIT


def test_a_bad_pattern_is_reported_rather_than_raised_as_a_crash(data):
    (data / "doc.md").write_text("hello")
    with pytest.raises(ValueError, match="bad pattern"):
        sources.search(data, "doc.md", "(unclosed")


# ----------------------------------------------------------------------- peek


def test_peek_is_bounded_however_big_the_source(data):
    (data / "big.txt").write_text("z" * 500_000)
    text = sources.peek(data, "big.txt", sources.Anchor(source="big.txt"))
    assert len(text) < sources.PEEK_LIMIT + 200
    assert "truncated" in text


def test_peek_at_a_line_returns_that_line(data):
    (data / "doc.md").write_text("one\ntwo\nthree\n")
    anchor = sources.Anchor(source="doc.md", line=2, end_line=2)
    assert sources.peek(data, "doc.md", anchor).strip() == "two"


# ---------------------------------------------------------------- the listing


def test_listing_sources_skips_the_index_and_dotfiles(data):
    (data / "real.md").write_text("x")
    (data / ".DS_Store").write_bytes(b"junk")
    (data / sources.INDEX_DIR).mkdir()
    assert sources.sources_in(data) == ["real.md"]


def test_outlining_something_that_is_not_there_says_so(data):
    assert sources.outline(data, "gone.pdf").kind == "missing"


def test_an_unreadable_file_is_reported_not_raised(data):
    """A corrupt upload must not take the whole survey down with it."""
    (data / "broken.docx").write_bytes(b"this is not a zip")
    outline = sources.outline(data, "broken.docx")
    assert outline.nodes == []
    assert "could not be read" in outline.note


# --------------------------------------------------------------------- cache


def test_the_decoding_is_cached_and_reused(data):
    (data / "doc.md").write_text("hello world")
    sources.decode(data, "doc.md")
    cache = sources.index_dir(data) / "doc.md.txt"
    assert cache.read_text() == "hello world"

    # The cache is authoritative while it is newer than the source, which is
    # what makes the second question about a document cost a file read.
    cache.write_text("served from the cache")
    assert sources.decode(data, "doc.md").text == "served from the cache"


def test_a_changed_source_invalidates_its_cache(data):
    import os
    import time

    target = data / "doc.md"
    target.write_text("first")
    sources.decode(data, "doc.md")
    time.sleep(0.01)
    target.write_text("second")
    os.utime(target, None)
    assert sources.decode(data, "doc.md").text == "second"
