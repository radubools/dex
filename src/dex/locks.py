"""Locks for read-modify-write on shared state: skills and port leases.

Two tasks finishing at once, a design turn publishing while a task promotes,
two test runs looking for a free port: each reads something shared, decides,
and writes it back. Without a lock both read the same "before", and the second
write drops the first. So every such update takes a lock, reads, writes, and
releases, in that order.

A lock here is two locks at once:
- a `threading.Lock` per name, for the tasks inside this server process (the
  runner works in one process, on several threads and one event loop);
- an `fcntl.flock` on a file under the state directory, for everything else:
  scripts, the CLI, another server, a task's own shell.

`flock` is advisory: only code that takes the lock is kept out. Everything in
dex that touches skills or ports goes through here.

**Stale locks are broken.** Every hold is a few seconds of reading and
writing. A lock held for longer than `STALE_S` (5 minutes) is taken to be
dead: a holder that hung, or one left behind by a restart. The next caller
that finds it so breaks it, logs who held it, and takes the lock itself:
- the thread lock is replaced with a fresh one, which the stuck thread no
  longer holds;
- the lock file is unlinked and created again, so the stuck holder keeps an
  flock on a file nobody else opens. The holder's pid and time are written
  in the file; a separate `.break` lock orders that write against the break,
  so only a lock that really is old gets broken, and a caller that locked a
  file someone unlinked in the meantime opens the new one and tries again.
A holder that comes back after its lock was broken releases a lock nobody
waits on; the work it then writes may race the new holder, which is the price
of not waiting on it forever.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterator

LOCKS_DIR = "locks"
#: A lock held longer than this is taken to be dead and is broken.
STALE_S = 300.0

log = logging.getLogger(__name__)


class LockTimeout(TimeoutError):
    """Somebody held the lock for longer than the caller was willing to wait."""


class _Slot:
    """One name's thread lock, and since when it is held (None while free)."""

    __slots__ = ("lock", "since")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.since: float | None = None


_threads: dict[str, _Slot] = {}
_guard = threading.Lock()


def lock_path(state_dir: Path, name: str) -> Path:
    """The lock file for `name` (a skill name, "ports", …)."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return Path(state_dir) / LOCKS_DIR / f"{safe}.lock"


def _hold_thread(key: str, deadline: float, stale: float, poll: float) -> _Slot:
    """Acquire this process's lock for `key`, breaking one held past `stale`."""
    while True:
        with _guard:
            slot = _threads.setdefault(key, _Slot())
        if slot.lock.acquire(timeout=max(0.0, min(1.0, deadline - time.monotonic()))):
            slot.since = time.time()
            return slot
        since = slot.since
        if since is not None and time.time() - since > stale:
            with _guard:
                if _threads.get(key) is slot:
                    log.warning("lock %s: a thread held it for %.0fs; breaking it", Path(key).name, time.time() - since)
                    _threads[key] = _Slot()
            continue
        if time.monotonic() >= deadline:
            raise LockTimeout(f"{Path(key).name}: still held after the wait")
        time.sleep(poll)


def _holder(fd: int) -> tuple[str, float | None]:
    """Who holds the lock file open on `fd`, and since when. None while nobody has written it."""
    try:
        text = os.pread(fd, 128, 0).decode(errors="replace").split()
        return text[0], float(text[1])
    except (OSError, ValueError, IndexError):
        return "?", None


def _same_file(fd: int, path: Path) -> bool:
    try:
        return os.fstat(fd).st_ino == os.stat(path).st_ino
    except FileNotFoundError:
        return False


@contextlib.contextmanager
def _breaking(path: Path) -> Iterator[None]:
    """The `.break` lock: held to break a lock file, and by a new holder while it writes its name in."""
    guard = os.open(f"{path}.break", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(guard, fcntl.LOCK_EX)  # held for a stat and an unlink or a write only
        try:
            yield
        finally:
            fcntl.flock(guard, fcntl.LOCK_UN)
    finally:
        os.close(guard)


def _stale(fd: int, stale: float) -> bool:
    since = _holder(fd)[1]
    return since is not None and time.time() - since > stale


def _hold_file(path: Path, deadline: float, stale: float, poll: float) -> int:
    """Open and flock `path`, breaking a lock held past `stale`. Returns the locked fd.

    A holder writes "pid time" into the file under the `.break` lock, after
    checking the file is still the one at `path`; a breaker unlinks it under
    the same lock, after checking the time it read is old. So a breaker never
    unlinks a file a live holder has just taken, and a holder never keeps a
    file that was unlinked under it.
    """
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if not _same_file(fd, path):
                        break  # broken by someone else: open the new file
                    if _stale(fd, stale):
                        with _breaking(path):
                            if _same_file(fd, path) and _stale(fd, stale):
                                pid, since = _holder(fd)
                                log.warning("lock %s: pid %s held it for %.0fs; breaking it",
                                            path.name, pid, time.time() - since)
                                os.unlink(path)
                        break
                    if time.monotonic() >= deadline:
                        raise LockTimeout(f"{path.name}: still held after the wait") from None
                    time.sleep(poll)
                    continue
                with _breaking(path):
                    if _same_file(fd, path):
                        os.ftruncate(fd, 0)
                        os.pwrite(fd, f"{os.getpid()} {time.time():.3f}\n".encode(), 0)
                        fd, locked = -1, fd
                        return locked
                fcntl.flock(fd, fcntl.LOCK_UN)  # we locked a file that was unlinked meanwhile
                break
        finally:
            if fd >= 0:
                os.close(fd)


@contextlib.contextmanager
def file_lock(path: Path, timeout: float | None = None, poll: float = 0.05,
              stale: float = STALE_S) -> Iterator[None]:
    """Hold an exclusive lock on `path` (created if needed) for the `with` block.

    Waits up to `timeout` seconds (default: long enough for a stale lock to be
    broken), then raises LockTimeout. A lock held for longer than `stale`
    seconds is broken and taken over. Released on exit, and by the OS if the
    process dies holding it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wait = stale + 60.0 if timeout is None else timeout
    deadline = time.monotonic() + wait
    slot = _hold_thread(str(path.resolve()), deadline, stale, poll)
    try:
        fd = _hold_file(path, deadline, stale, poll)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)  # free: nothing left to look stale
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    finally:
        slot.since = None
        slot.lock.release()


def skill_lock(state_dir: Path, name: str, timeout: float | None = None):
    """The lock every change to one skill takes: promote, publish, adopt, move projects."""
    return file_lock(lock_path(state_dir, f"skill-{name}"), timeout=timeout)
