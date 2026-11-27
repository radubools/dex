"""The dex-remote bridge (remote.py): what it says, and what it does with a request."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from dex.bus import EventBus
from dex.models import Event, TaskState
from dex.queue import TaskManager
from dex.remote import (
    RemoteBridge,
    RemoteConfig,
    field_path,
    from_value,
    scan_files,
    setting_key,
    summarize,
    to_value,
    tool_line,
)
from dex.store import SettingsStore, ThreadStore

# ------------------------------------------------------------------ one-liners


@pytest.mark.parametrize(("name", "data", "line"), [
    ("Read", {"file_path": "/a/b/utils.py"}, "Reading utils.py"),
    ("Write", {"file_path": "/a/SPEC.md", "content": "secret code"}, "Writing SPEC.md"),
    ("Edit", {"file_path": "x/y.ts", "old_string": "a", "new_string": "b"}, "Editing y.ts"),
    ("Bash", {"command": "rm -rf /tmp/x", "description": "Clean the scratch dir"}, "Clean the scratch dir"),
    ("Bash", {"command": "cd /x && FOO=1 .venv/bin/python -m pytest tests -q"}, "Running tests"),
    ("Bash", {"command": ".venv/bin/python scripts/build.py --fast"}, "Running build.py"),
    ("Bash", {"command": "ffmpeg -i a.mp4 -c copy b.mp4"}, "Processing media"),
    ("Bash", {"command": "jq '.x' secret.json"}, "Running jq"),
    ("Grep", {"pattern": "password"}, "Searching files"),
    ("WebFetch", {"url": "https://example.com/a/b?q=1"}, "Fetching example.com"),
    ("mcp__dex__start_task", {"title": "x"}, "Starting a sub-task"),
])
def test_tool_lines_name_the_step_not_its_content(name, data, line):
    assert tool_line(name, data) == line


def test_summaries_drop_prose_and_dedupe_states():
    assert summarize("text_delta", {"delta": "I think"}) is None
    assert summarize("thinking", {"text": "hmm"}) is None
    assert summarize("diff", {"patch": "+x"}) is None
    assert summarize("task_state", {"state": "running"}) == ("state", "Running")
    assert summarize("task_state", {"state": "running"}, "running") is None
    assert summarize("result", {"summary": "Built the package.\n- a.py"}) == (
        "summary", "Built the package.\n- a.py")
    assert summarize("error", {"message": "boom\nTraceback..."})[0] == "error"
    assert summarize("approval", {"auto": True}) is None
    assert summarize("tool", {"name": "Write", "input": {}, "title": "Write solutions.py"}) == (
        "step", "Writing solutions.py")


def test_firestore_values_round_trip():
    value = {"a": 1, "b": [True, None, 2.5, "x"], "c": {"d": "e"}}
    assert from_value(to_value(value)) == value
    assert field_path("projects", "ml-x") == "projects.`ml-x`"
    assert field_path("machines", "mac_studio") == "machines.mac_studio"


def test_scan_skips_hidden_tool_dirs_and_fresh_files(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / ".cues").mkdir()
    (tmp_path / ".cues" / "c.wav").write_text("x")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_text("x")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "v.mp4").write_text("xyz")
    old = time.time() - 60
    for p in tmp_path.rglob("*"):
        os.utime(p, (old, old))
    (tmp_path / "fresh.txt").write_text("being written")
    found = scan_files(tmp_path)
    assert sorted(found) == ["a.py", "out/v.mp4"]
    assert found["out/v.mp4"][0] == 3


def test_config_absent_means_no_bridge(tmp_path: Path):
    assert RemoteConfig.load(tmp_path / "nope.json") is None
    (tmp_path / "sa.json").write_text(json.dumps({"type": "service_account", "project_id": "p"}))
    (tmp_path / "remote.json").write_text(json.dumps({"bucket": "b", "credentials": "sa.json"}))
    loaded = RemoteConfig.load(tmp_path / "remote.json")
    assert loaded and loaded.gcp_project == "p" and loaded.topic == "dex-tasks"


# ---------------------------------------------------------------- the bridge


class FakeGoogle:
    """Firestore, Pub/Sub and Storage, in memory."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.queue: dict[str, list[dict[str, Any]]] = {}
        self.acked: list[str] = []
        self.objects: dict[str, bytes] = {}
        self.subscriptions: set[str] = set()
        self.commits: list[list[dict[str, Any]]] = []

    def doc_name(self, path: str) -> str:
        return path

    async def get_doc(self, path: str):
        return self.docs.get(path)

    def update(self, path, fields, mask=None):
        return {"path": path, "fields": fields, "mask": mask}

    async def commit(self, writes):
        self.commits.append(writes)
        for w in writes:
            doc = self.docs.setdefault(w["path"], {})
            if w["mask"] is None:
                doc.clear()
                doc.update(w["fields"])
                continue
            for path in w["mask"]:
                parts = [p.strip("`") for p in path.split(".")]
                src: Any = w["fields"]
                for part in parts:
                    src = src.get(part) if isinstance(src, dict) else None
                dst = doc
                for part in parts[:-1]:
                    dst = dst.setdefault(part, {})
                if src is None:
                    dst.pop(parts[-1], None)
                else:
                    dst[parts[-1]] = src

    async def ensure_subscription(self, project):
        self.subscriptions.add(project)

    async def pull(self, project, limit=10):
        out, self.queue[project] = self.queue.get(project, [])[:limit], self.queue.get(project, [])[limit:]
        return out

    async def ack(self, project, ack_ids):
        self.acked += ack_ids

    async def upload(self, name, path):
        self.objects[name] = path.read_bytes()

    async def delete_object(self, name):
        self.objects.pop(name, None)

    async def close(self):
        pass

    async def list_docs(self, collection):
        return {p.split("/", 1)[1]: d for p, d in self.docs.items() if p.startswith(collection + "/") and p.count("/") == 1}

    async def topic_publishers(self, emails):
        self.publishers = set(emails)
        return []

    async def download(self, name):
        return self.objects.get(name, b"")

    def request(self, project: str, rid: str, token: str = "", **body: Any) -> None:
        data = json.dumps({"requestId": rid, "idToken": token, **body})
        self.queue.setdefault(project, []).append({
            "ackId": f"ack-{rid}",
            "message": {"data": base64.b64encode(data.encode()).decode(), "attributes": {"project": project},
                        "publishTime": "2026-11-27T10:00:00.123456789Z"},
        })


