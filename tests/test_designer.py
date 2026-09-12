"""Parsing the design chat's reply."""

from dex.designer import parse

CURRENT = "# Project\n\nOriginal body.\n"


def test_extracts_both_blocks():
    reply = parse(
        "<summary>Added a testing convention.</summary>"
        "<guide># Project\n\nNew body.\n</guide>",
        CURRENT,
    )
    assert reply.summary == "Added a testing convention."
    assert reply.guide == "# Project\n\nNew body."
    assert reply.changed is True


def test_a_fenced_guide_is_unwrapped():
    reply = parse(
        "<summary>ok</summary><guide>```markdown\n# Project\n\nFenced.\n```</guide>", CURRENT
    )
    assert reply.guide == "# Project\n\nFenced."
    assert "```" not in reply.guide


def test_an_unchanged_guide_is_not_a_change():
    reply = parse(f"<summary>Just answering.</summary><guide>{CURRENT}</guide>", CURRENT)
    assert reply.changed is False
    assert reply.guide == CURRENT


def test_a_reply_with_no_guide_keeps_the_file():
    reply = parse("Which language should tasks use?", CURRENT)
    assert reply.changed is False
    assert reply.guide == CURRENT
    assert "Which language" in reply.summary


def test_an_empty_guide_is_refused():
    reply = parse("<summary>oops</summary><guide>   </guide>", CURRENT)
    assert reply.changed is False
    assert reply.guide == CURRENT


def test_prose_around_the_blocks_is_ignored():
    reply = parse(
        "Sure thing!\n<summary>Tightened the conventions.</summary>\n"
        "<guide># Project\n\nTighter.\n</guide>\nLet me know.",
        CURRENT,
    )
    assert reply.summary == "Tightened the conventions."
    assert reply.guide == "# Project\n\nTighter."
