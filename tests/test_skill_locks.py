"""Skill updates and port leases are lock, read, write, release (dex/locks.py, skills.promote, dex/ports.py)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from dex import ports, skills
from dex.locks import LockTimeout, file_lock


def _skill(ws: Path, name: str, files: dict[str, str]) -> skills.Skill:
    d = ws / "skills" / name / "draft"
    (d / "utils").mkdir(parents=True)
    (d / "skill.json").write_text(json.dumps({"name": name, "description": "d"}))
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    return skills.publish(skills.read(d))


def _project(ws: Path, name: str, skill: skills.Skill) -> Path:
    p = ws / "assets" / name
    p.mkdir(parents=True)
    skills.materialise(ws, p, [skill.name], versions={skill.name: skill.version})
    return p


def test_a_file_lock_excludes_other_holders_and_times_out(tmp_path):
    path = tmp_path / "x.lock"
    held = threading.Event()
    release = threading.Event()

    def holder():
        with file_lock(path):
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    with pytest.raises(LockTimeout):
        with file_lock(path, timeout=0.2):
            pass
    release.set()
    t.join()
    with file_lock(path, timeout=1):
        pass


def test_two_tasks_promoting_at_once_both_land(tmp_path):
    ws = tmp_path
    s = _skill(ws, "kit", {"utils/a.py": "A = 1\n", "SKILL.md": "# kit\n"})
    project = _project(ws, "ml", s)
    one = skills.work_copies(ws, project, tmp_path / "work1")
    two = skills.work_copies(ws, project, tmp_path / "work2")
    # Each task adds its own helper; the second also edits a different line of SKILL.md.
    (one["kit"][1] / "utils" / "b.py").write_text("B = 2\n")
    (two["kit"][1] / "utils" / "c.py").write_text("C = 3\n")
    (two["kit"][1] / "SKILL.md").write_text("# kit\n\nc.py: C.\n")
    first = skills.promote(ws, project, one, state_dir=tmp_path / ".dex")
    second = skills.promote(ws, project, two, state_dir=tmp_path / ".dex")
    assert first["changed"] and second["changed"] and not second["conflicts"]
    now = skills.find(ws, "kit", skills.read_enabled(project).skills["kit"])
    names = {p.name for p in (now.path / "utils").iterdir()}
    assert {"a.py", "b.py", "c.py"} <= names, names
    assert "c.py: C." in (now.path / "SKILL.md").read_text()
    # The version the project started on is still there, unchanged, for anyone else on it.
    assert skills.find(ws, "kit", s.version) is not None


def test_promotions_racing_on_threads_lose_nothing(tmp_path):
    ws = tmp_path
    s = _skill(ws, "kit", {"utils/a.py": "A = 1\n"})
    project = _project(ws, "ml", s)
    copies = [skills.work_copies(ws, project, tmp_path / f"w{i}") for i in range(6)]
    for i, c in enumerate(copies):
        (c["kit"][1] / "utils" / f"h{i}.py").write_text(f"H = {i}\n")
    threads = [threading.Thread(target=skills.promote, args=(ws, project, c), kwargs={"state_dir": tmp_path / ".dex"}) for c in copies]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    now = skills.find(ws, "kit", skills.read_enabled(project).skills["kit"])
    assert {f"h{i}.py" for i in range(6)} <= {p.name for p in (now.path / "utils").iterdir()}


def test_the_same_lines_changed_twice_is_a_conflict_and_nothing_is_published(tmp_path):
    ws = tmp_path
    s = _skill(ws, "kit", {"utils/a.py": "A = 1\n"})
    project = _project(ws, "ml", s)
    one = skills.work_copies(ws, project, tmp_path / "w1")
    two = skills.work_copies(ws, project, tmp_path / "w2")
    (one["kit"][1] / "utils" / "a.py").write_text("A = 2\n")
    (two["kit"][1] / "utils" / "a.py").write_text("A = 3\n")
    skills.promote(ws, project, one, state_dir=tmp_path / ".dex")
    v = skills.read_enabled(project).skills["kit"]
    out = skills.promote(ws, project, two, state_dir=tmp_path / ".dex")
    assert out["conflicts"] == {"kit": ["utils/a.py"]} and not out["changed"]
    assert skills.read_enabled(project).skills["kit"] == v


def test_publishing_a_draft_keeps_what_tasks_promoted_since_the_fork(tmp_path):
    ws = tmp_path
    s = _skill(ws, "kit", {"utils/a.py": "A = 1\n", "SKILL.md": "# kit\n"})
    project = _project(ws, "ml", s)
    draft = skills.fork(ws, "kit", s.version)
    (draft.path / "SKILL.md").write_text("# kit\n\nDesigned.\n")
    # Meanwhile a task promotes a helper.
    work = skills.work_copies(ws, project, tmp_path / "w")
    (work["kit"][1] / "utils" / "b.py").write_text("B = 2\n")
    skills.promote(ws, project, work, state_dir=ws / ".dex")
    done = skills.adopt(ws, ws / "assets")
    assert done and done[0][2] == ["ml"]
    now = skills.find(ws, "kit", skills.read_enabled(project).skills["kit"])
    assert (now.path / "utils" / "b.py").is_file() and "Designed." in (now.path / "SKILL.md").read_text()


def test_port_leases_never_hand_out_the_same_port_twice(tmp_path):
    got: list[int] = []
    lock = threading.Lock()

    def take():
        p = ports.lease(tmp_path, owner="t")
        with lock:
            got.append(p)

    threads = [threading.Thread(target=take) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(got)) == 12
    assert set(ports.leases(tmp_path)) == set(got)
    assert ports.release(tmp_path, got[0]) and got[0] not in ports.leases(tmp_path)


def test_a_lease_whose_holder_died_is_free_again(tmp_path):
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    p = ports.lease(tmp_path, owner="gone", pid=proc.pid)
    assert p not in ports.leases(tmp_path)
    with ports.leased(tmp_path, owner="block") as q:
        assert q in ports.leases(tmp_path)
    assert q not in ports.leases(tmp_path)
    assert time.time()  # nothing else to wait for


# ---- stale locks: a holder stuck for longer than `stale` is broken ----------

_HOLD = """
import sys, time
sys.path.insert(0, {src!r})
from dex.locks import file_lock
with file_lock({path!r}):
    print("held", flush=True)
    time.sleep(60)