class FakeVerifier:
    """Token "<email>|<uid>" is valid; anything else is not."""

    async def verify(self, token: str, at: float):
        if "|" not in token:
            raise ValueError("no sign-in token")
        email, uid = token.split("|")
        return {"email": email, "email_verified": True, "user_id": uid, "sub": uid}


def _authorise(google, email: str, role: str, *projects: str) -> None:
    """A users/<email> document, as the operator writes it in the Firebase console."""
    google.docs[f"users/{email}"] = {"role": role, "projects": {p: True for p in projects}}


@pytest.fixture
async def bridge(db, config):
    await db.pool.execute("DELETE FROM remote_tasks")
    await db.pool.execute("INSERT INTO projects (slug, name) VALUES ('ml', 'Machine learning')")
    bus = EventBus(db)
    manager = TaskManager(config, bus, db)  # not started: nothing runs
    google = FakeGoogle()
    made = RemoteBridge(config, db, manager, ThreadStore(db),
                        remote=RemoteConfig("p", "b", "dex-tasks", {}), google=google)  # type: ignore[arg-type]
    made.verifier = FakeVerifier()
    await SettingsStore(db).set(setting_key("ml"), True)
    yield made, google, manager, bus
    await bus.stop()


async def test_projects_and_publishers_follow_the_firebase_user_list(bridge, db):
    made, google, *_ = bridge
    _authorise(google, "op@x.com", "user", "ml")
    _authorise(google, "boss@x.com", "admin")
    _authorise(google, "knocking@x.com", "pending")
    await made.sync_access(await made.enabled_projects())
    assert google.publishers == {"op@x.com", "boss@x.com"}
    assert google.docs["projects/ml"]["name"] == "Machine learning"
    assert made.status.machine in google.docs["projects/ml"]["machines"]

    # Switched off: this machine is taken off the project.
    await SettingsStore(db).set(setting_key("ml"), False)
    await made.sync_access(await made.enabled_projects())
    assert google.docs["projects/ml"].get("machines") == {}


def test_who_is_allowed():
    allows = RemoteBridge.allows
    assert allows({"role": "admin"}, "ml")
    assert allows({"role": "user", "projects": {"ml": True}}, "ml")
    assert not allows({"role": "user", "projects": {"ml": True}}, "other")
    assert not allows({"role": "pending", "projects": {"ml": True}}, "ml")
    assert not allows(None, "ml")


