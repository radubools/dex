"""The admin surface: what is available, and turning it on per project."""

from __future__ import annotations

import json

import pytest

from tests.test_api import client, clean  # noqa: F401  (fixtures)


@pytest.fixture
def two_skills(client):  # noqa: F811
    """Two skills in the client's workspace."""
    from dex import skills

    root = client.config.workspace  # type: ignore[attr-defined]
    for name, modules in (
        ("figure", {"rig.py": "R = 1\n"}),
        ("packages", {"manifest.py": "M = 1\n"}),
    ):
        # Unpublished versions start under a placeholder directory; `publish`
        # renames it to the content hash.
        staged = root / "skills" / name / "new"
        (staged / "utils").mkdir(parents=True, exist_ok=True)
        for file, body in modules.items():
            (staged / "utils" / file).write_text(body)
        (staged / skills.MANIFEST_NAME).write_text(
            json.dumps({"name": name, "description": f"the {name} skill"}) + "\n"
        )
        skills.publish(skills.read(staged))
    return root


def version_of(client, name: str) -> str:
    """The current version the listing reports, for use in a URL."""
    body = client.get("/api/skills").json()
    return next(s["current"] for s in body["skills"] if s["name"] == name)


def row(client, name: str) -> dict:
    """The one row for a skill; versions are nested inside it now."""
    body = client.get("/api/skills").json()
    return next(s for s in body["skills"] if s["name"] == name)


def test_listing_reports_every_skill_with_its_version(client, two_skills):  # noqa: F811
    body = client.get("/api/skills").json()
    assert [s["name"] for s in body["skills"]] == ["figure", "packages"]
    for skill in body["skills"]:
        assert len(skill["current"]) == 6
        assert all(v["updated"] > 0 for v in skill["versions"])


def test_listing_says_which_projects_have_it_on(client, two_skills):  # noqa: F811
    client.post("/api/projects", json={"name": "Alpha"})
    client.post(f"/api/skills/figure/projects/alpha?version={version_of(client, 'figure')}")

    assert row(client, "figure")["projects"] == {"alpha": version_of(client, "figure")}
    assert row(client, "packages")["projects"] == {}


def test_enabling_materialises_the_modules(client, two_skills):  # noqa: F811
    client.post("/api/projects", json={"name": "Alpha"})
    assert client.post(f"/api/skills/figure/projects/alpha?version={version_of(client, 'figure')}").status_code == 200

    utils = client.config.project_dir("alpha") / "utils"  # type: ignore[attr-defined]
    assert (utils / "rig.py").read_text() == "R = 1\n"


def test_disabling_takes_them_back(client, two_skills):  # noqa: F811
    client.post("/api/projects", json={"name": "Alpha"})
    client.post(f"/api/skills/figure/projects/alpha?version={version_of(client, 'figure')}")
    assert client.delete(f"/api/skills/figure/projects/alpha?version={version_of(client, 'figure')}").status_code == 200

    utils = client.config.project_dir("alpha") / "utils"  # type: ignore[attr-defined]
    assert not (utils / "rig.py").exists()


def test_one_skill_can_be_on_for_several_projects(client, two_skills):  # noqa: F811
    """The reason any of this exists."""
    for name in ("Alpha", "Beta"):
        client.post("/api/projects", json={"name": name})
        client.post(f"/api/skills/figure/projects/{name.lower()}?version={version_of(client, 'figure')}")

    assert sorted(row(client, "figure")["projects"]) == ["alpha", "beta"]
    for slug in ("alpha", "beta"):
        assert (client.config.project_dir(slug) / "utils" / "rig.py").is_file()  # type: ignore[attr-defined]


def test_enabling_twice_is_not_an_error(client, two_skills):  # noqa: F811
    client.post("/api/projects", json={"name": "Alpha"})
    client.post(f"/api/skills/figure/projects/alpha?version={version_of(client, 'figure')}")
    assert client.post(f"/api/skills/figure/projects/alpha?version={version_of(client, 'figure')}").status_code == 200
    assert list(row(client, "figure")["projects"]) == ["alpha"]


