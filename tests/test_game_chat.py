"""Game chat: a game's chat panel talks to its project's Chats thread."""

from __future__ import annotations

import asyncio
import json
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
    assert "export_story" in brief and "Add a lesson" in brief


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
