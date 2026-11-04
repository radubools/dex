"""One page of a data file: CSV, TSV, JSON Lines, a JSON list of objects or a spreadsheet.

The viewer shows these as a table, and they can be far larger than a browser
should be sent, so the page is cut here. A text file is scanned once, keeping
where every `STEP`th row starts, and each page then seeks to it. A spreadsheet
has no way to seek, so its sheets are read whole and the last few kept.
"""

from __future__ import annotations

import csv
import itertools
import json
import sys
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO

DELIMITED = {".csv": ",", ".tsv": "\t"}
LINES = {".jsonl", ".ndjson"}
SHEETS = {".xls", ".xlsx", ".xlsm", ".xlsb", ".ods"}
#: A `.json` is parsed whole to see what it is, so one this large stays a file.
MAX_JSON = 256_000_000

MAX_LIMIT = 500
#: Rows between the offsets a scan keeps: a page reads at most this many extra.
STEP = 100
#: What one cell may send: a transcript in a column is not worth 500 copies of itself.
MAX_CELL = 5000
#: Files whose scans are remembered.
SCANS = 32

csv.field_size_limit(sys.maxsize)


class Unreadable(Exception):
    """Not a file this can read: the viewer offers the download instead."""


def readable(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix == ".json" or suffix in DELIMITED or suffix in LINES or suffix in SHEETS


class _Text:
    """A file's lines, decoded, saying how far it has read: csv hides that."""

    def __init__(self, file: BinaryIO, at: int = 0) -> None:
        self.file, self.at = file, at
        file.seek(at)

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        line = self.file.readline()
        if not line:
            raise StopIteration
        if self.at == 0:
            line = line.removeprefix(b"\xef\xbb\xbf")
        self.at = self.file.tell()
        return line.decode("utf-8", errors="replace")


def _records(path: Path, text: _Text) -> Iterator[Any]:
    """Rows from where `text` is: lists for CSV, parsed values for JSON Lines."""
    if path.suffix.lower() in LINES:
        for line in text:
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    yield line.strip()
    else:
        for row in csv.reader(text, delimiter=DELIMITED[path.suffix.lower()]):
            if row:
                yield row


@dataclass
class _Scan:
    stamp: tuple[int, int]
    header: list[str]
    starts: list[int]
    total: int


_scans: OrderedDict[Path, _Scan] = OrderedDict()


def _scan(path: Path) -> _Scan:
    info = path.stat()
    stamp = (info.st_mtime_ns, info.st_size)
    known = _scans.get(path)
    if known is not None and known.stamp == stamp:
        _scans.move_to_end(path)
        return known
    header: list[str] = []
    starts: list[int] = []
    total = 0
    with path.open("rb") as file:
        text = _Text(file)
        rows = _records(path, text)
        if path.suffix.lower() in DELIMITED:
            header = next(rows, [])
        start = text.at
        for _ in rows:
            if total % STEP == 0:
                starts.append(start)
            total += 1
            start = text.at
    scan = _scans[path] = _Scan(stamp, header, starts, total)
    _scans.move_to_end(path)
    while len(_scans) > SCANS:
        _scans.popitem(last=False)
    return scan


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # a spreadsheet's numbers are all floats
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, dict | list):
        value = json.dumps(value, ensure_ascii=False)
    return str(value)


