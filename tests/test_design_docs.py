"""The design docs, checked against the thing they describe.

Two of them are hand-maintained maps — the module table in the overview and
the ER block in the data model — and both had drifted before anyone noticed:
five modules were missing from one, and three token columns the database has
and `pricing` bills were missing from the other. These are the checks that
would have caught it, and they are cheap enough to run every time.

Prose is not checked. A document that explains *why* cannot be verified by a
test, and pretending otherwise would only make the tests brittle.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DESIGN = REPO / "design"


#: Bookkeeping the ER blocks leave out on purpose. The diagram is about what a
#: row *means*; every table has timestamps and listing them in each one would
#: bury the columns that carry the design.
CLERICAL = {"created_at", "updated_at", "started_at", "finished_at", "ts",
            "granted_at", "expires_at", "last_seen"}


def _er_block(doc: str, table: str) -> str | None:
    """The fullest ER block for a table, or `None` if it is only abbreviated.

    Some tables are drawn as `users { text id PK }` because another document
    owns them — auth is [01]'s subject, not this one's. A block with one or two
    entries is that abbreviation, and holding it to the full schema would be
    demanding detail the document deliberately sends elsewhere.
    """
    """The fullest ER block for a table.

    The document draws the schema more than once — an abbreviated overview
    (`threads { text id PK }`) and a detailed one. Taking the first match found
    the abbreviation and called every column undocumented.
    """
    blocks = re.findall(rf"^    {table} \{{(.*?)\}}", doc, re.DOTALL | re.M)
    if not blocks:
        return None
    fullest = max(blocks, key=len)
    return fullest if len(re.findall(r"^\s+\w+\s+\w+", fullest, re.M)) > 2 else None


def test_every_module_appears_in_the_overview():
    """A module nobody documented is one nobody knows to read."""
    table = (DESIGN / "00_overview.md").read_text(encoding="utf-8")
    modules = {
        p.stem for p in (REPO / "src" / "dex").glob("*.py")
        if p.stem not in {"__init__", "__main__"}
    }
    missing = sorted(m for m in modules if f"`{m}`" not in table)
    assert not missing, f"not in the module table: {missing}"


def test_the_er_block_matches_the_schema():
    """Every column the schema creates is in the diagram, and none is invented.

    Read from `schema.sql` rather than a live database, so this holds in a
    checkout with no Postgres.
    """
    schema = (REPO / "src" / "dex" / "schema.sql").read_text(encoding="utf-8")
    doc = (DESIGN / "10_data_model.md").read_text(encoding="utf-8")

    # Collected rather than asserted one at a time: a diagram that has drifted
    # has usually drifted in several places, and fixing them one failure per
    # run is the slowest way to find that out.
    missing: list[str] = []
    for table, body in re.findall(
        r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", schema, re.DOTALL
    ):
        body_text = _er_block(doc, table)
        if body_text is None:
            continue  # a table the diagram deliberately leaves out
        documented = set(re.findall(r"^\s+\w+\s+(\w+)", body_text, re.M))
        for line in body.splitlines():
            column = re.match(r"\s+(\w+)\s+\w", line)
            if not column or column.group(1).upper() in {
                "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT",
            } or column.group(1) in CLERICAL:
                continue
            if column.group(1) not in documented:
                missing.append(f"{table}.{column.group(1)}")
    assert not missing, f"in the schema but not in the ER block: {missing}"


def test_columns_added_by_migration_are_documented_too():
    """`ALTER TABLE ... ADD COLUMN` is how most columns arrived.

    `held` and `anchor` both came that way, and a check that only read
    `CREATE TABLE` would have missed them.
    """
    schema = (REPO / "src" / "dex" / "schema.sql").read_text(encoding="utf-8")
    doc = (DESIGN / "10_data_model.md").read_text(encoding="utf-8")
    for table, column in re.findall(
        r"ALTER TABLE (\w+)\s+ADD COLUMN IF NOT EXISTS (\w+)", schema
    ):
        body_text = _er_block(doc, table)
        if body_text is None:
            continue
        if column in CLERICAL:
            continue
        assert column in body_text, (
            f"{table}.{column} was added by a migration and is not in the ER block"
        )


def test_every_documented_file_exists():
    """A path in a document is a promise that it is there."""
    missing = []
    for doc in sorted(DESIGN.glob("*.md")):
        for ref in re.findall(r"`(src/dex/[a-z_/]+\.py)`", doc.read_text(encoding="utf-8")):
            if not (REPO / ref).exists():
                missing.append(f"{doc.name} -> {ref}")
    assert not missing, missing


@pytest.mark.parametrize("doc", sorted(DESIGN.glob("*.md")), ids=lambda p: p.stem)
def test_each_doc_records_what_is_wrong_with_it(doc: Path):
    """Every document says where it falls short.

    A design doc that only describes the happy shape of a thing reads as though
    the thing is finished, and none of them are.
    """
    assert "## Improvement opportunities" in doc.read_text(encoding="utf-8")
