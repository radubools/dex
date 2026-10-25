"""Game chat: a game's chat panel talks to its project's Chats thread."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.config import Config
from dex.game_chat import _inside, _merge, chat_brief, ChatMessage, ChatContext, load_settings
from dex.models import Event, Task, TaskState
from dex.runner import TaskRunner, chat_interjection

TOKEN = "chat-secret-ml"


class ChattyRunner:
    """A chat run that stays up until the player says something, then answers."""

    def __init__(self, task, config, bus, store, settings=None):
        self.task, self.config, self.bus, self.store = task, config, bus, store

    def emit(self, type_, **data):
        self.bus.publish(Event(type=type_, data=data, task_id=self.task.id, project=self.task.project))

    async def run(self) -> None:
        self.task.output_dir(self.config.assets_dir).mkdir(parents=True, exist_ok=True)
        self.task.state = TaskState.RUNNING
        await self.store.set_state(self.task.id, TaskState.RUNNING)
        self.emit("task_state", state="running", activity="thinking")
        for word in ("Hello", " there", "!"):
            self.emit("text_delta", id="b0", delta=word)
        self.emit("block_end", id="b0")
        if "wait" in self.task.problem:
            self.task.inbox = asyncio.Queue()
            text = await asyncio.wait_for(self.task.inbox.get(), timeout=5)
            self.task.inbox = None
            self.emit("text", text=f"You said: {text}")
        self.emit("result", ok=True, turns=1, costUsd=0.0, summary="done")
        self.task.state = TaskState.SUCCEEDED
        await self.store.set_state(self.task.id, TaskState.SUCCEEDED)


@pytest.fixture
def client(config: Config, dsn: str, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("dex.queue.TaskRunner", ChattyRunner)
    game = tmp_path / "games" / "model-workshop"
    (game / "public" / "assets").mkdir(parents=True)
    try:
        with TestClient(create_app(config)) as c:
            c.post("/api/projects", json={"slug": "ml", "name": "ML"})
            thread = c.post("/api/threads", json={"title": "Chats", "project": "ml"}).json()
            c.thread_id = thread.get("id") or thread.get("thread", {}).get("id")  # type: ignore[attr-defined]
            config.state_dir.mkdir(parents=True, exist_ok=True)
            (config.state_dir / "game-chat.json").write_text(json.dumps({"projects": {"ml": {
                "game": "Model Workshop", "root": str(game), "token": TOKEN, "threadId": c.thread_id,
            }}}))
            c.game = game  # type: ignore[attr-defined]
            yield c
    except RuntimeError as exc:  # no database
        pytest.skip(str(exc))


@pytest.fixture(autouse=True)
def _ml_project(db):
    yield


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def wait_state(c, chat_id, states, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = c.get(f"/api/game-chat/ml/chats/{chat_id}/events", headers=AUTH).json()
        if body["chat"]["state"] in states:
            return body
        time.sleep(0.05)
    raise AssertionError(f"chat never reached {states}: {body['chat']}")


def test_needs_the_projects_token(client):
    t = client.thread_id
    assert client.get(f"/api/game-chat/ml/threads/{t}/chats").status_code == 401
    assert client.get(f"/api/game-chat/ml/threads/{t}/chats", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(f"/api/game-chat/games/threads/{t}/chats", headers=AUTH).status_code == 401
    assert client.get(f"/api/game-chat/ml/threads/{t}/chats", headers=AUTH).status_code == 200


def test_cors_for_games_on_other_origins(client):
    pre = client.options(f"/api/game-chat/ml/threads/{client.thread_id}/chats",
                         headers={"Origin": "http://macbook-pro-4.local:5176", "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == "http://macbook-pro-4.local:5176"
    got = client.get(f"/api/game-chat/ml/threads/{client.thread_id}/chats", headers={**AUTH, "Origin": "http://x:5176"})
    assert got.headers["access-control-allow-origin"] == "http://x:5176"


def test_only_the_projects_chats_thread(client):
    other = client.post("/api/threads", json={"title": "Elsewhere", "project": "ml"}).json()
    other_id = other.get("id") or other.get("thread", {}).get("id")
    r = client.post(f"/api/game-chat/ml/threads/{other_id}/messages", json={"text": "hi"}, headers=AUTH)
    assert r.status_code == 403
    assert client.post("/api/game-chat/ml/threads/nope/messages", json={"text": "hi"}, headers=AUTH).status_code == 404


def test_a_chat_starts_lists_and_polls_incrementally(client):
    t = client.thread_id
    r = client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH, json={
        "text": "Make the mentor wave", "context": {"view": "the lab", "destinations": {"character": "public/assets/character/<name>/"}},
    }).json()
    assert r["delivered"] == "started"
    chat = r["chatId"]
    first = wait_state(client, chat, {"succeeded"})
    types = [e["type"] for e in first["events"]]
    assert "result" in types
    # Deltas of one block arrive merged into one.
    deltas = [e for e in first["events"] if e["type"] == "text_delta"]
    assert len(deltas) == 1 and deltas[0]["delta"] == "Hello there!"
    # Nothing new since the last seq.
    again = client.get(f"/api/game-chat/ml/chats/{chat}/events", params={"after": first["last"]}, headers=AUTH).json()
    assert again["events"] == [] and again["last"] == first["last"]
    chats = client.get(f"/api/game-chat/ml/threads/{t}/chats", headers=AUTH).json()["chats"]
    assert [c["chatId"] for c in chats] == [chat]
    assert chats[0]["title"] == "Make the mentor wave"
    # The thread in dex shows the player's message and the chat's chip.
    thread = client.get(f"/api/threads/{t}").json()
    texts = [m["text"] for m in (thread.get("messages") or thread.get("thread", {}).get("messages", []))]
    assert "Make the mentor wave" in texts


def test_a_message_to_a_running_chat_goes_straight_in(client):
    t = client.thread_id
    chat = client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH, json={"text": "please wait for me"}).json()["chatId"]
    deadline = time.time() + 5
    delivered = None
    while time.time() < deadline:
        r = client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH, json={"text": "make it blue", "chatId": chat}).json()
        delivered = r["delivered"]
        if delivered == "live":
            break
        time.sleep(0.1)
    assert delivered == "live"
    done = wait_state(client, chat, {"succeeded"})
    texts = [e.get("text") for e in done["events"] if e["type"] == "text"]
    assert "You said: make it blue" in texts
    notes = [e["message"] for e in done["events"] if e["type"] == "task_message"]
    assert notes and notes[-1]["live"] is True


def test_a_message_to_a_finished_chat_continues_it(client):
    t = client.thread_id
    chat = client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH, json={"text": "hi"}).json()["chatId"]
    wait_state(client, chat, {"succeeded"})
    r = client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH, json={"text": "and another thing", "chatId": chat}).json()
    assert r["delivered"] == "followup" and r["taskId"] != chat
    body = wait_state(client, chat, {"succeeded"})
    assert len(body["attempts"]) == 2
    # Still one chat in the list, now on its second attempt.
    chats = client.get(f"/api/game-chat/ml/threads/{t}/chats", headers=AUTH).json()["chats"]
    assert len(chats) == 1 and chats[0]["attempts"] == 2 and chats[0]["taskId"] == r["taskId"]


def test_cancel_and_unknown_chats(client):
    t = client.thread_id
    assert client.post("/api/game-chat/ml/chats/nope/cancel", headers=AUTH).status_code == 404
    chat = client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH, json={"text": "please wait"}).json()["chatId"]
    wait_state(client, chat, {"running"})
    r = client.post(f"/api/game-chat/ml/chats/{chat}/cancel", headers=AUTH)
    assert r.status_code == 200
    wait_state(client, chat, {"cancelled"})


# ---- pure helpers --------------------------------------------------------------------


def test_destinations_stay_inside_the_game(tmp_path):
    root = tmp_path / "g"
    root.mkdir()
    assert _inside(root, "public/assets/story/<name>/") == str(root / "public/assets/story/<name>/")
    assert _inside(root, "../dex/src") is None
    assert _inside(root, "/etc/passwd") == str(root / "etc/passwd")
    brief = chat_brief({"game": "Model Workshop", "root": str(root), "project": "ml"},
                       ChatMessage(text="Add a lesson", context=ChatContext(view="lab", destinations={"story": "public/assets/story/", "bad": "../../x"})))
    assert "Model Workshop" in brief and str(root / "public/assets/story/") in brief and "../../x" not in brief
    # A chat delegates: it never builds or exports a package itself.
    assert "never made or edited in this chat" in brief and "export_story" not in brief and "Add a lesson" in brief


def test_merge_keeps_the_feed_and_trims_output():
    ev = [
        {"seq": 1, "type": "text_delta", "id": "a", "taskId": "t", "delta": "He"},
        {"seq": 2, "type": "text_delta", "id": "a", "taskId": "t", "delta": "llo"},
        {"seq": 3, "type": "cost", "taskId": "t", "costUsd": 0.1},
        {"seq": 4, "type": "cost", "taskId": "t", "costUsd": 0.2},
        {"seq": 5, "type": "tool_result", "taskId": "t", "id": "x", "output": "z" * 5000},
        {"seq": 6, "type": "text_delta", "id": "b", "taskId": "t", "delta": "!"},
    ]
    out = _merge(ev)
    assert [e["type"] for e in out] == ["text_delta", "cost", "tool_result", "text_delta"]
    assert out[0]["delta"] == "Hello" and out[0]["seq"] == 2
    assert out[1]["costUsd"] == 0.2
    assert out[2]["truncated"] and len(out[2]["output"]) < 4100


async def test_converse_interrupts_for_a_live_message_and_waits_for_both_turns(config, db):
    """The runner's chat loop against a stand-in SDK client."""
    from claude_agent_sdk import ResultMessage

    def result():
        return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s", total_cost_usd=0.0, result="ok")

    class FakeClient:
        def __init__(self):
            self.sent, self.interrupts = [], 0
            self.out: asyncio.Queue = asyncio.Queue()

        async def query(self, text):
            self.sent.append(text)
            # Each sent turn ends with a result, a little later.
            self.turn = asyncio.get_running_loop().call_later(0.05, self.out.put_nowait, result())

        async def interrupt(self):
            # As the CLI: the turn under way ends now, with its (error) result.
            self.interrupts += 1
            self.turn.cancel()
            self.out.put_nowait(result())

        async def receive_messages(self):
            while True:
                yield await self.out.get()

    from dex.bus import EventBus
    from dex.store import TaskStore
    bus = EventBus(db)
    task = Task(problem="x", title="chat", slug="chat-x", scope="chat")
    runner = TaskRunner(task, config, bus, TaskStore(db), None)
    observed = []
    runner._observe = observed.append  # what would be reported
    client = FakeClient()
    await client.query("opening")
    run = asyncio.create_task(runner._converse(client))
    await asyncio.sleep(0.01)
    task.inbox.put_nowait("make it blue")
    await asyncio.wait_for(run, timeout=3)
    assert client.interrupts == 1
    assert client.sent[0] == "opening" and client.sent[1] == chat_interjection("make it blue")
    assert task.inbox is None
    # The interrupted turn's result is not reported (it would read as a failure).
    assert len([m for m in observed if isinstance(m, ResultMessage)]) == 1