def test_a_collision_is_refused_with_both_names(client, two_skills):  # noqa: F811
    """Two skills claiming one module is a rename, not a precedence puzzle."""
    from dex import skills

    clash = two_skills / "skills" / "other" / "new" / "utils"
    clash.mkdir(parents=True)
    (clash / "rig.py").write_text("R = 2\n")
    (two_skills / "skills" / "other" / "new" / skills.MANIFEST_NAME).write_text(
        json.dumps({"name": "other"}) + "\n"
    )
    skills.publish(skills.read(two_skills / "skills" / "other" / "new"))

    client.post("/api/projects", json={"name": "Alpha"})
    client.post(f"/api/skills/figure/projects/alpha?version={version_of(client, 'figure')}")
    response = client.post(f"/api/skills/other/projects/alpha?version={version_of(client, 'other')}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "figure" in detail and "other" in detail and "rig.py" in detail
    # And the project keeps what it had.
    assert (client.config.project_dir("alpha") / "utils" / "rig.py").read_text() == "R = 1\n"  # type: ignore[attr-defined]


def test_an_unknown_skill_or_project_is_a_404(client, two_skills):  # noqa: F811
    client.post("/api/projects", json={"name": "Alpha"})
    assert client.post("/api/skills/imaginary/projects/alpha").status_code == 404
    assert client.post("/api/skills/figure/projects/imaginary").status_code == 404


def test_two_versions_are_listed_separately(client, two_skills):  # noqa: F811
    """A newer version arriving does not replace the one in use."""
    from dex import skills

    old = version_of(client, "figure")
    # What a copy from another install looks like: a second directory.
    newer = two_skills / "skills" / "figure" / "aaaaaa"
    (newer / "utils").mkdir(parents=True)
    (newer / "utils" / "rig.py").write_text("R = 2\n")
    (newer / skills.MANIFEST_NAME).write_text(
        json.dumps({"name": "figure", "description": "the figure skill"}) + "\n"
    )

    assert sorted(v["version"] for v in row(client, "figure")["versions"]) == sorted(
        [old, "aaaaaa"]
    )


def test_moving_a_project_to_a_newer_version(client, two_skills):  # noqa: F811
    """Migration: the reason the version is in the directory name at all."""
    from dex import skills

    client.post("/api/projects", json={"name": "Alpha"})
    old = version_of(client, "figure")
    client.post(f"/api/skills/figure/projects/alpha?version={old}")
    utils = client.config.project_dir("alpha") / "utils"  # type: ignore[attr-defined]
    assert (utils / "rig.py").read_text() == "R = 1\n"

    newer = two_skills / "skills" / "figure" / "aaaaaa"
    (newer / "utils").mkdir(parents=True)
    (newer / "utils" / "rig.py").write_text("R = 2\n")
    (newer / skills.MANIFEST_NAME).write_text(
        json.dumps({"name": "figure", "description": "the figure skill"}) + "\n"
    )

    assert client.post(
        "/api/skills/figure/projects/alpha?version=aaaaaa"
    ).status_code == 200
    # Moved across, not added beside: a project is on one version of a skill.
    assert (utils / "rig.py").read_text() == "R = 2\n"
    assert row(client, "figure")["projects"] == {"alpha": "aaaaaa"}


def test_moving_back_to_the_older_version(client, two_skills):  # noqa: F811
    """The direction that matters when the new one turns out to be wrong."""
    from dex import skills

    client.post("/api/projects", json={"name": "Alpha"})
    old = version_of(client, "figure")
    newer = two_skills / "skills" / "figure" / "aaaaaa"
    (newer / "utils").mkdir(parents=True)
    (newer / "utils" / "rig.py").write_text("R = 2\n")
    (newer / skills.MANIFEST_NAME).write_text(
        json.dumps({"name": "figure", "description": "x"}) + "\n"
    )

    client.post("/api/skills/figure/projects/alpha?version=aaaaaa")
    client.post(f"/api/skills/figure/projects/alpha?version={old}")

    utils = client.config.project_dir("alpha") / "utils"  # type: ignore[attr-defined]
    assert (utils / "rig.py").read_text() == "R = 1\n"


def test_an_unknown_version_is_a_404(client, two_skills):  # noqa: F811
    client.post("/api/projects", json={"name": "Alpha"})
    assert client.post(
        "/api/skills/figure/projects/alpha?version=zzzzzz"
    ).status_code == 404


# ------------------------------------------------------ editing a SKILL.md


def test_a_project_lists_only_the_skills_it_has_on(client, two_skills):  # noqa: F811
    client.post("/api/projects", json={"name": "Alpha"})
    client.post(f"/api/skills/figure/projects/alpha?version={version_of(client, 'figure')}")

    listed = client.get("/api/projects/alpha/skills").json()["skills"]
    assert [s["name"] for s in listed] == ["figure"]


def test_reading_a_skill_doc(client, two_skills):  # noqa: F811
    from dex import skills

    skill = skills.find(two_skills, "figure")
    (skill.path / "SKILL.md").write_text("# Figure\n\nWhat it does.\n")

    body = client.get(f"/api/skills/figure/doc?version={skill.version}").json()
    assert "What it does." in body["text"]
    assert body["version"] == skill.version


def test_editing_a_skill_doc_publishes_a_new_version(client, two_skills):  # noqa: F811
    """Never in place: a published version is what somebody is running on.

    Its version *is* a hash of its contents, so editing it would leave the name
    describing something that no longer exists. The edit forks and publishes,
    which is the same path the design chat takes.
    """
    client.post("/api/projects", json={"name": "Alpha"})
    old = version_of(client, "figure")
    client.post(f"/api/skills/figure/projects/alpha?version={old}")

    body = client.put(
        f"/api/skills/figure/doc?version={old}", json={"text": "# Figure\n\nRewritten.\n"}
    ).json()

    assert body["version"] != old
    assert body["moved"] == ["alpha"]
    # Both versions are on disk, and the project moved to the new one.
    assert row(client, "figure")["projects"] == {"alpha": body["version"]}
    assert sorted(v["version"] for v in row(client, "figure")["versions"]) == sorted(
        [old, body["version"]]
    )


def test_editing_a_doc_to_the_same_text_publishes_nothing(client, two_skills):  # noqa: F811
    from dex import skills

    skill = skills.find(two_skills, "figure")
    (skill.path / "SKILL.md").write_text("# Figure\n")
    skills.publish(skill)
    current = version_of(client, "figure")

    body = client.put(
        f"/api/skills/figure/doc?version={current}", json={"text": "# Figure\n"}
    ).json()
    assert body["version"] == current
    assert body["moved"] == []


def test_editing_a_skill_that_is_not_there_is_a_404(client, two_skills):  # noqa: F811
    assert client.put(
        "/api/skills/imaginary/doc", json={"text": "x"}
    ).status_code == 404


# ---------------------------------------------------------------- export/import


def export_of(client, name: str, version: str = "") -> bytes:
    got = client.get(f"/api/skills/{name}/export", params={"version": version})
    assert got.status_code == 200, got.text
    return got.content


def do_import(client, blob: bytes, filename: str = "skill.tar.gz"):
    return client.post(
        "/api/skills/import", files={"file": (filename, blob, "application/gzip")}
    )


def test_exporting_a_skill_names_the_version_it_contains(client, two_skills):  # noqa: F811
    current = version_of(client, "figure")
    got = client.get("/api/skills/figure/export", params={"version": current})

    assert got.headers["content-type"] == "application/gzip"
    assert f"figure@{current}.tar.gz" in got.headers["content-disposition"]

    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(got.content)) as archive:
        names = sorted(archive.getnames())
    # Paths are relative to the version directory and carry no version: the
    # receiving install computes that from what it actually got.
    assert names == ["skill.json", "utils/API.md", "utils/rig.py"]


