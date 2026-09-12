"""A thin client for driving the server before there is a UI.

    dex-cli health
    dex-cli plan "two sum and LRU cache"      # propose a split, then confirm
    dex-cli submit "Two Sum: given nums..."   # skip planning, queue one task
    dex-cli tasks
    dex-cli watch [task_id]                   # follow the event stream
    dex-cli answer <task_id> <question_id> "Queue and wait"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = os.environ.get("DEX_URL", "http://127.0.0.1:4317")
TOKEN = os.environ.get("DEX_TOKEN", "")

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
COLORS = {"error": "\033[31m", "question": "\033[33m", "approval": "\033[33m",
          "asset": "\033[32m", "result": "\033[36m", "task_state": "\033[35m"}


def call(path: str, payload: dict[str, Any] | None = None, **params: Any) -> Any:
    url = f"{BASE}{path}" + (f"?{urlencode(params)}" if params else "")
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, method="POST" if payload is not None else "GET")
    request.add_header("content-type", "application/json")
    if TOKEN:
        request.add_header("x-dex-token", TOKEN)
    try:
        with urlopen(request) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        sys.exit(f"{exc.code} {exc.reason}: {exc.read().decode()[:400]}")
    except URLError as exc:
        sys.exit(f"cannot reach dex at {BASE}: {exc.reason}\nIs the server running? (dex)")


def watch(task_id: str | None) -> None:
    params = {"t": TOKEN} if TOKEN else {}
    if task_id:
        params["task"] = task_id
    url = f"{BASE}/api/events" + (f"?{urlencode(params)}" if params else "")
    request = Request(url, headers={"accept": "text/event-stream"})
    if TOKEN:
        request.add_header("x-dex-token", TOKEN)
    print(f"{DIM}watching {url}{RESET}")
    with urlopen(request) as stream:
        for raw in stream:
            line = raw.decode(errors="replace").rstrip("\n")
            if not line.startswith("data: "):
                continue
            render(json.loads(line[6:]))


def render(event: dict[str, Any]) -> None:
    kind = event.get("type", "?")
    color = COLORS.get(kind, "")
    slug = (event.get("task") or {}).get("slug") or (event.get("taskId") or "")[:6]
    head = f"{color}{kind:<16}{RESET}{DIM}{slug}{RESET}"

    match kind:
        case "text" | "thinking":
            print(f"{head} {event.get('text', '').strip()[:600]}")
        case "tool":
            print(f"{head} {event.get('title')}")
        case "tool_result":
            print(f"{head} {'ok' if event.get('ok') else 'FAILED'} {DIM}{event.get('output','')[:160]}{RESET}")
        case "question":
            print(f"{head} {BOLD}{event.get('question')}{RESET}")
            for option in event.get("options") or []:
                print(f"                 · {option}")
            print(f"{DIM}   answer with: dex-cli answer {event.get('taskId')} {event.get('id')} \"...\"{RESET}")
        case "approval":
            print(f"{head} {BOLD}{event.get('title')}{RESET}")
            print(f"{DIM}   approve with: dex-cli approve {event.get('taskId')} {event.get('id')} allow{RESET}")
        case "asset":
            print(f"{head} {event.get('path')} {DIM}({event.get('kind')}, {event.get('bytes')} bytes){RESET}")
        case "result":
            print(f"{head} {event.get('turns')} turns, ${event.get('costUsd') or 0:.4f}")
        case _:
            payload = {k: v for k, v in event.items() if k not in {"seq", "ts", "type", "taskId"}}
            print(f"{head} {json.dumps(payload)[:400]}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="dex-cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("tasks")
    p_plan = sub.add_parser("plan"); p_plan.add_argument("message")
    p_plan.add_argument("-y", "--yes", action="store_true", help="enqueue without confirming")
    p_submit = sub.add_parser("submit"); p_submit.add_argument("problem"); p_submit.add_argument("--title")
    p_watch = sub.add_parser("watch"); p_watch.add_argument("task_id", nargs="?")
    p_answer = sub.add_parser("answer")
    p_answer.add_argument("task_id"); p_answer.add_argument("question_id"); p_answer.add_argument("answer")
    p_approve = sub.add_parser("approve")
    p_approve.add_argument("task_id"); p_approve.add_argument("approval_id")
    p_approve.add_argument("decision", choices=["allow", "deny"])
    p_cancel = sub.add_parser("cancel"); p_cancel.add_argument("task_id")
    args = parser.parse_args()

    match args.command:
        case "health":
            print(json.dumps(call("/api/health"), indent=2))
        case "tasks":
            for task in call("/api/tasks")["tasks"]:
                print(f"{task['state']:<15} {task['slug']:<28} {task['activity'] or ''}")
        case "submit":
            task = call("/api/tasks", {"problem": args.problem, "title": args.title})["task"]
            print(f"queued {task['slug']} ({task['id']}) → {task['outputDir']}")
        case "plan":
            plan = call("/api/chat/plan", {"message": args.message})["plan"]
            if plan["needsClarification"]:
                sys.exit(f"dex needs more detail: {plan['needsClarification']}")
            for task in plan["tasks"]:
                print(f"  {BOLD}{task['title']}{RESET} → {task['slug']}\n    {DIM}{task['problem'][:200]}{RESET}")
            if plan["notes"]:
                print(f"{DIM}{plan['notes']}{RESET}")
            if not args.yes and input(f"\nrun these {len(plan['tasks'])} task(s) in parallel? [y/N] ").lower() != "y":
                sys.exit("nothing queued")
            for task in call("/api/chat/confirm", {"tasks": plan["tasks"]})["tasks"]:
                print(f"queued {task['slug']} ({task['id']})")
        case "watch":
            try:
                watch(args.task_id)
            except KeyboardInterrupt:
                pass
        case "answer":
            print(call(f"/api/tasks/{args.task_id}/answer", {"id": args.question_id, "answer": args.answer}))
        case "approve":
            print(call(f"/api/tasks/{args.task_id}/approve", {"id": args.approval_id, "decision": args.decision}))
        case "cancel":
            print(call(f"/api/tasks/{args.task_id}/cancel", {}))


if __name__ == "__main__":
    main()
