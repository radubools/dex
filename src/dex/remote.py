"""The remote bridge: tasks submitted from the dex-remote web page, run here.

Nothing in the cloud runs code. The page (remote/web) is static files in a
bucket; it signs people in with Google through Firebase Auth and, for someone
granted a project, publishes the request straight to the Pub/Sub topic with
their Google token, the project as an attribute and their Firebase ID token in
the message. Attachments go to the files bucket first. Firestore is read-only
to people: only this module writes it.

This module is the other end, inside the dex server. For each project whose
Settings switch is on (`remote:<slug>`), it pulls that project's subscription,
checks the sender's ID token and then dex's own users and grants (the source
of truth: they decide who may publish, through the topic's IAM policy, and who
sees what, through `access/<email>`), queues an ordinary local task, records
the request as accepted or rejected, and acks. While the task runs, and after, it
mirrors the task to Firestore as one-liners -- "Reading foo.py", "Running
the tests", the closing summary -- never the agent's prose, thinking or code,
and uploads its package's files to the bucket, marking each one available once
it is there. The page polls Firestore every 15 seconds; nothing streams.

Configuration is one file, `~/.config/dex/remote.json` (or `DEX_REMOTE_CONFIG`),
written by `remote/terraform` (see remote/README.md):

    {"gcpProject": "dex-remote", "bucket": "dex-remote-files",
     "topic": "dex-tasks", "credentials": "~/.config/dex/remote-sa.json"}

No file, no bridge: the Settings switch says so and does nothing.

Google APIs are called over REST with httpx; the service-account token is
signed here with `cryptography`, so no Google client library is needed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .db import Database
from .store import SettingsStore, ThreadStore

log = logging.getLogger("dex.remote")

#: How often the bridge pulls, mirrors and uploads. The page polls on the same
#: cadence, so anything faster would only be seen 15 seconds later anyway.
TICK_S = 15.0
#: How often who-may-see-what is pushed to Firestore.
ACCESS_SYNC_S = 120.0
#: How often each served project's `machines.<name>.seen` is refreshed.
SEEN_S = 60.0


#: Files the bridge will not upload: too big, or too many for one package.
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_FILES = 1000
#: Directories that are never part of what a package hands back.
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", ".git", ".cues"}
#: A file modified this recently may still be being written.
SETTLE_S = 2.0

#: Events per task written per tick, so one chatty run cannot stall the rest.
EVENTS_PER_TICK = 200
#: The one event type whose text is a paragraph: the closing summary.
SUMMARY_CHARS = 6000
LINE_CHARS = 200

TERMINAL = {"succeeded", "failed", "cancelled", "archived"}

DEFAULT_CONFIG = Path("~/.config/dex/remote.json")


def setting_key(project: str) -> str:
    """The Settings switch for one project, e.g. `remote:ml`."""
    return f"remote:{project}"


def machine_id() -> str:
    """This machine's name as a Firestore map key: letters, digits, `_`."""
    host = socket.gethostname().split(".")[0] or "dex"
    return re.sub(r"[^A-Za-z0-9_]", "_", host).lower()


def subscription_for(project: str) -> str:
    """One subscription per project, shared by every machine that serves it:
    Pub/Sub hands each request to one of them."""
    return f"dex-tasks-{re.sub(r'[^A-Za-z0-9_.~+%-]', '-', project)}"


# ------------------------------------------------------------------ summaries


def _line(text: Any, limit: int = LINE_CHARS) -> str:
    first = " ".join(str(text or "").split())
    return first if len(first) <= limit else first[: limit - 1] + "…"


def _name(data: dict[str, Any]) -> str:
    path = data.get("file_path") or data.get("path") or data.get("notebook_path") or ""
    return Path(str(path)).name if path else ""


#: Bash programs, said as what they are for rather than what was typed.
_PROGRAMS = {
    "pytest": "Running tests", "npm": "Running npm", "npx": "Running a Node tool",
    "node": "Running a Node script", "git": "Using git", "ls": "Listing files",
    "cat": "Reading a file", "head": "Reading a file", "tail": "Reading a file",
    "sed": "Reading or editing a file", "grep": "Searching files", "rg": "Searching files",
    "find": "Finding files", "mkdir": "Creating a directory", "cp": "Copying files",
    "mv": "Moving files", "rm": "Removing files", "curl": "Fetching a URL",
    "ffmpeg": "Processing media", "manim": "Rendering an animation",
    "pip": "Installing packages", "uv": "Managing Python packages",
    "make": "Building", "tsc": "Type-checking", "playwright": "Running browser tests",
}


def _bash(data: dict[str, Any]) -> str:
    """A shell step in a few words: the agent's own description when it gave
    one, else the program it ran -- never the command line itself."""
    if data.get("description"):
        return _line(data["description"], 120)
    command = str(data.get("command") or "")
    # The first real program: past `cd x &&`, `env`, and `NAME=value` prefixes.
    words = [w for w in re.split(r"[\s;&|()]+", command) if w and not re.match(r"^\w+=", w)]
    skip = {"cd", "env", "sudo", "time", "timeout", "exec", "nohup"}
    for i, word in enumerate(words):
        program = Path(word).name
        if program in skip or (i > 0 and Path(words[i - 1]).name == "cd"):
            continue
        if program.startswith("python"):
            rest = words[i + 1:]
            if len(rest) >= 2 and rest[0] == "-m":
                return _PROGRAMS.get(rest[1], f"Running {rest[1]}"[:60])
            script = next((Path(w).name for w in rest if w.endswith(".py")), "")
            return f"Running {script}" if script else "Running Python"
        return _PROGRAMS.get(program, f"Running {program}"[:60])
    return "Running a command"


