"""The pre-planning pass: work out what is *in* the sources before splitting it.

The planner is one tool-less turn. Given "translate this" and a 920-page PDF it
can only guess, because all it ever saw was a filename — so it produces one
enormous task, or three arbitrary ones, and the operator has no way to tell
which. What it needs first is an account of what the document actually contains
and where each part of it sits.

That is this. A survey is a short agent run with exactly five tools, all of
them reading *structure*: list the sources, outline one, search it, peek at a
bounded excerpt, and outline a URL. There is no Read, no Bash and no Grep, so the document
cannot be pulled into the context whole — not because the prompt asks it not
to, but because nothing in the tool surface can. The 920-page book above costs
about two thousand tokens to survey.

What comes back is a list of segments, each with an anchor into the original.
The planner turns segments into tasks, and the anchor rides along: the operator
sees "pp. 12–48 · Chapter 3" on the plan card and can open the page, and the
worker that eventually runs the task is told exactly which pages are its own.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import sources as src

log = logging.getLogger("dex.surveyor")

#: The only tools a survey run gets. Named in full because `allowed_tools`
#: matches the wire names, and an in-process MCP tool is `mcp__<server>__<name>`.
SERVER = "survey"
TOOL_NAMES = [
    f"mcp__{SERVER}__list_sources",
    f"mcp__{SERVER}__outline",
    f"mcp__{SERVER}__search",
    f"mcp__{SERVER}__peek",
    f"mcp__{SERVER}__outline_url",
]

#: A survey that has taken this many turns is not converging; the segments it
#: has are better than a run that never stops.
MAX_TURNS = 24


@dataclass
class Segment:
    """One separable piece of the source material."""

    title: str
    summary: str = ""
    anchor: src.Anchor | None = None
    #: Roughly how much material this covers, in the source's own terms.
    extent: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "extent": self.extent,
            "anchor": self.anchor.to_json() if self.anchor else None,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> "Segment":
        anchor = raw.get("anchor")
        return Segment(
            title=str(raw.get("title", "")).strip(),
            summary=str(raw.get("summary", "")).strip(),
            extent=str(raw.get("extent", "")).strip(),
            anchor=src.Anchor.from_json(anchor) if isinstance(anchor, dict) else None,
        )


@dataclass
class Survey:
    """What the sources contain, and how they divide."""

    segments: list[Segment] = field(default_factory=list)
    #: What the survey understood the material to be, in a sentence or two.
    overview: str = ""
    #: Anything the survey could not reach: a scan with no text, a blocked URL.
    gaps: str = ""
    #: True when the sources turned out not to need splitting at all.
    single: bool = False
    #: What the operator settled when the survey asked them something. The
    #: planner is a separate, stateless call and never sees the exchange, so
    #: an answer that does not come back through here is an answer nobody
    #: acted on.
    clarified: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "segments": [s.to_json() for s in self.segments],
            "overview": self.overview,
            "gaps": self.gaps,
            "single": self.single,
            "clarified": self.clarified,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> "Survey":
        return Survey(
            segments=[
                Segment.from_json(s)
                for s in raw.get("segments") or []
                if isinstance(s, dict)
            ],
            overview=str(raw.get("overview", "")).strip(),
            gaps=str(raw.get("gaps", "")).strip(),
            single=bool(raw.get("single")),
            clarified=str(raw.get("clarified", "")).strip(),
        )

    def render(self) -> str:
        """The survey as text, for the planner's prompt."""
        lines: list[str] = []
        if self.overview:
            lines.append(self.overview)
            lines.append("")
        if self.clarified:
            # First, because it may change what the rest of it means.
            lines.append(f"The operator was asked, and settled: {self.clarified}")
            lines.append("")
        if self.single:
            lines.append(
                "This material does not divide — it is one piece of work:"
            )
        elif len(self.segments) == 1:
            lines.append("The sources came to one part:")
        else:
            lines.append(f"The sources divide into {len(self.segments)} parts:")
        for i, seg in enumerate(self.segments, start=1):
            where = seg.anchor.describe() if seg.anchor else "no anchor"
            extent = f", {seg.extent}" if seg.extent else ""
            lines.append(f"{i}. **{seg.title}** — [{where}{extent}]")
            if seg.summary:
                lines.append(f"   {seg.summary}")
        if self.gaps:
            lines.append("")
            lines.append(f"Not covered: {self.gaps}")
        return "\n".join(lines)


