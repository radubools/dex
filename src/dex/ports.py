"""Port leases: a free TCP port for a task's temporary server, never handed out twice.

"Find a free port" by trying to bind one is a race: two callers probe the same
port, both find it free, both start a server on it, one fails. So ports are
*leased* under a lock: lock, read the lease table, pick a port that is neither
leased nor bound, record the lease, release. The lease holds until it is
released, its holder process exits, or it expires.

    port = ports.lease(state_dir, owner="story-e2e knn")
    ...                                 # start the server on `port`
    ports.release(state_dir, port)

    with ports.leased(state_dir, owner="…") as port:
        ...

From a shell (any language): `python -m dex.ports lease --owner NAME [--pid PID]`
prints a port; `python -m dex.ports release PORT`; `python -m dex.ports list`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Iterator

from .locks import file_lock, lock_path

LEASES_FILE = "ports.json"
#: Where leases come from: above the games' own dev servers (5173–5299) and
#: the story tool's default scan start (5300), below anything system-wide.
PORT_RANGE = (5400, 5999)
#: A lease nobody released and whose holder still runs expires after this long.
DEFAULT_TTL_S = 6 * 3600


def _default_state_dir() -> Path:
    return Path(os.environ.get("DEX_STATE") or Path.home() / "dex" / ".dex")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _bindable(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET, "0.0.0.0")):
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                return False
    return True


def _read(path: Path) -> dict[str, dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _live(leases: dict[str, dict], now: float) -> dict[str, dict]:
    """The leases still held: holder alive and not expired."""
    return {p: l for p, l in leases.items()
            if _alive(int(l.get("pid") or 0)) and now < float(l.get("until") or 0)}


def lease(state_dir: Path | None = None, *, owner: str = "", pid: int | None = None,
          ttl: float = DEFAULT_TTL_S, port_range: tuple[int, int] = PORT_RANGE) -> int:
    """Lease a free port: locked read of the table, pick, record, unlock. Returns the port."""
    state = Path(state_dir) if state_dir else _default_state_dir()
    table = state / LEASES_FILE
    with file_lock(lock_path(state, "ports")):
        now = time.time()
        leases = _live(_read(table), now)
        lo, hi = port_range
        # Start past the last lease, so a port just released is not reused at once.
        start = max([int(p) for p in leases] + [lo - 1]) + 1
        candidates = list(range(start, hi + 1)) + list(range(lo, start))
        for port in candidates:
            if str(port) in leases or not _bindable(port):
                continue
            leases[str(port)] = {"owner": owner, "pid": pid or os.getpid(), "at": now, "until": now + ttl}
            table.write_text(json.dumps(leases, indent=2) + "\n")
            return port
    raise RuntimeError(f"no free port in {lo}–{hi}")


def release(state_dir: Path | None, port: int) -> bool:
    """Give a port back. True when it was leased."""
    state = Path(state_dir) if state_dir else _default_state_dir()
    table = state / LEASES_FILE
    with file_lock(lock_path(state, "ports")):
        leases = _live(_read(table), time.time())
        held = leases.pop(str(port), None) is not None
        table.write_text(json.dumps(leases, indent=2) + "\n")
        return held


def leases(state_dir: Path | None = None) -> dict[int, dict]:
    """The ports currently leased, with who holds them."""
    state = Path(state_dir) if state_dir else _default_state_dir()
    with file_lock(lock_path(state, "ports")):
        return {int(p): l for p, l in _live(_read(state / LEASES_FILE), time.time()).items()}


@contextlib.contextmanager
def leased(state_dir: Path | None = None, *, owner: str = "", ttl: float = DEFAULT_TTL_S) -> Iterator[int]:
    """A port for the `with` block, released after it."""
    port = lease(state_dir, owner=owner, ttl=ttl)
    try:
        yield port
    finally:
        release(state_dir, port)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dex.ports", description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", help="dex's state directory (default $DEX_STATE or ~/dex/.dex)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("lease", help="lease a free port and print it")
    a.add_argument("--owner", default="")
    a.add_argument("--pid", type=int, help="the process the lease belongs to (default: the caller's parent)")
    a.add_argument("--ttl", type=float, default=DEFAULT_TTL_S)
    r = sub.add_parser("release", help="give a port back")
    r.add_argument("port", type=int)
    sub.add_parser("list", help="the ports leased now")
    args = ap.parse_args(argv)
    state = Path(args.state).expanduser() if args.state else None
    if args.cmd == "lease":
        # From a shell the lease belongs to whoever called us, not to this short-lived process.
        print(lease(state, owner=args.owner, pid=args.pid or os.getppid(), ttl=args.ttl))
    elif args.cmd == "release":
        print("released" if release(state, args.port) else "not leased")
    else:
        print(json.dumps(leases(state), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
