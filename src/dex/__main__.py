"""`dex` entry point: run the task server."""

from __future__ import annotations

import argparse
import logging
import socket
from pathlib import Path

import uvicorn

from .api import create_app
from .config import CONFIG, MAX_WORKERS
from .runner import credential_source, manim_available


def lan_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))  # no packets sent; just picks the route
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(prog="dex", description="Run the dex task server.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="restart the server when src/dex changes (development only — "
             "a reload kills any task that is mid-run)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s  %(message)s"
    )
    CONFIG.ensure_dirs()

    suffix = f"/#t={CONFIG.token}" if CONFIG.token else "/"
    # Deliberately not a worker count: the limit lives in the database and is
    # changed from the UI, so anything printed here would be a guess that
    # disagrees with reality the moment it is adjusted.
    print(f"\n  dex · {CONFIG.model} · up to {MAX_WORKERS} workers, set in Settings")
    print(f"  assets    {CONFIG.assets_dir}")
    print(f"  manim     {'available' if manim_available() else 'not installed (animations will be skipped)'}")
    auth = credential_source()
    if auth:
        print(f"  claude    {auth}")
    else:
        print("  claude    NO CREDENTIALS — export ANTHROPIC_API_KEY or run `claude auth login`")
    if CONFIG.token:
        print("  access    shared token required (DEX_TOKEN is set)")
    print(f"  serving   http://{lan_address()}:{CONFIG.port}{suffix}\n")

    if args.reload:
        print("  reload    on — in-flight tasks are lost on every restart\n")
        # Reload needs an import string rather than an app instance, so uvicorn
        # can rebuild the app in the child process after each change.
        uvicorn.run(
            "dex.api:create_app",
            factory=True,
            host=CONFIG.host,
            port=CONFIG.port,
            log_level="info",
            reload=True,
            reload_dirs=[str(Path(__file__).resolve().parent)],
            # Without this the reload never completes. The UI holds an SSE
            # stream open for as long as the page is, uvicorn's graceful
            # shutdown waits for open connections, and so a save under
            # `src/dex/` left the server stopped — logging "Waiting for
            # connections to close" and answering nothing — until someone
            # noticed and killed it.
            timeout_graceful_shutdown=3,
        )
    else:
        uvicorn.run(create_app(), host=CONFIG.host, port=CONFIG.port, log_level="info")


if __name__ == "__main__":
    main()