def test_exporting_takes_the_version_asked_for_not_the_current_one(client, two_skills):  # noqa: F811
    from dex import skills

    old = version_of(client, "figure")
    client.put(f"/api/skills/figure/doc?version={old}", json={"text": "# Changed\n"})
    assert version_of(client, "figure") != old

    with __import__("tarfile").open(
        fileobj=__import__("io").BytesIO(export_of(client, "figure", old))
    ) as archive:
        manifest = json.loads(archive.extractfile("skill.json").read())
    assert manifest["version"] == old
    assert skills.find(client.config.workspace, "figure", old) is not None  # type: ignore[attr-defined]


def test_a_skill_survives_the_round_trip_to_another_install(client, two_skills, tmp_path):  # noqa: F811
    """Export here, import into a workspace that has never seen it."""
    from dex import skills

    blob = export_of(client, "figure")
    elsewhere = tmp_path / "other-install"
    elsewhere.mkdir()

    got = skills.import_archive(elsewhere, blob)

    assert got.fresh
    assert got.skill.name == "figure"
    assert got.skill.version == version_of(client, "figure")
    assert (got.skill.path / "utils" / "rig.py").read_text() == "R = 1\n"


def test_importing_resolves_to_the_skill_of_that_name(client, two_skills, tmp_path):  # noqa: F811
    """An archive of `figure` becomes another version of `figure`, not a sibling."""
    from dex import skills

    original = version_of(client, "figure")
    blob = export_of(client, "figure", original)
    # The local copy moves on, so the archive is now an older version.
    client.put(f"/api/skills/figure/doc?version={original}", json={"text": "# Local\n"})
    newer = version_of(client, "figure")
    draft = skills.fork(client.config.workspace, "figure", newer)  # type: ignore[attr-defined]
    (draft.path / "utils" / "rig.py").write_text("R = 2\n")
    skills.adopt(client.config.workspace, client.config.assets_dir)  # type: ignore[attr-defined]

    body = do_import(client, blob).json()

    assert body["name"] == "figure"
    assert body["version"] == original
    # One skill, three versions in its dropdown — not a second row.
    names = [s["name"] for s in client.get("/api/skills").json()["skills"]]
    assert names.count("figure") == 1
    assert original in [v["version"] for v in row(client, "figure")["versions"]]