def _flat(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested objects as `a.b` columns. A list stays one cell: it isn't more rows."""
    flat: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict) and value:
            flat.update(_flat(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _objects(rows: list[dict[str, Any]], total: int) -> dict[str, Any]:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    return {"columns": columns, "rows": [[_cell(row.get(c)) for c in columns] for row in rows], "total": total}


@lru_cache(maxsize=4)
def _json_rows(path: Path, stamp: tuple[int, int]) -> tuple[list[str], list[dict[str, Any]]]:
    if stamp[1] > MAX_JSON:
        raise Unreadable("too large to read as a table")
    data = json.loads(path.read_bytes())
    if not isinstance(data, list) or not data or not all(isinstance(row, dict) for row in data):
        raise Unreadable("not a list of objects")
    rows = [_flat(row) for row in data]
    return list(dict.fromkeys(key for row in rows for key in row)), rows


def _json(path: Path, offset: int, limit: int) -> dict[str, Any]:
    """The columns are the whole file's: it is all here, so they can stay put between pages."""
    info = path.stat()
    columns, rows = _json_rows(path, (info.st_mtime_ns, info.st_size))
    page = rows[offset:offset + limit]
    return {"columns": columns, "rows": [[_cell(row.get(c)) for c in columns] for row in page], "total": len(rows)}


def _delimited_or_lines(path: Path, offset: int, limit: int) -> dict[str, Any]:
    scan = _scan(path)
    rows: list[Any] = []
    if offset < scan.total:
        with path.open("rb") as file:
            at = _Text(file, scan.starts[offset // STEP])
            rows = list(itertools.islice(_records(path, at), offset % STEP, offset % STEP + limit))
    if path.suffix.lower() in DELIMITED:
        return _table(scan.header, rows, scan.total)
    # Each line names its own fields: the columns are those this page has.
    return _objects([_flat(row) if isinstance(row, dict) else {"value": row} for row in rows], scan.total)


def _table(header: list[Any], rows: Iterable[list[Any]], total: int) -> dict[str, Any]:
    """Rows may be longer or shorter than the header: every one comes out as wide."""
    rows = list(rows)
    width = max([len(header), *(len(row) for row in rows)])
    columns = [_cell(c) for c in header] + [""] * (width - len(header))
    return {
        "columns": columns,
        "rows": [[_cell(c) for c in row] + [""] * (width - len(row)) for row in rows],
        "total": total,
    }


@lru_cache(maxsize=4)
def _sheet(path: Path, stamp: tuple[int, int], name: str) -> list[list[Any]]:
    return _book(path).get_sheet_by_name(name).to_python()


def _book(path: Path) -> Any:
    from python_calamine import CalamineWorkbook  # noqa: PLC0415 -- a task dependency, and rarely wanted

    return CalamineWorkbook.from_path(str(path))


def _spreadsheet(path: Path, sheet: str | None, offset: int, limit: int) -> dict[str, Any]:
    names = list(_book(path).sheet_names)
    if not names:
        return {"columns": [], "rows": [], "total": 0, "sheets": [], "sheet": None}
    name = sheet if sheet in names else names[0]
    info = path.stat()
    rows = _sheet(path, (info.st_mtime_ns, info.st_size), name)
    return {
        **_table(rows[0] if rows else [], rows[offset + 1:offset + 1 + limit], max(0, len(rows) - 1)),
        "sheets": names,
        "sheet": name,
    }


def read_page(
    path: Path, *, sheet: str | None = None, offset: int = 0, limit: int = 100, full: bool = False,
) -> dict[str, Any]:
    """`limit` rows from `offset`, counted below the header: columns, rows, total.

    Cells stop at `MAX_CELL` unless `full` asks for them whole.
    """
    offset, limit = max(0, offset), min(max(1, limit), MAX_LIMIT)
    try:
        if path.suffix.lower() in SHEETS:
            page = _spreadsheet(path, sheet, offset, limit)
        elif path.suffix.lower() == ".json":
            page = _json(path, offset, limit)
        else:
            page = _delimited_or_lines(path, offset, limit)
    except Unreadable:
        raise
    except Exception as exc:  # a malformed file is the reader's to hear about, not a 500
        raise Unreadable(f"{type(exc).__name__}: {exc}") from exc
    if not full:
        page["rows"] = [[c if len(c) <= MAX_CELL else c[:MAX_CELL] + "…" for c in row] for row in page["rows"]]
    return {**page, "offset": offset, "limit": limit}
