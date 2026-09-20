"""The pre-planning pass: when it runs, and what comes back from it."""

from __future__ import annotations

import json

import pytest

from dex import sources, surveyor


# ------------------------------------------------------------------- routing


def test_a_message_with_an_attachment_is_surveyed():
    assert surveyor.needs_survey("translate this", ["book.pdf"]) is True


def test_a_message_with_a_url_is_surveyed():
    assert surveyor.needs_survey("index https://example.com/docs", []) is True


def test_a_plain_message_is_planned_directly():
    assert surveyor.needs_survey("write me a two-sum package", []) is False


def test_naming_a_domain_in_passing_does_not_start_a_survey():
    """Only a real `http(s)://` URL counts, or half of ordinary English does."""
    assert surveyor.needs_survey("compare it with numpy.org's approach", []) is False


def test_the_decision_takes_no_project_or_guide():
    """It is hardcoded on purpose: the signature has nowhere to put a guide.

    This is the requirement, not an implementation detail — a plan has to mean
    the same thing in every project, so no project gets to opt out of being
    surveyed.
    """
    import inspect

    assert list(inspect.signature(surveyor.needs_survey).parameters) == [
        "text", "attachments",
    ]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("see https://a.example/x and https://b.example/y", 2),
        ("https://a.example/x https://a.example/x", 1),
        ("(https://a.example/x)", 1),
        ("ends a sentence https://a.example/x.", 1),
    ],
)
def test_urls_are_picked_out_cleanly(text, expected):
    found = surveyor.urls_in(text)
    assert len(found) == expected
    assert all(not u.endswith((".", ")", "]")) for u in found)


# ------------------------------------------------------------------- parsing


def _reply(payload: dict) -> str:
    return f"Here is what I found.\n\n```json\n{json.dumps(payload)}\n```"


def test_a_survey_parses_out_of_a_fenced_reply():
    survey = surveyor.parse_survey(_reply({
        "overview": "A novel in three parts.",
        "single": False,
        "segments": [
            {"title": "Part One", "summary": "Yu Lian", "extent": "pp. 1-40",
             "anchor": {"source": "book.pdf", "page": 1, "endPage": 40,
                        "label": "pp. 1-40 · Part One"}},
        ],
        "gaps": "",
    }))
    assert survey.overview == "A novel in three parts."
    assert len(survey.segments) == 1
    assert survey.segments[0].anchor.page == 1
    assert survey.segments[0].anchor.end_page == 40


def test_a_survey_parses_without_a_fence():
    survey = surveyor.parse_survey('{"segments": [], "overview": "nothing here"}')
    assert survey.overview == "nothing here"


def test_a_reply_that_is_not_json_is_kept_as_prose_not_lost():
    """A wasted run is bad; a wasted run that also loses what it said is worse."""
    survey = surveyor.parse_survey("I could not open the file, sorry.")
    assert "could not open" in survey.overview
    assert "did not answer in JSON" in survey.gaps


def test_an_empty_reply_says_so():
    assert "returned nothing" in surveyor.parse_survey("   ").gaps


def test_a_segment_without_an_anchor_still_parses():
    survey = surveyor.parse_survey('{"segments": [{"title": "Whole thing"}]}')
    assert survey.segments[0].title == "Whole thing"
    assert survey.segments[0].anchor is None


def test_junk_among_the_segments_is_dropped_not_fatal():
    survey = surveyor.parse_survey(
        '{"segments": [{"title": "Real"}, "not an object", null]}'
    )
    assert [s.title for s in survey.segments] == ["Real"]


def test_the_outer_object_wins_over_a_nested_one():
    """A reply that shows its working must not be parsed as the working."""
    raw = (
        'First I considered {"segments": [{"title": "wrong"}]} but settled on:\n'
        '```json\n{"segments": [{"title": "right"}], "overview": "final"}\n```'
    )
    assert [s.title for s in surveyor.parse_survey(raw).segments] == ["right"]


# ----------------------------------------------------------------- rendering


def test_a_rendered_survey_says_where_each_part_is():
    survey = surveyor.Survey(
        overview="A price book.",
        segments=[
            surveyor.Segment(
                title="Division 32",
                summary="Exterior improvements.",
                extent="~90,000 chars",
                anchor=sources.Anchor(source="rs.pdf", page=641, end_page=700),
            ),
        ],
    )
    rendered = survey.render()
    assert "A price book." in rendered
    assert "Division 32" in rendered
    assert "pp. 641–700" in rendered
    assert "Exterior improvements." in rendered