"""


def _stuck_holder(path: Path):
    """Another process that takes the lock and hangs holding it (what a restart can leave behind)."""
    import subprocess
    import sys
    src = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.Popen([sys.executable, "-c", _HOLD.format(src=src, path=str(path))],
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_a_lock_held_past_stale_by_a_hung_process_is_broken_and_taken(tmp_path):
    path = tmp_path / "x.lock"
    proc = _stuck_holder(path)
    try:
        with pytest.raises(LockTimeout):  # fresh: waited on, not broken
            with file_lock(path, timeout=0.3, stale=30):
                pass
        time.sleep(0.6)
        t0 = time.monotonic()
        with file_lock(path, timeout=5, stale=0.5):
            assert proc.poll() is None  # the holder still runs; its lock was broken
            assert path.read_text().split()[0] == str(__import__("os").getpid())
        assert time.monotonic() - t0 < 2
    finally:
        proc.kill()
        proc.wait()


def test_a_thread_stuck_holding_a_lock_is_broken_after_stale(tmp_path):
    path = tmp_path / "x.lock"
    held, release = threading.Event(), threading.Event()

    def stuck():
        with file_lock(path, stale=0.4):
            held.set()
            release.wait(10)

    t = threading.Thread(target=stuck)
    t.start()
    held.wait(5)
    t0 = time.monotonic()
    with file_lock(path, timeout=5, stale=0.4):
        assert time.monotonic() - t0 >= 0.3
        assert t.is_alive()
    release.set()
    t.join()
    with file_lock(path, timeout=1):  # the stuck thread's late release leaves the lock usable
        pass


_COUNT = """
import sys, time
sys.path.insert(0, {src!r})
from dex.locks import file_lock
with file_lock({path!r}, timeout=20, stale=0.5):
    n = int(open({counter!r}).read())
    time.sleep(0.05)
    open({counter!r}, "w").write(str(n + 1))
"""


def test_waiters_racing_to_break_one_stale_lock_still_take_it_one_at_a_time(tmp_path):
    import subprocess
    import sys
    path, counter = tmp_path / "x.lock", tmp_path / "n"
    counter.write_text("0")
    proc = _stuck_holder(path)
    src = str(Path(__file__).resolve().parents[1] / "src")
    try:
        waiters = [subprocess.Popen([sys.executable, "-c", _COUNT.format(src=src, path=str(path), counter=str(counter))])
                   for _ in range(8)]
        assert [w.wait(30) for w in waiters] == [0] * 8
        assert counter.read_text() == "8"  # no two waiters held it at once after the break
    finally:
        proc.kill()
        proc.wait()