def tool_line(name: str, data: dict[str, Any]) -> str:
    """One step, as the remote page shows it: "Reading utils.py"."""
    tool = name.split("__")[-1] if name.startswith("mcp__") else name
    stem = _name(data)
    match tool:
        case "Read":
            return f"Reading {stem}" if stem else "Reading a file"
        case "Write":
            return f"Writing {stem}" if stem else "Writing a file"
        case "Edit" | "MultiEdit" | "NotebookEdit":
            return f"Editing {stem}" if stem else "Editing a file"
        case "Bash" | "shell" | "exec_command" | "run_command":
            return _bash(data)
        case "Glob" | "Grep" | "LS":
            return "Searching files"
        case "WebFetch":
            url = str(data.get("url") or "")
            host = url.split("://", 1)[-1].split("/", 1)[0]
            return f"Fetching {host}" if host else "Fetching a page"
        case "WebSearch":
            return "Searching the web"
        case "TodoWrite":
            return "Updating its plan"
        case "Task" | "Agent":
            return "Delegating a subtask"
        case "Skill":
            return _line(f"Using the {data.get('skill') or data.get('name') or ''} skill".replace("the  ", "the "), 80)
        case "ToolSearch":
            return "Loading tools"
        case "ask_user":
            return "Asking the operator a question"
        case "start_task" | "start_power_task":
            return "Starting a sub-task"
        case "continue_task":
            return "Asking another task for a change"
        case "wait_for_tasks":
            return "Waiting on other tasks"
        case "task_status":
            return "Checking other tasks"
        case "render_manim":
            return "Rendering an animation"
        case _:
            return _line(f"Using {tool.replace('_', ' ')}", 60)


_STATES = {
    "queued": "Queued",
    "running": "Running",
    "awaiting_input": "Waiting for the operator",
    "paused": "Paused; it will continue",
    "blocked": "Waiting on other tasks",
    "succeeded": "Succeeded",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "archived": "Archived",
    "todo": "Waiting for dex to be idle",
}


def summarize(type_: str, data: dict[str, Any], last_state: str | None = None) -> tuple[str, str] | None:
    """(kind, text) for one local event, or None if the page does not get it.

    What goes out is deliberately thin: state changes, one line per tool
    step, errors, and the closing summary. Prose, thinking, diffs, inputs and
    outputs stay here.
    """
    if type_ == "task_state":
        state = str(data.get("state") or "")
        if not state or state == last_state:
            return None
        return "state", _STATES.get(state, state.replace("_", " ").capitalize())
    if type_ == "tool":
        tool_input = dict(data.get("input") or {})
        name = str(data.get("name") or "")
        # Some runners leave the input out of the event; dex's own title for a
        # file step ("Write solutions.py") still names the file.
        title = str(data.get("title") or "")
        if name in ("Read", "Write", "Edit", "MultiEdit") and not _name(tool_input) and " " in title:
            tool_input["file_path"] = title.split(" ", 1)[1]
        return "step", tool_line(name, tool_input)
    if type_ == "result":
        summary = str(data.get("summary") or "").strip()
        if not summary:
            return None
        if len(summary) > SUMMARY_CHARS:
            summary = summary[: SUMMARY_CHARS - 1] + "…"
        return "summary", summary
    if type_ == "error":
        return "error", _line(data.get("message") or data.get("error") or "error")
    if type_ == "question":
        return "question", _line(f"Asked: {data.get('question') or ''}", 300)
    if type_ == "question_answered":
        how = "dex answered" if data.get("auto") else "Answered"
        return "answer", _line(f"{how}: {data.get('answer') or ''}", 300)
    if type_ == "approval" and not data.get("auto"):
        return "wait", "Waiting for the operator to approve a step"
    return None


MIRRORED_TYPES = ["task_state", "tool", "result", "error", "question", "question_answered", "approval"]


# ---------------------------------------------------------------- credentials