# --------------------------------------------------------------------- tools


def survey_server(project_dir: Path, attached: list[str] | None = None) -> Any:
    """The five reading tools, bound to one request's material.

    `project_dir` contains every file ever uploaded to the project, which is
    *not* what this request is about. Binding the tools to `attached` — the
    files this message actually brought — is what keeps them apart.

    Without it `list_sources` answered with the whole directory while the brief
    said "Files they attached: (none)", and a survey of a Wikipedia link opened
    with two unrelated novels it had to reason about before it could start. Two
    accounts of the same thing, one of them wrong, and the agent had no way to
    tell which. `None` means no restriction, for a caller that wants the lot.

    Every path is still resolved inside `project_dir` by `sources`, so a source
    name that climbs out reaches nothing — the same containment the preview
    pane uses, for the same reason.
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    allowed = None if attached is None else set(attached)

    def _text(body: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": body}]}

    def _refuse(name: str) -> dict[str, Any] | None:
        """`None` when the name is this request's to read."""
        if allowed is None or name in allowed:
            return None
        listed = ", ".join(sorted(allowed)) or "none"
        return _text(
            f"{name!r} was not attached to this request and is not yours to "
            f"read. Attached: {listed}."
        )

    @tool(
        "list_sources",
        "List the files attached to this project, with their type and size. "
        "Call this first: it is the only way to learn what names the other "
        "tools will accept.",
        {},
    )
    async def list_sources(_args: dict[str, Any]) -> dict[str, Any]:
        names = src.sources_in(project_dir)
        if allowed is not None:
            # Order follows what the operator attached, not the directory's
            # modification times.
            names = [n for n in attached or [] if n in set(names)]
        if not names:
            return _text(
                "No files were attached to this request. If the operator gave "
                "a link, outline_url is your only source."
            )
        rows = []
        for name in names:
            size = (project_dir / name).stat().st_size
            rows.append(f"- {name} ({size:,} bytes)")
        return _text("\n".join(rows))

    @tool(
        "outline",
        "The structure of one source: a PDF's bookmarks, a document's headings, "
        "a workbook's sheets, a table's columns. Each entry carries an anchor you "
        "can quote back in a segment. This is cheap — always outline before "
        "searching.",
        {"source": str},
    )
    async def outline(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("source", ""))
        return _refuse(name) or _text(src.outline(project_dir, name).render())

    @tool(
        "search",
        "Find a regular expression in a source's decoded text. Returns the "
        "matching line and its anchor — a page or line number — never the "
        "surrounding text. Use it to locate a table of contents, a chapter "
        "break, a recurring heading, or a term you need the position of.",
        {"source": str, "pattern": str},
    )
    async def search(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("source", ""))
        pattern = str(args.get("pattern", ""))
        if (no := _refuse(name)) is not None:
            return no
        try:
            hits = src.search(project_dir, name, pattern)
        except ValueError as exc:
            return _text(str(exc))
        if not hits:
            return _text(f"No match for /{pattern}/ in {name}.")
        rows = [f"- [{h.anchor.describe()}] {h.excerpt}" for h in hits]
        if len(hits) >= src.SEARCH_LIMIT:
            rows.append(f"… stopped at {src.SEARCH_LIMIT} matches; narrow the pattern.")
        return _text("\n".join(rows))

    @tool(
        "peek",
        "Read a bounded excerpt at one anchor — at most a few thousand "
        "characters. For confirming what a section is, not for reading it. "
        "Give a page (and optionally endPage) for a PDF, or a line for a text "
        "document.",
        # A full JSON Schema rather than the `{name: type}` shorthand, which
        # marks every key required: as declared that way, peeking a markdown
        # file meant inventing a page number and peeking a PDF meant inventing
        # a line. Only the source is genuinely required — the rest say *where*,
        # and which of them applies depends on what kind of source it is.
        {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Exact name from list_sources."},
                "page": {"type": "integer", "description": "First page, for a PDF."},
                "endPage": {"type": "integer", "description": "Last page, for a range."},
                "line": {"type": "integer", "description": "Line, for a text document."},
            },
            "required": ["source"],
        },
    )
    async def peek(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("source", ""))
        if (no := _refuse(name)) is not None:
            return no
        anchor = src.Anchor(
            source=name,
            page=src._int_or_none(args.get("page")),
            end_page=src._int_or_none(args.get("endPage")),
            line=src._int_or_none(args.get("line")),
        )
        return _text(src.peek(project_dir, name, anchor) or "(nothing there)")

    @tool(
        "outline_url",
        "The structure of a web page the operator named: its heading tree, and "
        "the site's own sitemap when it publishes one. Only fetches public "
        "addresses.",
        {"url": str},
    )
    async def outline_url(args: dict[str, Any]) -> dict[str, Any]:
        from . import web_sources

        url = str(args.get("url", ""))
        try:
            result = await web_sources.outline_url(url)
        except web_sources.Blocked as exc:
            return _text(f"Refused: {exc}")
        except Exception as exc:
            return _text(f"Could not fetch {url}: {type(exc).__name__}: {exc}")
        return _text(result.render())

    return create_sdk_mcp_server(
        name=SERVER,
        version="0.1.0",
        tools=[list_sources, outline, search, peek, outline_url],
    )


