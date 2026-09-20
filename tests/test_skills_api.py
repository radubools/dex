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