def test_a_chat_is_told_to_pull_what_other_tasks_made_with_the_games_own_puller(tmp_path):
    from dex.game_chat import ChatMessage, chat_brief, pull_command

    game = tmp_path / "games" / "lab"
    game.mkdir(parents=True)
    entry = {"game": "Lab", "root": str(game)}
    # No puller beside the game: it installs by export, as before.
    assert pull_command(entry) is None
    assert "--only" not in chat_brief(entry, ChatMessage(text="hello"))
    (tmp_path / "games" / "tools").mkdir()
    (tmp_path / "games" / "tools" / "pull_dex_assets.py").write_text("")
    cmd = pull_command(entry)
    assert cmd.endswith(f"pull_dex_assets.py {game}")
    brief = chat_brief(entry, ChatMessage(text="hello"))
    assert f"{cmd} --only <task-dir>" in brief and "never force it" in brief


def test_chat_brief_starts_a_lesson_set_with_the_catalog(tmp_path):
    brief = chat_brief({"game": "Lab", "root": str(tmp_path)}, ChatMessage(text="add statistics lessons"))
    assert "A new set of lessons" in brief and "story catalog" in brief
    # The catalog first, the stories after it.
    assert brief.index("catalog task") < brief.index("one story task per lesson")


SNAPSHOT = {
    "algorithm": "kmeans", "mode": "story", "level": "medium",
    "story": {"id": "kmeans-medium", "title": "k-means (medium)", "level": "medium", "schema": 2},
    "beat": {"label": "k-means", "index": 4, "of": 29, "beat": "assign", "section": "Assignment",
             "title": "", "text": "Now assign each point: p2 through p5 join c1.", "choice": None,
             "input": None, "done": False, "path": ["intro", "start", "ruler", "d2-table", "assign"],
             "vars": {"inertia0": 24.5}},
    "lab": {"state": "assigned", "entities": [{"id": "c1", "asset": "environment/centroid", "at": [1, 0, 2]}],
            "labels": [{"id": "pt:p2", "text": "x 1.50 · z -2.50"}], "points": [{"id": "p2", "x": 1.5, "z": -2.5, "cls": 0}],
            "params": [{"name": "k", "value": 2}]},
    "storyJson": {"schema": 2, "id": "kmeans-medium", "beats": [{"id": "assign", "say": "Now assign each point: ..."}]},
}