def test_an_imported_version_is_enabled_for_nothing(client, two_skills):  # noqa: F811
    """Arriving is not adopting. The operator chooses when to move."""
    client.post("/api/projects", json={"name": "Alpha"})
    client.post("/api/projects", json={"name": "Beta"})
    original = version_of(client, "figure")
    client.post(f"/api/skills/figure/projects/alpha?version={original}")
    client.post(f"/api/skills/figure/projects/beta?version={original}")

    # A different install's build of the same skill.
    from dex import skills

    draft = skills.fork(client.config.workspace, "figure", original)  # type: ignore[attr-defined]
    (draft.path / "utils" / "rig.py").write_text("R = 99\n")
    published = skills.publish(draft)
    blob = skills.export(published)
    __import__("shutil").rmtree(published.path)  # as though it had never been here

    fresh = do_import(client, blob).json()["version"]

    # Both projects are still where they were.
    assert row(client, "figure")["projects"] == {"alpha": original, "beta": original}

    # Enabling it in one project moves only that project, and the previously
    # activated version goes off there — that is what testing one first means.
    client.post(f"/api/skills/figure/projects/alpha?version={fresh}")
    assert row(client, "figure")["projects"] == {"alpha": fresh, "beta": original}


def test_importing_what_is_already_here_changes_nothing(client, two_skills):  # noqa: F811
    current = version_of(client, "figure")
    body = do_import(client, export_of(client, "figure", current)).json()

    assert body["version"] == current
    assert body["fresh"] is False
    assert [v["version"] for v in row(client, "figure")["versions"]] == [current]


def test_an_archive_that_writes_outside_its_directory_is_refused(client, two_skills):  # noqa: F811
    """A tarball is somebody else's bytes; `..` in a member is the whole risk.

    Hostile in one member only — everything else is a perfectly good skill, so
    the refusal has to come from the traversal rather than from the archive
    being unusable for some other reason.
    """
    import io
    import tarfile

    def member(archive, name, body):
        info = tarfile.TarInfo(name)
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))

    hostile = io.BytesIO()
    with tarfile.open(fileobj=hostile, mode="w:gz") as archive:
        member(archive, "skill.json", json.dumps({"name": "figure"}).encode())
        member(archive, "utils/rig.py", b"R = 1\n")
        member(archive, "../../escaped.py", b"pwned = True\n")

    got = do_import(client, hostile.getvalue())

    assert got.status_code == 400
    # Specifically the traversal: without the extraction filter this archive
    # unpacks cleanly and imports, so a bare 400 would prove nothing.
    assert "outside the destination" in got.json()["detail"]
    # Nothing was left behind — not a version, not a half-unpacked staging dir.
    assert [v["version"] for v in row(client, "figure")["versions"]] == [
        version_of(client, "figure")
    ]
    workspace = client.config.workspace  # type: ignore[attr-defined]
    assert not [p for p in (workspace / "skills").rglob("*") if "incoming-" in p.name]


