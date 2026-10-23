#!/usr/bin/env python3
"""Set up a game's in-game chat with dex (see design/16_game_chat.md).

For one project it finds or creates the project's "Chats" thread, makes a
chat token (or keeps the one it has), records both with the game's folder in
dex's `.dex/game-chat.json`, and writes the game's `.env.local` so its dev
server and builds talk to dex:

    .venv/bin/python scripts/game_chat_setup.py --project games \\
        --game "Many Builders" --root ~/games/many-builders
    .venv/bin/python scripts/game_chat_setup.py --project ml \\
        --game "Model Workshop" --root ~/games/model-workshop

`--state` is the running server's state directory (default: this checkout's
`.dex`; for the server in ~/dex, pass ~/dex/.dex). `--rotate` makes a new
token. It only inserts a thread when the project has no Chats thread; it
changes no other row.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

import asyncio
import asyncpg

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("DEX_DATABASE_URL", "postgresql://127.0.0.1/dex")
TITLE = "Chats"


async def chats_thread(project: str) -> str:
    conn = await asyncpg.connect(DSN)
    try:
        if not await conn.fetchval("SELECT 1 FROM projects WHERE slug = $1", project):
            sys.exit(f"no project {project!r} in {DSN}")
        found = await conn.fetchval(
            "SELECT id FROM threads WHERE project = $1 AND title = $2 AND kind = 'chat' ORDER BY created_at LIMIT 1",
            project, TITLE,
        )
        if found:
            return found
        tid = uuid.uuid4().hex[:12]
        now = time.time()
        await conn.execute(
            """INSERT INTO threads (id, title, created_at, updated_at, project, kind)
               VALUES ($1, $2, to_timestamp($3), to_timestamp($3), $4, 'chat')""",
            tid, TITLE, now, project,
        )
        return tid
    finally:
        await conn.close()


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    keep = [ln for ln in lines if ln.split("=", 1)[0].strip() not in values]
    path.write_text("\n".join([*keep, *(f"{k}={v}" for k, v in values.items())]) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--game", required=True, help="the game's name, as the chat brief says it")
    ap.add_argument("--root", required=True, help="the game's folder")
    ap.add_argument("--state", default=str(ROOT / ".dex"), help="the dex server's state directory")
    ap.add_argument("--env-file", help="default: <root>/.env.local")
    ap.add_argument("--dex-url", help="VITE_DEX_URL for the game (default: port 4317 on the page's own host)")
    ap.add_argument("--rotate", action="store_true", help="make a new token")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not (root / "package.json").exists():
        sys.exit(f"{root} does not look like a game folder (no package.json)")
    state = Path(args.state).expanduser()
    state.mkdir(parents=True, exist_ok=True)
    path = state / "game-chat.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    projects = data.setdefault("projects", {})
    entry = projects.get(args.project, {})
    thread = asyncio.run(chats_thread(args.project))
    token = entry.get("token") if entry.get("token") and not args.rotate else secrets.token_urlsafe(24)
    projects[args.project] = {"game": args.game, "root": str(root), "token": token, "threadId": thread}
    path.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(path, 0o600)

    env = {"VITE_DEX_CHAT_TOKEN": token, "VITE_DEX_CHAT_THREAD": thread}
    if args.dex_url:
        env["VITE_DEX_URL"] = args.dex_url
    env_file = Path(args.env_file).expanduser() if args.env_file else root / ".env.local"
    write_env(env_file, env)
    print(json.dumps({"project": args.project, "threadId": thread, "settings": str(path), "env": str(env_file)}))


if __name__ == "__main__":
    main()