def test_a_survey_round_trips_through_json():
    survey = surveyor.Survey(
        overview="x",
        segments=[surveyor.Segment(
            title="t", anchor=sources.Anchor(source="a.pdf", page=2),
        )],
    )
    back = surveyor.Survey.from_json(survey.to_json())
    assert back.overview == "x"
    assert back.segments[0].anchor.page == 2


# --------------------------------------------------------------------- tools


def test_the_survey_tools_are_the_only_ones_it_gets():
    """The guarantee is structural: no Read, no Bash, no Grep in the list."""
    assert all(name.startswith("mcp__survey__") for name in surveyor.TOOL_NAMES)
    assert {n.rsplit("__", 1)[-1] for n in surveyor.TOOL_NAMES} == {
        "list_sources", "outline", "search", "peek", "outline_url",
    }


# ------------------------------------------------- the tools see one request


def _server(tmp_path, files: dict[str, bytes], attached):
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    return surveyor.survey_server(tmp_path, attached)


async def _call(server, name, **args):
    """Through the real MCP handler, the way the agent reaches it."""
    handlers = server["instance"]._request_handlers
    params = handlers["tools/call"].params_type(name=name, arguments=args)
    result = await handlers["tools/call"].handler(None, params)
    return result.content[0].text


async def test_only_this_request_s_files_are_listed(tmp_path):
    """The bug: a link-only request was shown two months-old novels.

    `datasets/<project>/` accumulates every upload ever made to the project.
    Listing all of it contradicted the brief, which said nothing was attached.
    """
    server = _server(
        tmp_path,
        {"old-novel.md": b"# Ch 1\n", "today.md": b"# Today\n"},
        attached=["today.md"],
    )
    listed = await _call(server, "list_sources")
    assert "today.md" in listed
    assert "old-novel.md" not in listed


async def test_a_link_only_request_is_told_it_has_no_files(tmp_path):
    server = _server(tmp_path, {"leftover.md": b"# Old\n"}, attached=[])
    listed = await _call(server, "list_sources")
    assert "No files were attached" in listed
    assert "leftover.md" not in listed


async def test_reading_a_file_from_another_request_is_refused(tmp_path):
    """Listing it is not enough — the name could still be guessed."""
    server = _server(
        tmp_path, {"old.md": b"# Secret\n", "today.md": b"# Today\n"},
        attached=["today.md"],
    )
    for call, extra in (("outline", {}), ("search", {"pattern": "."}), ("peek", {"line": 1})):
        answer = await _call(server, call, source="old.md", **extra)
        assert "not attached to this request" in answer, call
        assert "Secret" not in answer


async def test_the_attached_file_still_reads(tmp_path):
    server = _server(tmp_path, {"today.md": b"# Today\n\nbody\n"}, attached=["today.md"])
    assert "Today" in await _call(server, "outline", source="today.md")


async def test_no_restriction_lists_everything(tmp_path):
    """`None` is the unrestricted caller, which is not how the runner calls it."""
    server = _server(tmp_path, {"a.md": b"#a\n", "b.md": b"#b\n"}, attached=None)
    listed = await _call(server, "list_sources")
    assert "a.md" in listed and "b.md" in listed


# ------------------------------------------------ what the planner is told


def test_a_survey_that_does_not_divide_says_so():
    """"divides into 1 parts" is both wrong and the wrong signal."""
    survey = surveyor.Survey(
        overview="A six-page letter.", single=True,
        segments=[surveyor.Segment(title="The letter")],
    )
    rendered = survey.render()
    assert "does not divide" in rendered
    assert "1 parts" not in rendered


def test_a_divided_survey_states_the_count():
    survey = surveyor.Survey(
        segments=[surveyor.Segment(title=f"Part {i}") for i in range(20)]
    )
    assert "divide into 20 parts" in survey.render()


def test_every_segment_reaches_the_rendering():
    """The planner plans from this text and nothing else, so nothing may drop."""
    survey = surveyor.Survey(segments=[
        surveyor.Segment(
            title=f"Linked article {i}",
            anchor=sources.Anchor(source=f"https://x/{i}", url=f"https://x/{i}"),
        )
        for i in range(1, 21)
    ])
    rendered = survey.render()
    for i in range(1, 21):
        assert f"Linked article {i}" in rendered
        assert f"https://x/{i}" in rendered