@dataclass
class RemoteConfig:
    gcp_project: str
    bucket: str
    topic: str
    credentials: dict[str, Any]
    database: str = "(default)"

    @classmethod
    def load(cls, path: Path | None = None) -> RemoteConfig | None:
        path = (path or Path(os.environ.get("DEX_REMOTE_CONFIG") or DEFAULT_CONFIG)).expanduser()
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        creds_path = Path(str(raw.get("credentials") or "~/.config/dex/remote-sa.json")).expanduser()
        if not creds_path.is_absolute():
            creds_path = path.parent / creds_path
        credentials = json.loads(creds_path.read_text(encoding="utf-8"))
        return cls(
            gcp_project=raw.get("gcpProject") or credentials.get("project_id") or "",
            bucket=raw["bucket"],
            topic=raw.get("topic") or "dex-tasks",
            credentials=credentials,
            database=raw.get("database") or "(default)",
        )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Credentials:
    """An access token for the cloud-platform scope, from a service-account key
    or an `authorized_user` file (`gcloud auth application-default login`)."""

    SCOPE = "https://www.googleapis.com/auth/cloud-platform"

    def __init__(self, info: dict[str, Any], http: httpx.AsyncClient) -> None:
        self.info = info
        self.http = http
        self._token = ""
        self._expires = 0.0
        self._lock = asyncio.Lock()

    def _assertion(self) -> str:
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import padding  # noqa: PLC0415

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT", "kid": self.info.get("private_key_id", "")}
        claims = {
            "iss": self.info["client_email"], "scope": self.SCOPE,
            "aud": self.info.get("token_uri") or "https://oauth2.googleapis.com/token",
            "iat": now, "exp": now + 3600,
        }
        signing = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
        key = serialization.load_pem_private_key(self.info["private_key"].encode(), password=None)
        signature = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr,call-arg]
        return f"{signing}.{_b64url(signature)}"

    async def token(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires - 120:
                return self._token
            if self.info.get("type") == "service_account":
                form = {
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": self._assertion(),
                }
                url = self.info.get("token_uri") or "https://oauth2.googleapis.com/token"
            else:
                form = {
                    "grant_type": "refresh_token",
                    "client_id": self.info["client_id"],
                    "client_secret": self.info["client_secret"],
                    "refresh_token": self.info["refresh_token"],
                }
                url = "https://oauth2.googleapis.com/token"
            reply = await self.http.post(url, data=form)
            reply.raise_for_status()
            body = reply.json()
            self._token = body["access_token"]
            self._expires = time.time() + float(body.get("expires_in") or 3600)
            return self._token


def _rfc3339(value: Any) -> float | None:
    """Seconds since the epoch for a Pub/Sub or Firestore timestamp."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    # Python reads at most microseconds; Google sends up to nanoseconds.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


class TokenVerifier:
    """Checks a Firebase Auth ID token: Google's signature, this project as its
    audience and issuer, a verified email, and validity at a given moment."""

    CERTS = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
    #: Clock skew allowed either side of the token's validity window.
    LEEWAY_S = 300

    def __init__(self, project: str, http: httpx.AsyncClient) -> None:
        self.project = project
        self.http = http
        self._certs: dict[str, Any] = {}
        self._fetched = 0.0

    async def _key(self, kid: str) -> Any:
        if kid not in self._certs or time.time() - self._fetched > 3600:
            from cryptography import x509  # noqa: PLC0415

            reply = await self.http.get(self.CERTS)
            reply.raise_for_status()
            # Keep keys seen before: a request can wait in the queue past a
            # rotation, and a key Google signed with is still Google's.
            for key_id, pem in reply.json().items():
                self._certs[key_id] = x509.load_pem_x509_certificate(pem.encode()).public_key()
            self._fetched = time.time()
        if kid not in self._certs:
            raise ValueError("signed with a key Google no longer publishes")
        return self._certs[kid]

    async def verify(self, token: str, at: float) -> dict[str, Any]:
        from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
        from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import padding  # noqa: PLC0415

        def part(raw: str) -> bytes:
            return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

        try:
            head_raw, body_raw, sig_raw = token.split(".")
            header = json.loads(part(head_raw))
            claims = json.loads(part(body_raw))
            signature = part(sig_raw)
        except ValueError as exc:
            raise ValueError("no sign-in token") from exc
        if header.get("alg") != "RS256":
            raise ValueError("unexpected token algorithm")
        key = await self._key(str(header.get("kid") or ""))
        try:
            key.verify(signature, f"{head_raw}.{body_raw}".encode(), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as exc:
            raise ValueError("bad token signature") from exc
        if claims.get("aud") != self.project or claims.get("iss") != f"https://securetoken.google.com/{self.project}":
            raise ValueError("token is for another project")
        if not claims.get("email") or claims.get("email_verified") is not True:
            raise ValueError("token has no verified email")
        if not (float(claims.get("iat", 0)) - self.LEEWAY_S <= at <= float(claims.get("exp", 0)) + self.LEEWAY_S):
            raise ValueError("token had expired when the request was sent")
        return claims


# ------------------------------------------------------------------ Firestore


def to_value(value: Any) -> dict[str, Any]:
    """A Python value as a Firestore REST `Value`."""
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, datetime):
        return {"timestampValue": value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {str(k): to_value(v) for k, v in value.items()}}}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [to_value(v) for v in value]}}
    return {"stringValue": str(value)}


def from_value(value: dict[str, Any]) -> Any:
    """A Firestore REST `Value` as plain Python (timestamps stay strings)."""
    (kind, inner), = value.items() if value else (("nullValue", None),)
    match kind:
        case "nullValue":
            return None
        case "booleanValue":
            return bool(inner)
        case "integerValue":
            return int(inner)
        case "doubleValue":
            return float(inner)
        case "mapValue":
            return {k: from_value(v) for k, v in (inner.get("fields") or {}).items()}
        case "arrayValue":
            return [from_value(v) for v in inner.get("values") or []]
        case _:
            return inner


def field_path(*parts: str) -> str:
    """A Firestore field path, back-quoting any segment that is not a plain
    identifier (project slugs have dashes, machine names may have digits first)."""
    out = []
    for part in parts:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            out.append(part)
        else:
            out.append("`" + part.replace("\\", "\\\\").replace("`", "\\`") + "`")
    return ".".join(out)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Google:
    """The three Google APIs the bridge uses, over REST."""

    def __init__(self, config: RemoteConfig, http: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))
        self.creds = Credentials(config.credentials, self.http)
        self.root = f"projects/{config.gcp_project}/databases/{config.database}/documents"

    async def close(self) -> None:
        await self.http.aclose()

    async def _call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self.creds.token()}", **kwargs.pop("headers", {})}
        return await self.http.request(method, url, headers=headers, **kwargs)

    # Firestore -----------------------------------------------------------

    def doc_name(self, path: str) -> str:
        return path if path.startswith("projects/") and "/documents/" in path else f"{self.root}/{path}"

    async def get_doc(self, path: str) -> dict[str, Any] | None:
        reply = await self._call("GET", f"https://firestore.googleapis.com/v1/{self.doc_name(path)}")
        if reply.status_code == 404:
            return None
        reply.raise_for_status()
        return {k: from_value(v) for k, v in (reply.json().get("fields") or {}).items()}

    def update(self, path: str, fields: dict[str, Any], mask: list[str] | None = None) -> dict[str, Any]:
        """One write for `commit`: set `fields`, or with `mask` only those paths
        (a masked path with no value is deleted)."""
        write: dict[str, Any] = {
            "update": {"name": self.doc_name(path), "fields": {k: to_value(v) for k, v in fields.items()}}
        }
        if mask is not None:
            write["updateMask"] = {"fieldPaths": mask}
        return write

    async def commit(self, writes: list[dict[str, Any]]) -> None:
        for start in range(0, len(writes), 400):
            reply = await self._call(
                "POST",
                f"https://firestore.googleapis.com/v1/projects/{self.config.gcp_project}"
                f"/databases/{self.config.database}/documents:commit",
                json={"writes": writes[start:start + 400]},
            )
            if reply.is_error:
                raise RuntimeError(f"firestore commit: {reply.status_code} {reply.text[:300]}")

    async def list_docs(self, collection: str) -> dict[str, dict[str, Any]]:
        """Every document in a top-level collection: id -> fields."""
        out: dict[str, dict[str, Any]] = {}
        token = ""
        while True:
            params = {"pageSize": 300, **({"pageToken": token} if token else {})}
            reply = await self._call("GET", f"https://firestore.googleapis.com/v1/{self.root}/{collection}", params=params)
            reply.raise_for_status()
            body = reply.json()
            for document in body.get("documents") or []:
                out[document["name"].rsplit("/", 1)[-1]] = {
                    k: from_value(v) for k, v in (document.get("fields") or {}).items()
                }
            token = body.get("nextPageToken") or ""
            if not token:
                return out

    # Pub/Sub IAM -----------------------------------------------------------

    async def topic_publishers(self, emails: set[str]) -> list[str]:
        """Make the topic's publishers exactly these people (plus any member
        that is not a person: a service account or group granted by hand).
        Returns the emails Google refused, e.g. ones with no Google account."""
        topic = f"https://pubsub.googleapis.com/v1/projects/{self.config.gcp_project}/topics/{self.config.topic}"
        reply = await self._call("GET", f"{topic}:getIamPolicy")
        reply.raise_for_status()
        policy = reply.json()
        role = "roles/pubsub.publisher"
        bindings = [b for b in policy.get("bindings") or [] if b.get("role") != role]
        current = next((b for b in policy.get("bindings") or [] if b.get("role") == role), {"members": []})
        others = [m for m in current.get("members") or [] if not m.startswith("user:")]
        refused: list[str] = []
        wanted = sorted(emails)
        while True:
            members = others + [f"user:{e}" for e in wanted if e not in refused]
            new = {**policy, "bindings": bindings + ([{"role": role, "members": members}] if members else [])}
            if sorted(current.get("members") or []) == sorted(members):
                return refused
            reply = await self._call("POST", f"{topic}:setIamPolicy", json={"policy": new})
            if not reply.is_error:
                return refused
            # "User a@b.c does not exist." (or "user:a@b.c ...") for an email with no Google account.
            bad = re.search(r"(?:[Uu]ser:?\s?)([^\s'\"]+@[^\s'\"]+?)\.? does not exist", reply.text)
            if reply.status_code != 400 or not bad or bad.group(1) in refused:
                raise RuntimeError(f"pubsub setIamPolicy: {reply.status_code} {reply.text[:300]}")
            refused.append(bad.group(1))

    # Pub/Sub -------------------------------------------------------------

    def subscription_path(self, project: str) -> str:
        return f"projects/{self.config.gcp_project}/subscriptions/{subscription_for(project)}"

    async def ensure_subscription(self, project: str) -> None:
        """The project's subscription, made on first use with a filter so it
        only ever holds that project's requests."""
        url = f"https://pubsub.googleapis.com/v1/{self.subscription_path(project)}"
        reply = await self._call("GET", url)
        if reply.status_code == 200:
            return
        body = {
            "topic": f"projects/{self.config.gcp_project}/topics/{self.config.topic}",
            "filter": f'attributes.project = "{project}"',
            "ackDeadlineSeconds": 120,
            "messageRetentionDuration": "604800s",
            "expirationPolicy": {},  # never expires while no machine polls
        }
        reply = await self._call("PUT", url, json=body)
        if reply.is_error and reply.status_code != 409:
            raise RuntimeError(f"pubsub subscription: {reply.status_code} {reply.text[:300]}")

    async def pull(self, project: str, limit: int = 10) -> list[dict[str, Any]]:
        reply = await self._call(
            "POST", f"https://pubsub.googleapis.com/v1/{self.subscription_path(project)}:pull",
            json={"maxMessages": limit, "returnImmediately": True},
        )
        reply.raise_for_status()
        return reply.json().get("receivedMessages") or []

    async def ack(self, project: str, ack_ids: list[str]) -> None:
        if not ack_ids:
            return
        reply = await self._call(
            "POST", f"https://pubsub.googleapis.com/v1/{self.subscription_path(project)}:acknowledge",
            json={"ackIds": ack_ids},
        )
        reply.raise_for_status()

    # Storage -------------------------------------------------------------

    async def upload(self, name: str, path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        async def chunks():  # noqa: ANN202
            with path.open("rb") as handle:
                while block := handle.read(1 << 20):
                    yield block

        reply = await self._call(
            "POST",
            f"https://storage.googleapis.com/upload/storage/v1/b/{self.config.bucket}/o",
            params={"uploadType": "media", "name": name},
            headers={"Content-Type": content_type, "Content-Length": str(path.stat().st_size)},
            content=chunks(),
        )
        if reply.is_error:
            raise RuntimeError(f"upload {name}: {reply.status_code} {reply.text[:300]}")

    async def download(self, name: str, limit: int = 100 * 1024 * 1024) -> bytes:
        reply = await self._call(
            "GET", f"https://storage.googleapis.com/storage/v1/b/{self.config.bucket}/o/{quote(name, safe='')}",
            params={"alt": "media"},
        )
        if reply.status_code == 404:
            return b""
        if reply.is_error:
            raise RuntimeError(f"download {name}: {reply.status_code}")
        return reply.content[:limit]

    async def delete_object(self, name: str) -> None:
        reply = await self._call(
            "DELETE", f"https://storage.googleapis.com/storage/v1/b/{self.config.bucket}/o/{quote(name, safe='')}"
        )
        if reply.is_error and reply.status_code != 404:
            raise RuntimeError(f"delete {name}: {reply.status_code}")


# --------------------------------------------------------------------- files


def scan_files(root: Path, now: float | None = None) -> dict[str, tuple[int, int]]:
    """Every file a package hands back: relative path -> (size, mtime_ns).
    Hidden and tool directories are skipped, and so is anything still being
    written or too big to send."""
    now = time.time() if now is None else now
    found: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            if filename.startswith(".") or filename.endswith((".tmp", ".pyc")):
                continue
            path = Path(dirpath) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > MAX_FILE_BYTES or now - stat.st_mtime < SETTLE_S:
                continue
            found[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
            if len(found) >= MAX_FILES:
                return found
    return found


# -------------------------------------------------------------------- bridge


@dataclass
class BridgeStatus:
    configured: bool = False
    error: str | None = None
    last_poll: float | None = None
    machine: str = field(default_factory=machine_id)
    accepted: int = 0
    #: Granted emails Google would not make publishers (no Google account).
    refused: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "configured": self.configured, "error": self.error,
            "lastPoll": self.last_poll, "machine": self.machine, "accepted": self.accepted,
            "refused": self.refused,
        }


class RemoteBridge:
    """Pulls the remote queue for the projects switched on, and mirrors the
    tasks it started back to Firestore and the bucket."""

    def __init__(self, config: Any, db: Database, tasks: Any, threads: ThreadStore,
                 remote: RemoteConfig | None = None, google: Google | None = None) -> None:
        self.config = config
        self.db = db
        self.tasks = tasks
        self.threads = threads
        self.settings = SettingsStore(db)
        self.remote = remote
        self.google = google
        self.status = BridgeStatus()
        self._runtime: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._last_access = 0.0
        self.verifier: Any = None
        self._seen: dict[str, float] = {}

    # lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        if self.remote is None:
            try:
                self.remote = RemoteConfig.load()
            except Exception as exc:  # noqa: BLE001 - a bad file must not stop the server
                self.status.error = f"remote config: {exc}"
                log.warning("remote bridge off: %s", exc)
        if self.remote is None:
            return
        self.status.configured = True
        self.google = self.google or Google(self.remote)
        self.verifier = self.verifier or TokenVerifier(self.remote.gcp_project, self.google.http)
        self._runtime = asyncio.create_task(self._loop(), name="dex-remote-bridge")

    async def stop(self) -> None:
        if self._runtime:
            self._runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runtime
            self._runtime = None
        if self.google:
            await self.google.close()

    def wake(self) -> None:
        """Run a tick now: a switch was flipped."""
        self._last_access = 0.0
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
                self.status.error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep polling; say what failed
                self.status.error = _line(str(exc) or type(exc).__name__, 300)
                log.warning("remote bridge tick failed: %s", exc, exc_info=True)
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK_S)

    # one pass ----------------------------------------------------------------

    async def enabled_projects(self) -> list[str]:
        rows = await self.db.pool.fetch(
            "SELECT key FROM settings WHERE key LIKE 'remote:%' AND (value->>'v')::boolean IS TRUE"
        )
        slugs = [row["key"].split(":", 1)[1] for row in rows]
        known = {r["slug"] for r in await self.db.pool.fetch("SELECT slug FROM projects")}
        return sorted(s for s in slugs if s in known)

    async def tick(self) -> None:
        enabled = await self.enabled_projects()
        if time.monotonic() - self._last_access > ACCESS_SYNC_S:
            await self.sync_access(enabled)
            self._last_access = time.monotonic()
        for project in enabled:
            await self.poll(project)
        await self.mirror_all()
        self.status.last_poll = time.time()

    # who may do what -----------------------------------------------------------
    #
    # The list lives in Firestore, `users/<email>`, kept by hand in the Firebase
    # console: `role` is "user" (the projects in `projects`, a map of slug to
    # true) or "admin" (every project some dex serves). Anything else, or no
    # document, is not authorised. The page checks it at sign-in; the rules
    # check it on every read; this checks it again before running anything.

    @staticmethod
    def allows(user: dict[str, Any] | None, project: str) -> bool:
        if not user:
            return False
        role = user.get("role")
        if role == "admin":
            return True
        return role == "user" and (user.get("projects") or {}).get(project) is True

    async def authorized(self, email: str, project: str) -> bool:
        assert self.google is not None
        return self.allows(await self.google.get_doc(f"users/{email.lower()}"), project)

    async def sync_access(self, enabled: list[str]) -> None:
        """Publish `projects/<slug>` for the projects this machine serves (only
        this machine's own `machines.<name>` field, so two machines do not undo
        each other), and make the topic's publishers the authorised users."""
        assert self.google is not None
        published = await self.settings.get("remote_published", {}) or {}
        owned = sorted(set(enabled) | set(published.get("projects") or []))
        host = self.status.machine
        names = {
            r["slug"]: (r["name"], r["description"])
            for r in await self.db.pool.fetch("SELECT slug, name, description FROM projects")
        }
        writes: list[dict[str, Any]] = []
        for project in owned:
            fields: dict[str, Any] = {}
            mask = [field_path("machines", host)]
            if project in enabled:
                name, description = names.get(project, (project, ""))
                fields = {"slug": project, "name": name, "description": description,
                          "machines": {host: {"seen": now_utc()}}}
                mask += ["slug", "name", "description"]
            writes.append(self.google.update(f"projects/{project}", fields, mask))
        if writes:
            await self.google.commit(writes)
        users = await self.google.list_docs("users")
        everyone = {email.lower() for email, user in users.items() if user.get("role") in ("user", "admin")}
        self.status.refused = await self.google.topic_publishers(everyone)
        await self.settings.set("remote_published", {"projects": sorted(enabled)})


    # queue -------------------------------------------------------------------

    async def poll(self, project: str) -> None:
        assert self.google is not None
        await self.google.ensure_subscription(project)
        messages = await self.google.pull(project)
        done: list[str] = []
        for message in messages:
            try:
                await self.accept(project, message)
                done.append(message["ackId"])
            except Exception as exc:  # noqa: BLE001 - left unacked, Pub/Sub redelivers
                log.warning("remote request in %s not taken: %s", project, exc, exc_info=True)
        await self.google.ack(project, done)
        # Keep the project's `seen` fresh, so the page can say whether anyone
        # serves it: once a minute, not every tick, as it is a billed write.
        if time.monotonic() - self._seen.get(project, -1e9) < SEEN_S:
            return
        self._seen[project] = time.monotonic()
        await self.google.commit([self.google.update(
            f"projects/{project}", {"machines": {self.status.machine: {"seen": now_utc()}}},
            [field_path("machines", self.status.machine)],
        )])

    async def accept(self, project: str, received: dict[str, Any]) -> None:
        """Turn one queued request into a local task. Returns normally when the
        message is dealt with (taken, rejected, or already taken), so it is
        acked; raises when it should come back.

        Pub/Sub does not say who published a message, so the page puts the
        sender's Firebase ID token in it. It must have been valid when Pub/Sub
        stamped the message (`publishTime`, which the sender cannot set): that
        proves who sent it even when no dex was polling for days. The email in
        the token is the only one believed.
        """
        assert self.google is not None and self.remote is not None
        message = received.get("message") or {}
        try:
            body = json.loads(base64.b64decode(message.get("data") or "") or b"{}")
        except ValueError:
            log.warning("remote: unreadable message %s", message.get("messageId"))
            return
        if body.get("kind") == "answer":
            await self.answer(project, message, body)
            return
        request_id = str(body.get("requestId") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
            log.warning("remote: message %s has no usable request id", message.get("messageId"))
            return
        path = f"projects/{project}/requests/{request_id}"
        if await self.db.pool.fetchval(
            "SELECT 1 FROM remote_tasks WHERE request_id = $1", f"{project}/{request_id}"
        ):
            return
        existing = await self.google.get_doc(path)
        if existing is not None and existing.get("status") in ("accepted", "rejected"):
            return
        published = _rfc3339(message.get("publishTime")) or time.time()
        stamp = now_utc()
        record = {
            "id": request_id, "project": project, "title": _line(body.get("title") or "", 200),
            "createdAt": datetime.fromtimestamp(published, timezone.utc),
            "machine": self.status.machine, "updatedAt": stamp,
        }

        async def reject(reason: str, email: str = "") -> None:
            await self.google.commit([self.google.update(
                path, {**record, "email": email, "status": "rejected", "reason": reason}
            )])
            log.info("remote: %s rejected: %s", path, reason)

        try:
            claims = await self.verifier.verify(str(body.get("idToken") or ""), at=published)
        except ValueError as exc:
            # No trustworthy email, so nobody can be told on the page except
            # through the request id they hold.
            await reject(f"The sign-in on this request could not be checked ({exc}). Sign in again and resubmit.")
            return
        email = str(claims.get("email") or "").lower()
        problem = str(body.get("problem") or "").strip()[:20000]
        title = _line(body.get("title") or "", 80) or None
        if existing is not None and existing.get("email") not in (None, email):
            await reject("That request id belongs to someone else.", email)
            return
        if not await self.authorized(email, project):
            await reject("You are not authorised for this project in dex-remote.", email)
            return
        if not problem:
            await reject("The request has no description.", email)
            return

        sources = self.config.project_datasets(project)
        attached = []
        for item in (body.get("uploads") or [])[:20]:
            name = str((item or {}).get("object") or "")
            # Only what this sender put in their own upload area for this request.
            if not name.startswith(f"projects/{project}/uploads/{claims.get('user_id') or claims.get('sub')}/{request_id}/"):
                continue
            data = await self.google.download(name)
            if data:
                from . import uploads  # noqa: PLC0415

                attached.append(uploads.store(sources / "remote" / request_id, name.rsplit("/", 1)[-1], data))
        from . import uploads  # noqa: PLC0415

        problem = uploads.with_sources(problem, attached, sources)

        thread_id = await self.thread_for(project, email)
        task = await self.tasks.submit(problem, title, None, thread_id, project=project)
        await self.db.pool.execute(
            """INSERT INTO remote_tasks (task_id, project, request_id, email)
               VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING""",
            task.id, project, f"{project}/{request_id}", email,
        )
        with contextlib.suppress(Exception):
            await self.threads.touch(thread_id)
        await self.google.commit([
            self.google.update(path, {**record, "email": email, "status": "accepted", "taskId": task.id}),
            self.google.update(f"projects/{project}/tasks/{task.id}", {
                "id": task.id, "project": project, "requestId": request_id, "email": email,
                "title": task.title, "problem": str(body.get("problem") or "")[:20000],
                "state": task.state.value, "activity": "Queued", "machine": self.status.machine,
                "createdAt": stamp, "updatedAt": stamp, "summary": None, "error": None,
                "files": [], "attachments": [u.name for u in attached],
            }),
        ])
        self.status.accepted += 1
        log.info("remote: %s from %s became task %s", path, email, task.id)

    async def answer(self, project: str, message: dict[str, Any], body: dict[str, Any]) -> None:
        """An answer to a task's question, from the page: `taskId` is the task
        the page knows (the first attempt), `questionId` the question's id."""
        assert self.google is not None
        published = _rfc3339(message.get("publishTime")) or time.time()
        root, question_id = str(body.get("taskId") or ""), str(body.get("questionId") or "")
        answer = str(body.get("answer") or "").strip()[:4000]
        try:
            claims = await self.verifier.verify(str(body.get("idToken") or ""), at=published)
        except ValueError as exc:
            log.info("remote: answer to %s refused: %s", root, exc)
            return
        email = str(claims.get("email") or "").lower()
        if not answer or not await self.authorized(email, project):
            log.info("remote: answer to %s from %s refused", root, email)
            return
        owner = await self.db.pool.fetchval(
            "SELECT project FROM remote_tasks WHERE task_id = $1", root
        )
        if owner != project:
            log.info("remote: answer names %s, which is not a remote task of %s", root, project)
            return
        # The question belongs to whichever attempt asked it, usually the latest.
        for attempt in reversed(await self.chain(root)):
            if self.tasks.answer(attempt["id"], question_id, answer):
                log.info("remote: %s answered %s's question %s", email, attempt["id"], question_id)
                return
        log.info("remote: question %s of %s is no longer open", question_id, root)

    async def thread_for(self, project: str, email: str) -> str:
        """One thread per person per project, so their tasks read together."""
        key = f"remote_thread:{project}:{email}"
        thread_id = await self.settings.get(key)
        if thread_id and await self.threads.get(thread_id):
            return str(thread_id)
        thread = await self.threads.create(f"Remote · {email}", project=project)
        await self.settings.set(key, thread.id)
        return thread.id

    # mirror ------------------------------------------------------------------

    async def mirror_all(self) -> None:
        rows = await self.db.pool.fetch(
            "SELECT task_id, project, last_seq, last_state, files, done FROM remote_tasks ORDER BY created_at"
        )
        for row in rows:
            try:
                await self.mirror(dict(row))
            except Exception as exc:  # noqa: BLE001 - one task must not stop the others
                log.warning("remote: mirroring %s failed: %s", row["task_id"], exc, exc_info=True)

    async def chain(self, root: str) -> list[dict[str, Any]]:
        """The root task and every attempt descended from it, oldest first."""
        rows = await self.db.pool.fetch(
            """WITH RECURSIVE chain AS (
                   SELECT id, state, activity, error, created_at FROM tasks WHERE id = $1
                   UNION ALL
                   SELECT t.id, t.state, t.activity, t.error, t.created_at
                     FROM tasks t JOIN chain c ON t.parent_id = c.id)
               SELECT * FROM chain ORDER BY created_at""",
            root,
        )
        return [dict(r) for r in rows]

    async def mirror(self, row: dict[str, Any]) -> None:
        assert self.google is not None
        root, project = row["task_id"], row["project"]
        chain = await self.chain(root)
        if not chain:
            return
        ids = [c["id"] for c in chain]
        events = await self.db.pool.fetch(
            """SELECT seq, type, data, extract(epoch from ts)::float8 AS ts FROM events
                WHERE task_id = ANY($1::text[]) AND seq > $2 AND type = ANY($3::text[])
                ORDER BY seq LIMIT $4""",
            ids, row["last_seq"], MIRRORED_TYPES, EVENTS_PER_TICK,
        )
        latest = chain[-1]
        state = str(latest["state"])
        if row["done"] and not events and state in TERMINAL:
            return

        base = f"projects/{project}/tasks/{root}"
        writes: list[dict[str, Any]] = []
        last_seq, last_state = row["last_seq"], row["last_state"]
        summary = error = None
        activity = None
        #: The open question, for the page to answer: set, cleared, or untouched.
        question: Any = ...
        for event in events:
            last_seq = event["seq"]
            data = Database.load(event["data"]) if not isinstance(event["data"], dict) else event["data"]
            said = summarize(event["type"], data or {}, last_state)
            if event["type"] == "task_state":
                last_state = str((data or {}).get("state") or last_state)
            elif event["type"] == "question":
                question = {
                    "id": str(data.get("id") or ""), "text": str(data.get("question") or "")[:2000],
                    "options": [str(o)[:200] for o in (data.get("options") or [])][:12],
                }
            elif event["type"] == "question_answered":
                question = None
            if said is None:
                continue
            kind, text = said
            if kind == "summary":
                summary = text
            elif kind == "error":
                error = text
            if kind in ("step", "state", "wait", "question", "answer"):
                activity = text
            writes.append(self.google.update(f"{base}/events/{event['seq']:012d}", {
                "seq": event["seq"], "kind": kind, "text": text,
                "ts": datetime.fromtimestamp(event["ts"], timezone.utc),
            }))

        fields: dict[str, Any] = {"state": state, "updatedAt": now_utc(), "attempts": len(chain)}
        mask = ["state", "updatedAt", "attempts"]
        if activity:
            fields["activity"] = activity
            mask.append("activity")
        if question is not ...:
            fields["question"] = question
            mask.append("question")
        elif state in TERMINAL:
            fields["question"] = None
            mask.append("question")
        if summary is not None:
            fields["summary"] = summary
            mask.append("summary")
        if error is not None or (state == "failed" and latest.get("error")):
            fields["error"] = error or _line(latest.get("error"))
            mask.append("error")

        files, uploaded = await self.sync_files(project, root, latest["id"], row)
        if files is not None:
            fields["files"] = files
            mask.append("files")
        # Nothing new: no write, so `updatedAt` only moves when something did
        # and the page's incremental poll costs one read.
        if events or files is not None or state != row["last_state"]:
            writes.append(self.google.update(base, fields, mask))
            await self.google.commit(writes)
            if not events and state != row["last_state"]:
                last_state = state

        finished = state in TERMINAL and len(events) < EVENTS_PER_TICK
        await self.db.pool.execute(
            """UPDATE remote_tasks SET last_seq = $2, last_state = $3, files = $4::jsonb, done = $5
                WHERE task_id = $1""",
            root, last_seq, last_state, Database.dump(uploaded), finished,
        )

    async def sync_files(self, project: str, root: str, latest_id: str,
                         row: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        """Upload what changed in the package. Returns the file list for the
        task document (None when nothing changed) and the new upload record."""
        assert self.google is not None
        task = await self.tasks.tasks.get(latest_id)
        before = row["files"]
        if not isinstance(before, dict):
            before = Database.load(before) if before else {}
        before = dict(before or {})
        if task is None or task.scope != "package":
            return None, before
        directory = task.output_dir(self.config.assets_dir)
        found = scan_files(directory)
        changed = [p for p, stat in found.items() if before.get(p) != list(stat)]
        gone = [p for p in before if p not in found]
        if not changed and not gone:
            return None, before

        prefix = f"projects/{project}/tasks/{root}/files/"

        def listing(available: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {"path": p, "size": found[p][0], "available": p in available,
                 "object": prefix + p}
                for p in sorted(found)
            ]

        # First the list with the new files pending, so the page can show them
        # arriving; then each upload; then the list with them available.
        current = {p: v for p, v in before.items() if p in found and p not in changed}
        await self.google.commit([self.google.update(
            f"projects/{project}/tasks/{root}", {"files": listing(current)}, ["files"]
        )])
        for path in gone:
            with contextlib.suppress(Exception):
                await self.google.delete_object(prefix + path)
        for path in changed:
            try:
                await self.google.upload(prefix + path, directory / path)
                current[path] = list(found[path])
            except Exception as exc:  # noqa: BLE001 - try again next tick
                log.warning("remote: upload %s failed: %s", path, exc)
        return listing(current), current