def test_the_lesson_on_screen_reaches_the_agent_hidden_from_the_player(client, config):
    t = client.thread_id
    r = client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH, json={
        "text": "this sentence is clumsy", "context": {"view": "k-means, medium", "story": SNAPSHOT}}).json()
    chat = r["chatId"]
    task = client.get(f"/api/tasks/{chat}").json()["task"]
    brief = task["problem"]
    # Appended to the player's message, in the block a game's chat panel strips.
    words, hidden = brief.split("## The player's message", 1)[1].split("<dex-context>", 1)
    assert words.strip() == "this sentence is clumsy" and hidden.rstrip().endswith("</dex-context>")
    assert "instantiation of the lesson's state so far" in hidden
    assert "Beat 5 of 29: `assign`" in hidden and "Now assign each point: p2 through p5 join c1." in hidden
    assert "`assigned`" in hidden and "x 1.50 · z -2.50" in hidden
    # The whole snapshot, story JSON included, is a file in the chat's directory.
    rel = re.search(r"`(context/snapshot-\d+\.json)`", hidden).group(1)
    saved = json.loads((Path(task["outputDir"]) / rel).read_text())
    assert saved["storyJson"]["id"] == "kmeans-medium" and saved["beat"]["vars"] == {"inertia0": 24.5}
    # The thread records only what the player typed.
    thread = client.get(f"/api/threads/{t}").json()
    texts = [m["text"] for m in (thread.get("messages") or thread.get("thread", {}).get("messages", []))]
    assert "this sentence is clumsy" in texts and not any("dex-context" in (x or "") for x in texts)
    # A follow-up carries what is on screen then, also appended for the agent only.
    wait_state(client, chat, {"succeeded"})
    moved = {**SNAPSHOT, "beat": {**SNAPSHOT["beat"], "index": 9, "beat": "update"}}
    client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH, json={
        "text": "and this one", "chatId": chat, "context": {"story": moved}})
    follow = wait_state(client, chat, {"succeeded", "running", "queued"})
    latest = client.get(f"/api/tasks/{follow['chat']['taskId']}").json()["task"]
    assert "and this one" in latest["problem"] and "Beat 10 of 29: `update`" in latest["problem"]