# -------------------------------------------------------------------- parsing


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_survey(raw: str) -> Survey:
    """The survey's JSON verdict, however it was wrapped.

    Same tolerance as the planner's parser, and for the same reason: a model
    that explains itself before answering is not a failure, and a survey thrown
    away for having a sentence in front of its JSON costs a whole run.
    """
    if not raw.strip():
        return Survey(gaps="the survey returned nothing")

    for candidate in _candidates(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and (
            "segments" in parsed or "overview" in parsed or "single" in parsed
        ):
            return Survey.from_json(parsed)
    return Survey(overview=raw.strip()[:2000], gaps="the survey did not answer in JSON")


def _candidates(raw: str) -> list[str]:
    """Every plausible JSON object in the reply, most likely first."""
    found = [m.group(1) for m in _FENCE.finditer(raw)]
    depth = 0
    start = -1
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                found.append(raw[start: i + 1])
    # Longest first: an outer object holding the answer beats a nested fragment.
    return sorted(dict.fromkeys(found), key=len, reverse=True)


# ---------------------------------------------------------------- the trigger


#: A bare URL in a chat message. Deliberately narrow — `http(s)://` and
#: nothing else — so an ordinary sentence mentioning a domain does not start a
#: crawl the operator did not ask for.
URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+")


def urls_in(text: str) -> list[str]:
    """Every URL the operator typed, in order, without duplicates."""
    return list(dict.fromkeys(m.group(0).rstrip(".,;:") for m in URL_RE.finditer(text)))


def needs_survey(text: str, attachments: list[str]) -> bool:
    """Whether this message should be surveyed before it is planned.

    Hardcoded on purpose, and not a question any project's guide gets to
    answer. A message that brings sources with it is a different kind of
    request from one that does not: the material exists, it has a shape, and
    planning without looking at that shape is guessing. Every project gets the
    same behaviour so that the plan an operator sees means the same thing
    whichever project they are in.
    """
    return bool(attachments) or bool(urls_in(text))