async def test_a_request_becomes_one_local_task_and_is_acked(bridge, db):
    made, google, manager, _ = bridge
    _authorise(google, "op@x.com", "user", "ml")
    google.request("ml", "req-0001", token="op@x.com|u1", title="Plot it", problem="Plot the loss curve")
    await made.poll("ml")
    assert google.acked == ["ack-req-0001"]
    row = await db.pool.fetchrow("SELECT task_id, email FROM remote_tasks")
    task = await manager.tasks.get(row["task_id"])
    assert task.project == "ml" and task.title == "Plot it" and task.state is TaskState.QUEUED
    assert google.docs["projects/ml/requests/req-0001"]["status"] == "accepted"
    assert google.docs[f"projects/ml/tasks/{task.id}"]["email"] == "op@x.com"

    # Redelivered: acked again, no second task.
    google.request("ml", "req-0001", token="op@x.com|u1", title="Plot it", problem="again")
    await made.poll("ml")
    assert await db.pool.fetchval("SELECT count(*) FROM tasks WHERE project = 'ml'") == 1


async def test_a_request_from_someone_without_a_grant_is_rejected(bridge, db):
    made, google, *_ = bridge
    _authorise(google, "other@x.com", "user", "algorithms")
    google.request("ml", "req-0002", token="other@x.com|u2", title="x", problem="y")
    await made.poll("ml")
    assert google.acked == ["ack-req-0002"]
    assert google.docs["projects/ml/requests/req-0002"]["status"] == "rejected"
    assert await db.pool.fetchval("SELECT count(*) FROM tasks WHERE project = 'ml'") == 0


async def test_mirror_sends_one_liners_and_files(bridge, db, config):
    made, google, manager, bus = bridge
    _authorise(google, "op@x.com", "admin")
    google.request("ml", "req-0003", token="op@x.com|u1", title="Fit", problem="Fit a model")
    await made.poll("ml")
    task_id = await db.pool.fetchval("SELECT task_id FROM remote_tasks")
    task = await manager.tasks.get(task_id)

    await bus.start()
    for type_, data in [
        ("task_state", {"state": "running"}),
        ("thinking_delta", {"delta": "secret reasoning"}),
        ("tool", {"name": "Bash", "input": {"command": "python -m pytest -q"}}),
        ("text", {"text": "a long explanation"}),
        ("question", {"id": "q1", "question": "Which loss?", "options": ["mse", "mae"]}),
        ("question_answered", {"id": "q1", "answer": "mse"}),
        ("result", {"summary": "Fitted the model."}),
        ("task_state", {"state": "succeeded"}),
    ]:
        bus.publish(Event(type=type_, data=data, task_id=task_id, project="ml"))
    await bus.stop()
    await manager.tasks.set_state(task_id, TaskState.SUCCEEDED)

    out = task.output_dir(config.assets_dir)
    (out / "plots").mkdir(parents=True)
    (out / "plots" / "loss.png").write_bytes(b"png")
    old = time.time() - 60
    os.utime(out / "plots" / "loss.png", (old, old))

    await made.mirror_all()
    doc = google.docs[f"projects/ml/tasks/{task_id}"]
    events = sorted((p, d) for p, d in google.docs.items() if p.startswith(f"projects/ml/tasks/{task_id}/events/"))
    assert [d["text"] for _, d in events] == ["Running", "Running tests", "Asked: Which loss?", "Answered: mse",
                                              "Fitted the model.", "Succeeded"]
    assert doc["state"] == "succeeded" and doc["summary"] == "Fitted the model."
    assert doc.get("question") is None
    assert doc["files"] == [{"path": "plots/loss.png", "size": 3, "available": True,
                             "object": f"projects/ml/tasks/{task_id}/files/plots/loss.png"}]
    assert google.objects[f"projects/ml/tasks/{task_id}/files/plots/loss.png"] == b"png"
    assert await db.pool.fetchval("SELECT done FROM remote_tasks") is True

    # Nothing new: nothing written.
    before = len(google.commits)
    await made.mirror_all()
    assert len(google.commits) == before