def test_a_huge_snapshot_is_refused(client):
    big = {**SNAPSHOT, "storyJson": {"blob": "x" * 4_100_000}}
    r = client.post(f"/api/game-chat/ml/threads/{client.thread_id}/messages", headers=AUTH,
                    json={"text": "hi", "context": {"story": big}})
    assert r.status_code == 422


def test_the_e2e_switch_off_reaches_the_chat_its_follow_ups_and_its_pull(client):
    t = client.thread_id
    (client.game.parent / "tools").mkdir(exist_ok=True)
    (client.game.parent / "tools" / "pull_dex_assets.py").write_text("")
    r = client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH,
                    json={"text": "rephrase it", "context": {"e2e": False}}).json()
    task = client.get(f"/api/tasks/{r['chatId']}").json()["task"]
    assert task["options"] == {"e2e": False}
    brief = task["problem"]
    assert "End-to-end tests are off" in brief.split("## The player's message", 1)[1]  # in the hidden block
    assert "--allow-untested --only <task-dir>" in brief
    wait_state(client, r["chatId"], {"succeeded"})
    client.post(f"/api/game-chat/ml/threads/{t}/messages", headers=AUTH,
                json={"text": "and run them now", "chatId": r["chatId"], "context": {"e2e": True}})
    follow = wait_state(client, r["chatId"], {"succeeded", "running", "queued"})
    latest = client.get(f"/api/tasks/{follow['chat']['taskId']}").json()["task"]
    assert latest["attempt"] == 2 and latest["options"] == {"e2e": True}
