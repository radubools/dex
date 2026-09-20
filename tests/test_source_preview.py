"""Serving an uploaded source back to the preview pane.

The upload side is covered in `test_uploads.py`; this is the read side — the
routes the viewer calls to list a project's data directory and to fetch one
file for an `<iframe>`.
"""

from __future__ import annotations

import pytest

from tests.test_api import client, clean  # noqa: F401  (fixtures)


def _upload(c, name: str, body: bytes, project: str = "algorithms"):
    return c.post(
        f"/api/uploads?project={project}",
        files={"files": (name, body, "application/octet-stream")},
    )


def test_lists_what_was_uploaded(client):  # noqa: F811
    _upload(client, "brief.pdf", b"%PDF-1.4 stub")
    body = client.get("/api/datasets?project=algorithms").json()
    assert body["project"] == "algorithms"
    names = [f["name"] for f in body["files"]]
    assert "brief.pdf" in names
    assert next(f for f in body["files"] if f["name"] == "brief.pdf")["bytes"] == 13


def test_lists_nothing_for_a_project_with_no_data(client):  # noqa: F811
    assert client.get("/api/datasets?project=algorithms").json()["files"] == []


def test_hides_dotfiles(client):  # noqa: F811
    """`.DS_Store` is in every directory a Mac has touched and is not a source."""
    root = client.config.project_datasets("algorithms")  # type: ignore[attr-defined]
    root.mkdir(parents=True, exist_ok=True)
    (root / ".DS_Store").write_bytes(b"junk")
    assert client.get("/api/datasets?project=algorithms").json()["files"] == []


def test_serves_the_bytes_back(client):  # noqa: F811
    _upload(client, "brief.pdf", b"%PDF-1.4 stub")
    r = client.get("/api/datasets/raw?project=algorithms&name=brief.pdf")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 stub"


def test_a_pdf_is_typed_and_inline(client):  # noqa: F811
    """Both halves of what makes a frame render rather than download."""
    _upload(client, "brief.pdf", b"%PDF-1.4 stub")
    r = client.get("/api/datasets/raw?project=algorithms&name=brief.pdf")
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.headers["content-disposition"].startswith("inline")


def test_keeps_a_non_ascii_name(client):  # noqa: F811
    """The name that prompted this: a CJK filename must survive the round trip."""
    _upload(client, "默读.pdf", b"%PDF-1.4 stub")
    listed = client.get("/api/datasets?project=algorithms").json()["files"]
    assert [f["name"] for f in listed] == ["默读.pdf"]
    r = client.get("/api/datasets/raw?project=algorithms&name=默读.pdf")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 stub"


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd", "..%2f..%2fsecret", "/etc/passwd", "sub/../../escape"],
)
def test_refuses_a_name_that_climbs_out(client, name):  # noqa: F811
    """`resolve` re-checks every name; nothing outside the project answers."""
    assert client.get(
        "/api/datasets/raw", params={"project": "algorithms", "name": name}
    ).status_code == 404


def test_refuses_a_directory(client):  # noqa: F811
    root = client.config.project_datasets("algorithms")  # type: ignore[attr-defined]
    (root / "nested").mkdir(parents=True, exist_ok=True)
    assert client.get(
        "/api/datasets/raw?project=algorithms&name=nested"
    ).status_code == 404


def test_one_project_cannot_read_another(client):  # noqa: F811
    """The name is resolved under the *named* project, not searched for.

    Both projects are created first: `resolve_project` falls back to the
    default for a slug with no project row, so without them both halves of
    this would read the same directory and the test would pass for the wrong
    reason.
    """
    for name in ("Alpha", "Beta"):
        assert client.post("/api/projects", json={"name": name}).status_code == 200
    _upload(client, "private.txt", b"secret", project="beta")

    assert client.get(
        "/api/datasets/raw?project=alpha&name=private.txt"
    ).status_code == 404
    assert client.get(
        "/api/datasets/raw?project=beta&name=private.txt"
    ).content == b"secret"
    assert [f["name"] for f in
            client.get("/api/datasets?project=alpha").json()["files"]] == []
