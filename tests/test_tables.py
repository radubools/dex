"""Pages of data files, and pages of a package's HTML."""

from __future__ import annotations

import json

import openpyxl
import pytest

from dex import tables
from tests.test_api import clean, client  # noqa: F401


def test_a_csv_is_paged_below_its_header(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("id,name\n" + "".join(f'{i},"n\n{i}"\n' for i in range(1, 351)))
    page = tables.read_page(path, offset=240, limit=100)
    assert page["columns"] == ["id", "name"] and page["total"] == 350
    assert len(page["rows"]) == 100 and page["rows"][0] == ["241", "n\n241"]
    assert tables.read_page(path, offset=300, limit=100)["rows"][-1][0] == "350"
    assert tables.read_page(path, offset=900)["rows"] == []


def test_a_long_cell_is_cut_unless_asked_for_whole(tmp_path):
    path = tmp_path / "a.csv"
    long = "x" * (tables.MAX_CELL + 10)
    path.write_text(f"a\n{long}\n")
    assert tables.read_page(path)["rows"][0][0] == "x" * tables.MAX_CELL + "…"
    assert tables.read_page(path, full=True)["rows"][0][0] == long


def test_a_changed_file_is_scanned_again(tmp_path):
    path = tmp_path / "a.tsv"
    path.write_text("a\tb\n1\t2\n")
    assert tables.read_page(path)["total"] == 1
    path.write_text("a\tb\n1\t2\n3\n")
    page = tables.read_page(path)
    assert page["total"] == 2 and page["rows"][1] == ["3", ""]


def test_a_byte_order_mark_is_not_part_of_the_first_column(tmp_path):
    path = tmp_path / "a.csv"
    path.write_bytes(b"\xef\xbb\xbfid,x\n1,2\n")
    assert tables.read_page(path)["columns"] == ["id", "x"]


def test_json_lines_take_their_columns_from_the_page(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text('{"a": 1}\n\n{"b": {"c": [2]}, "a": null}\nnot json\n7\n')
    page = tables.read_page(path)
    assert page["total"] == 4 and page["columns"] == ["a", "b.c", "value"]
    assert page["rows"] == [["1", "", ""], ["", "[2]", ""], ["", "", "not json"], ["", "", "7"]]


def test_a_json_list_of_objects_is_flattened(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps([{"a": 1, "b": {"c": {"d": 2}}, "e": [{"f": 1}]}, {"g": True}] * 150))
    page = tables.read_page(path, offset=299, limit=100)
    assert page["columns"] == ["a", "b.c.d", "e", "g"] and page["total"] == 300
    assert page["rows"] == [["", "", "", "true"]]


@pytest.mark.parametrize("content", ['{"a": 1}', "[1, 2]", '[{"a": 1}, 2]', "[]", "not json"])
def test_any_other_json_is_not_a_table(tmp_path, content):
    path = tmp_path / "a.json"
    path.write_text(content)
    with pytest.raises(tables.Unreadable):
        tables.read_page(path)


def test_a_workbook_is_paged_a_sheet_at_a_time(tmp_path):
    pytest.importorskip("python_calamine")
    book = openpyxl.Workbook()
    book.active.title = "One"
    book.active.append(["id", "ok"])
    for i in range(1, 251):
        book.active.append([i, True])
    book.create_sheet("Two").append(["x"])
    path = tmp_path / "a.xlsx"
    book.save(path)

    page = tables.read_page(path, offset=200, limit=100)
    assert page["sheets"] == ["One", "Two"] and page["sheet"] == "One" and page["total"] == 250
    assert page["columns"] == ["id", "ok"] and page["rows"][0] == ["201", "true"] and len(page["rows"]) == 50
    two = tables.read_page(path, sheet="Two")
    assert two["sheet"] == "Two" and two["total"] == 0 and two["rows"] == []


def test_a_file_that_is_not_what_it_says_is_unreadable(tmp_path):
    pytest.importorskip("python_calamine")
    path = tmp_path / "a.xlsx"
    path.write_text("nope")
    with pytest.raises(tables.Unreadable):
        tables.read_page(path)


def package(client, config):  # noqa: F811
    home = config.assets_dir / "alpha" / "report"
    (home / "pics").mkdir(parents=True)
    (config.assets_dir / "alpha" / "other").mkdir()
    (config.assets_dir / "alpha" / "other" / "secret.txt").write_text("no")
    (home / "pics" / "a.txt").write_text("shot")
    (home / "pics" / "index.html").write_text("<p>hi</p>")
    (home / "rows.jsonl").write_text(json.dumps({"a": 1}) + "\n")
    return home


def test_the_table_route_pages_and_refuses_what_it_cannot_read(client, config):  # noqa: F811
    package(client, config)
    got = client.get("/api/assets/table", params={"path": "alpha/report/rows.jsonl"})
    assert got.status_code == 200 and got.json()["rows"] == [["1"]]
    assert client.get("/api/assets/table", params={"path": "alpha/report/pics/a.txt"}).status_code == 415


def test_a_page_reaches_its_package_and_nothing_else(client, config):  # noqa: F811
    package(client, config)
    url = client.get("/api/assets/page", params={"path": "alpha/report/pics/index.html"}).json()["url"]
    page = client.get(url)
    assert page.status_code == 200 and "sandbox allow-scripts" in page.headers["content-security-policy"]
    # What it loads, for the opaque origin its sandbox gives it.
    beside = client.get(url.replace("pics/index.html", "pics/a.txt"))
    assert beside.text == "shot" and beside.headers["access-control-allow-origin"] == "*"
    assert client.get(url.replace("alpha/report/pics/index.html", "alpha/other/secret.txt")).status_code == 403
    assert client.get(url.replace("pics/index.html", "../other/secret.txt")).status_code in (403, 404)
    token = url.split("/")[4]
    assert client.get(url.replace(token, "1-00")).status_code == 403