async def test_the_token_not_the_body_says_who_sent_it(bridge, db):
    made, google, *_ = bridge
    _authorise(google, "op@x.com", "user", "ml")
    # Claims to be the operator, but carries no valid token.
    google.request("ml", "req-0004", token="", email="op@x.com", title="x", problem="y")
    # Someone without a grant, naming the operator in the body.
    google.request("ml", "req-0005", token="mallory@x.com|u9", email="op@x.com", title="x", problem="y")
    await made.poll("ml")
    assert google.docs["projects/ml/requests/req-0004"]["status"] == "rejected"
    assert google.docs["projects/ml/requests/req-0005"]["status"] == "rejected"
    assert google.docs["projects/ml/requests/req-0005"]["email"] == "mallory@x.com"
    assert await db.pool.fetchval("SELECT count(*) FROM remote_tasks") == 0


async def test_attachments_are_fetched_from_the_senders_own_area_only(bridge, db, config):
    made, google, manager, _ = bridge
    _authorise(google, "op@x.com", "user", "ml")
    mine = "projects/ml/uploads/u1/req-0006/data.csv"
    theirs = "projects/ml/uploads/u2/req-0006/secret.csv"
    google.objects[mine] = b"a,b\n1,2\n"
    google.objects[theirs] = b"secret"
    google.request("ml", "req-0006", token="op@x.com|u1", title="Read", problem="Summarise the data",
                   uploads=[{"object": mine}, {"object": theirs}])
    await made.poll("ml")
    task = await manager.tasks.get(await db.pool.fetchval("SELECT task_id FROM remote_tasks"))
    assert "data.csv" in task.problem and "secret.csv" not in task.problem
    stored = config.project_datasets("ml") / "remote" / "req-0006" / "data.csv"
    assert stored.read_bytes() == b"a,b\n1,2\n"


async def test_token_verifier_checks_signature_audience_and_time():
    import httpx
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID
    import datetime as dt

    from dex.remote import TokenVerifier, _b64url

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "t")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"k1": pem})))

    def token(**claims):
        base = {"aud": "p", "iss": "https://securetoken.google.com/p", "email": "a@x.com",
                "email_verified": True, "iat": 1000, "exp": 4600, **claims}
        signing = f"{_b64url(json.dumps({'alg': 'RS256', 'kid': 'k1'}).encode())}.{_b64url(json.dumps(base).encode())}"
        return f"{signing}.{_b64url(key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256()))}"

    verifier = TokenVerifier("p", http)
    assert (await verifier.verify(token(), at=2000))["email"] == "a@x.com"
    for bad, at in [(token(aud="other"), 2000), (token(), 9000), (token(email_verified=False), 2000),
                    (token()[:-4] + "AAAA", 2000)]:
        with pytest.raises(ValueError):
            await verifier.verify(bad, at=at)


async def test_an_open_question_is_mirrored_and_answered_from_the_page(bridge, db):
    made, google, manager, bus = bridge
    _authorise(google, "op@x.com", "user", "ml")
    _authorise(google, "other@x.com", "user", "algorithms")
    google.request("ml", "req-0007", token="op@x.com|u1", title="Ask", problem="Ask me something")
    await made.poll("ml")
    task_id = await db.pool.fetchval("SELECT task_id FROM remote_tasks")
    await bus.start()
    bus.publish(Event(type="question", data={"id": "q9", "question": "Indices or values?",
                                             "options": ["indices", "values"]}, task_id=task_id, project="ml"))
    await bus.stop()
    await made.mirror_all()
    assert google.docs[f"projects/ml/tasks/{task_id}"]["question"] == {
        "id": "q9", "text": "Indices or values?", "options": ["indices", "values"]}

    answered = []
    manager.answer = lambda tid, qid, value: answered.append((tid, qid, value)) or True

    def answer(rid, token, **body):
        data = json.dumps({"kind": "answer", "taskId": task_id, "questionId": "q9", "idToken": token, **body})
        google.queue.setdefault("ml", []).append({"ackId": f"ack-{rid}", "message": {
            "data": base64.b64encode(data.encode()).decode(), "publishTime": "2026-11-27T10:00:00Z"}})

    answer("a1", "other@x.com|u2", answer="values")   # not authorised for ml
    answer("a2", "", answer="values")                 # no token
    answer("a3", "op@x.com|u1", answer="indices")
    await made.poll("ml")
    assert answered == [(task_id, "q9", "indices")]
    assert set(google.acked) >= {"ack-a1", "ack-a2", "ack-a3"}