def test_an_archive_that_is_not_a_skill_is_refused(client, two_skills):  # noqa: F811
    import io
    import tarfile

    nameless = io.BytesIO()
    with tarfile.open(fileobj=nameless, mode="w:gz") as archive:
        body = b"hello\n"
        info = tarfile.TarInfo("notes.txt")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))

    assert do_import(client, nameless.getvalue()).status_code == 400
    assert do_import(client, b"this is not a tarball at all").status_code == 400


def test_exporting_a_skill_that_is_not_there_is_a_404(client, two_skills):  # noqa: F811
    assert client.get("/api/skills/imaginary/export").status_code == 404


def test_a_committed_widget_bundle_travels_with_the_skill(client, two_skills):  # noqa: F811
    """`dist/` is out of the *hash*, not out of the skill.

    The bundle embeds its own source path, and that path contains the version
    the hash is computing — so hashing it never settles. It still has to be in
    the archive: without it the widget has to be built before it will open, and
    the receiving install has no build step.
    """
    import io
    import tarfile

    from dex import skills

    figure = skills.find(client.config.workspace, "figure")  # type: ignore[attr-defined]
    bundle = figure.path / "widgets" / "skeleton" / "dist" / "index.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("export default 1\n")
    (bundle.parent.parent / "widget.json").write_text('{"id": "skeleton"}\n')
    before = skills.publish(figure).version

    blob = export_of(client, "figure", before)
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        assert "widgets/skeleton/dist/index.js" in archive.getnames()
        assert archive.extractfile("widgets/skeleton/dist/index.js").read() == (
            b"export default 1\n"
        )

    # And carrying an unhashed file did not move the version.
    landed = skills.import_archive(client.config.workspace.parent / "elsewhere", blob)  # type: ignore[attr-defined]
    assert landed.skill.version == before
    assert (landed.skill.path / "widgets" / "skeleton" / "dist" / "index.js").exists()


def test_an_archive_naming_a_skill_that_is_not_a_directory_name_is_refused(client, two_skills):  # noqa: F811
    """The name becomes a directory, so it is checked as one — and not rewritten.

    Quietly sanitising `../evil` into `evil` would file the archive under a
    name its manifest never claimed, which is worse than refusing it.
    """
    import io
    import tarfile

    for claimed in ("../evil", "..", ".hidden", ""):
        blob = io.BytesIO()
        with tarfile.open(fileobj=blob, mode="w:gz") as archive:
            body = json.dumps({"name": claimed}).encode()
            info = tarfile.TarInfo("skill.json")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
            mod = b"R = 1\n"
            info = tarfile.TarInfo("utils/rig.py")
            info.size = len(mod)
            archive.addfile(info, io.BytesIO(mod))

        got = do_import(client, blob.getvalue())
        assert got.status_code == 400, f"{claimed!r} was accepted"

    workspace = client.config.workspace  # type: ignore[attr-defined]
    assert sorted(p.name for p in (workspace / "skills").iterdir()) == ["figure", "packages"]


def test_an_import_leaves_no_staging_directory_behind(client, two_skills):  # noqa: F811
    """An `incoming-` directory left on disk would read as a version.

    Both paths are checked: the archive that publishes, and the duplicate that
    is thrown away because this install already had that content.
    """
    import shutil

    from dex import skills

    workspace = client.config.workspace  # type: ignore[attr-defined]
    # Captured now: the import becomes the newest, so "current" moves.
    original = version_of(client, "figure")

    def staging() -> list[str]:
        return [
            p.name for p in (workspace / "skills" / "figure").iterdir()
            if p.name.startswith(skills.INCOMING)
        ]

    # Content this install does not have: built here, then removed again.
    draft = skills.fork(workspace, "figure", original)
    (draft.path / "utils" / "rig.py").write_text("R = 7\n")
    published = skills.publish(draft)
    arriving = skills.export(published)
    shutil.rmtree(published.path)

    assert do_import(client, arriving).json()["fresh"] is True
    assert staging() == []

    # The same archive again: already here, so nothing new and nothing left.
    assert do_import(client, arriving).json()["fresh"] is False
    assert staging() == []

    assert sorted(v["version"] for v in row(client, "figure")["versions"]) == sorted(
        [published.version, original]
    )
