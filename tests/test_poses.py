"""Pose files are ordinary assets, served through the assets endpoint."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.config import Config
from dex.projects import ProjectStore

LANDMARKS = [
    "TOP_OF_HEAD", "TOP_OF_THORACIC_SPINE", "TOP_OF_LUMBAR_SPINE", "BOTTOM_OF_SACRAL_SPINE",
    "LEFT_SHOULDER", "RIGHT_SHOULDER", "LEFT_ELBOW", "RIGHT_ELBOW",
    "LEFT_WRIST", "RIGHT_WRIST", "LEFT_CENTRAL_FINGERTIP", "RIGHT_CENTRAL_FINGERTIP",
    "LEFT_HIP", "RIGHT_HIP", "LEFT_KNEE", "RIGHT_KNEE",
    "LEFT_ANKLE", "RIGHT_ANKLE", "LEFT_CENTRAL_TOETIP", "RIGHT_CENTRAL_TOETIP",
]


@pytest.fixture
def client(config: Config, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        c.config = config  # type: ignore[attr-defined]
        yield c


def write_pose(config: Config, project: str, name: str) -> dict:
    directory = config.project_dir(project) / "poses"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "pose": name,
        "display_name": name.replace("_", " ").title(),
        "landmarks": {m: [0.0, float(i) / 10, 0.0] for i, m in enumerate(LANDMARKS)},
    }
    (directory / f"{name}.json").write_text(json.dumps(payload))
    return payload


def test_a_pose_directory_lists_like_any_other(client, config):
    ProjectStore(client.app.state.db, config.assets_dir)
    write_pose(config, "algorithms", "mountain_pose")

    listing = client.get("/api/assets", params={"path": "algorithms/poses"}).json()
    assert listing["kind"] == "dir"
    assert [e["name"] for e in listing["entries"]] == ["mountain_pose.json"]


def test_a_pose_file_is_read_like_any_other_asset(client, config):
    write_pose(config, "algorithms", "warrior_ii")

    body = client.get("/api/assets", params={"path": "algorithms/poses/warrior_ii.json"}).json()
    assert body["kind"] == "file"
    payload = json.loads(body["content"])
    assert payload["pose"] == "warrior_ii"
    assert set(payload["landmarks"]) == set(LANDMARKS)


def test_poses_of_one_project_are_not_visible_from_another(client, config):
    write_pose(config, "algorithms", "tree_pose")
    assert client.get("/api/assets", params={"path": "yoga/poses"}).status_code == 404


def test_a_pose_path_cannot_escape_the_assets_root(client, config):
    write_pose(config, "algorithms", "mountain_pose")
    for path in ["algorithms/poses/../../../pyproject.toml", "../../etc/hosts"]:
        assert client.get("/api/assets", params={"path": path}).status_code in (403, 404)


def test_the_reference_pose_in_the_repository_is_well_formed():
    """The fixture the viewer was checked against must stay valid."""
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "assets" / "yoga" / "poses" / "mountain_pose.json"
    if not path.exists():
        pytest.skip("no reference pose checked in")

    payload = json.loads(path.read_text())
    assert payload["pose"] == path.stem
    assert set(payload["landmarks"]) == set(LANDMARKS)
    for name, value in payload["landmarks"].items():
        assert len(value) == 3 and all(isinstance(n, (int, float)) for n in value), name
    # Standing on the floor, roughly human sized.
    lowest = min(v[1] for v in payload["landmarks"].values())
    assert 0 <= lowest < 0.1
    assert 1.4 < payload["landmarks"]["TOP_OF_HEAD"][1] < 2.1


async def test_poses_are_gathered_from_task_directories_too(client, config):
    """A generated pose lives under the task that made it, not in `poses/`.

    dex gives each task its own output directory and confines its writes to it,
    so a pose a task generates cannot land in the project's shared `poses/`.
    The listing has to reach into task directories or the viewer never sees it.
    """
    project = config.assets_dir / "yoga"
    (project / "poses").mkdir(parents=True, exist_ok=True)
    (project / "poses" / "mountain.json").write_text('{"pose": "mountain"}')
    (project / "malasana-2" / "poses").mkdir(parents=True, exist_ok=True)
    (project / "malasana-2" / "poses" / "malasana.json").write_text('{"pose": "malasana"}')

    body = client.get("/api/assets", params={"path": "yoga", "match": "**/poses/*.json"}).json()
    names = {e["name"] for e in body["entries"]}
    assert names == {"yoga/poses/mountain.json", "yoga/malasana-2/poses/malasana.json"}
    # Each hit is readable back through the same endpoint.
    for name in names:
        assert client.get("/api/assets", params={"path": name}).status_code == 200


async def test_a_match_cannot_escape_the_assets_root(client):
    for pattern in ("../../*", "/etc/*", "**/../../../*"):
        assert client.get(
            "/api/assets", params={"path": "yoga", "match": pattern}
        ).status_code in (400, 404), pattern
