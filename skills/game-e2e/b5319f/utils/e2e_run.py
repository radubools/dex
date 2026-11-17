"""End-to-end tests of a story in its game: the game-e2e skill's runner.

A story task never runs its own end-to-end test. It starts a game-e2e task as
a dependency (in the "End-to-end tests" thread) naming its story package and
the game. That task plays the story with the engine's
`~/games/engine/tools/story-e2e.mjs`, in headless Chromium at desktop and
phone size, and keeps the report, the screenshots and its visual review in
its **own** package directory (`out`). It reads the story's package and never
writes into it. The story task then takes the passing report into its own
`e2e/` with `adopt`, which is what the game's pull and `story.done` read.

    from utils import e2e_run
    r = e2e_run.run(story_pkg, "~/games/model-workshop", out=TASK_DIR / "e2e")
    r["passed"], r["output"]          # out/report.json, out/<size>/report.html and shots/
    e2e_run.problem(story_pkg, out)   # None when out's report passed for the story as it is now

The report names the exact `story.json` and `e2e.json` it tested (sha256), so
a report is good wherever it sits, for as long as the story is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

STORY_FILE = "story.json"
E2E_FILE = "e2e.json"
E2E_DIR = "e2e"
REPORT = "report.json"
REVIEW = "review.md"
#: What the visual review must end with.
REVIEWED = "reviewed: desktop, phone"
SIZES = ("desktop", "phone")
#: What `adopt` copies into the story's e2e/: the reports and the review, not
#: the screenshots (they stay in the e2e task's package, a few hundred MB).
ADOPTED = (REPORT, "report.html", REVIEW)


def tool(game_root: str | Path) -> Path:
    """The engine's story-e2e.mjs next to the game (games share one engine)."""
    root = Path(game_root).expanduser().resolve()
    for base in (root.parent, root):
        f = base / "engine" / "tools" / "story-e2e.mjs"
        if f.is_file():
            return f
    raise FileNotFoundError(f"no engine/tools/story-e2e.mjs beside {root}")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _lease_port(owner: str) -> tuple[int, Callable[[], None]]:
    """A port for one run's dev server, and how to give it back.

    Leased from dex (`dex.ports`, under a lock), so runs started at once never
    get the same port. Without dex, a random free port above the tool's range.
    """
    try:
        from dex import ports
        port = ports.lease(owner=owner)
        return port, lambda: ports.release(None, port)
    except Exception:
        import random
        import socket
        for _ in range(200):
            port = random.randint(5400, 5999)
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                    return port, lambda: None
                except OSError:
                    continue
        raise RuntimeError("no free port for the end-to-end test")


def _spawn(story: Path, game_root: str | Path, viewports: str, out: Path, port: int) -> subprocess.Popen:
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["node", str(tool(game_root)), "--game", str(Path(game_root).expanduser().resolve()),
           "--story", str(story), "--viewports", viewports, "--out", str(out), "--port", str(port)]
    tests = (story if story.is_dir() else story.parent) / E2E_FILE
    if tests.is_file():
        cmd += ["--tests", str(tests)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def merge(out: str | Path, viewports: Sequence[str] = SIZES) -> dict | None:
    """Merge the per-size reports (`out/<size>/report.json`) into `out/report.json`.

    Passed only when every size passed, for the same story.json and e2e.json.
    None when a size has no report.
    """
    out = Path(out)
    parts = []
    for v in viewports:
        f = out / v / REPORT
        if not f.is_file():
            return None
        parts.append(json.loads(f.read_text()))
    first = parts[0]
    same = all(p.get("story", {}).get("sha256") == first.get("story", {}).get("sha256")
               and (p.get("tests") or {}).get("sha256") == (first.get("tests") or {}).get("sha256") for p in parts)
    merged = {**first, "passed": same and all(p.get("passed") for p in parts),
              "runs": [r for p in parts for r in p.get("runs", [])],
              "parts": [f"{v}/report.html" for v in viewports]}
    (out / REPORT).write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    return merged


def merge_html(out: str | Path, game_root: str | Path) -> Path | None:
    """Write `out/report.html` for the merged report, rendered by the engine's own html().

    `merge` writes the merged JSON only (the tool writes one HTML per size);
    this makes the single human-readable report the story task adopts: it
    re-reads `out/report.json`, rewrites each run's shot paths to
    `<viewport>/shots/…` (relative to `out`), and renders them with
    `story-e2e.mjs`'s `html`. `game_root` is only used to find that engine
    tool. None when there is no merged report; the path written otherwise.
    """
    out = Path(out)
    f = out / REPORT
    if not f.is_file():
        return None
    script = (
        "import { readFileSync, writeFileSync } from 'node:fs';"
        "import { pathToFileURL } from 'node:url';"
        "const { html } = await import(pathToFileURL(process.env.E2E_TOOL).href);"
        "const rep = JSON.parse(readFileSync(process.env.E2E_REPORT, 'utf8'));"
        "for (const r of rep.runs || []) for (const b of r.beats || []) {"
        "  if (b.shot) b.shot = `${r.viewport}/${b.shot}`;"
        "  if (b.shotMore) b.shotMore = `${r.viewport}/${b.shotMore}`; }"
        "writeFileSync(process.env.E2E_HTML, html(rep));"
    )
    # paths go through the environment: story-e2e.mjs runs its CLI when
    # process.argv[1] is its own file, and `-e` puts the first arg there.
    env = {**os.environ, "E2E_TOOL": str(tool(game_root)), "E2E_REPORT": str(f),
           "E2E_HTML": str(out / "report.html")}
    subprocess.run(["node", "--input-type=module", "-e", script], check=True, env=env)
    return out / "report.html"


def run(story_pkg: str | Path, game_root: str | Path, out: str | Path, *,
        viewports: Sequence[str] = SIZES, timeout: int = 1800) -> dict:
    """Play the whole story, every route, at each size (in parallel), into `out`.

    `out` is in this task's own package (TASK_DIR / "e2e"): the story's package
    is only read. Each size runs on its own leased port into `out/<size>/`, and
    the reports merge into `out/report.json`, the one a story adopts. Returns
    {passed, report, output}.
    """
    pkg = Path(story_pkg).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    if out == pkg or pkg in out.parents:
        raise ValueError("out must be in this task's own package, not the story's")
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT).unlink(missing_ok=True)
    vps = [v.strip() for v in viewports if v.strip()]
    leases = [_lease_port(f"game-e2e {pkg.name} {v}") for v in vps]
    outs: list[str] = []
    try:
        procs = [(_spawn(pkg, game_root, v, out / v, port), v) for v, (port, _) in zip(vps, leases)]
        for proc, v in procs:
            try:
                o, e = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                o, e = proc.communicate()
            outs.append(f"[{v}] " + "\n".join(((o or "") + (e or "")).strip().splitlines()[-12:]))
    finally:
        for _, give_back in leases:
            give_back()
    report = merge(out, vps)
    return {"passed": bool(report and report.get("passed")), "report": report, "output": "\n".join(outs)}


def cut(story: dict, tests: dict | None, until: str) -> tuple[dict, dict | None]:
    """The story up to and including beat `until` (which ends it), and its tests for those beats.

    Gotos, branches and options leading past it are dropped.
    """
    ids = [b.get("id") for b in story.get("beats") or []]
    if until not in ids:
        raise ValueError(f"no beat {until!r}")
    keep = set(ids[: ids.index(until) + 1])
    beats = []
    for b in story["beats"][: len(keep)]:
        b = json.loads(json.dumps(b))
        if b.get("goto") not in (None, *keep):
            b.pop("goto")
        if b.get("branch"):
            b["branch"] = [x for x in b["branch"] if x.get("goto") in keep]
            if not b["branch"]:
                b.pop("branch")
        w = b.get("wait")
        if isinstance(w, dict) and "choice" in w:
            for o in w["choice"].get("options", []):
                if o.get("goto") not in (None, *keep):
                    o.pop("goto")
        beats.append(b)
    beats[-1]["end"] = True
    cut_tests = None
    if tests is not None:
        cut_tests = {**tests,
                     "beats": {k: v for k, v in (tests.get("beats") or {}).items() if k in keep},
                     "routes": [r for r in tests.get("routes") or [] if set(r.get("choose") or {}) <= keep]}
    return {**story, "beats": beats}, cut_tests


def segment(story_pkg: str | Path, game_root: str | Path, until: str, out: str | Path, *,
            viewports: Sequence[str] = ("desktop",), timeout: int = 1800) -> dict:
    """Test the story only up to beat `until`: fast feedback on one section.

    Plays a cut copy in `out/segments/<until>/`, at one size by default. It is
    never the report a story adopts: that is `run` on the whole story, both
    sizes. Returns {passed, report, output, beats}.
    """
    pkg = Path(story_pkg).expanduser().resolve()
    story = json.loads((pkg / STORY_FILE).read_text())
    tests_file = pkg / E2E_FILE
    tests = json.loads(tests_file.read_text()) if tests_file.is_file() else None
    cut_story, cut_tests = cut(story, tests, until)
    seg = Path(out).expanduser().resolve() / "segments" / until
    if seg.exists():
        shutil.rmtree(seg)
    seg.mkdir(parents=True)
    (seg / STORY_FILE).write_text(json.dumps(cut_story, ensure_ascii=False, indent=2))
    if cut_tests is not None:
        (seg / E2E_FILE).write_text(json.dumps(cut_tests, ensure_ascii=False, indent=2))
    port, give_back = _lease_port(f"game-e2e {pkg.name} {until}")
    try:
        proc = _spawn(seg, game_root, ",".join(viewports), seg / E2E_DIR, port)
        try:
            o, e = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            o, e = proc.communicate()
    finally:
        give_back()
    f = seg / E2E_DIR / REPORT
    report = json.loads(f.read_text()) if f.is_file() else None
    tail = "\n".join(((o or "") + (e or "")).strip().splitlines()[-25:])
    return {"passed": proc.returncode == 0 and bool(report and report.get("passed")), "report": report,
            "output": tail, "beats": len(cut_story["beats"])}


def start(story_pkg: str | Path, game_root: str | Path, out: str | Path, *, timeout: int = 1800) -> dict:
    """Start `run` in the background and return at once: {pid, log}. Then `wait(out)`.

    For a long story whose run outlasts one shell command. Never write sleep
    or polling loops of your own around the tool.
    """
    out = Path(out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    for f in ("e2e.done", "e2e.log"):
        (out / f).unlink(missing_ok=True)
    script = (f"import json, sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r}); "
              f"from utils import e2e_run; r = e2e_run.run({str(Path(story_pkg).expanduser())!r}, "
              f"{str(Path(game_root).expanduser())!r}, {str(out)!r}, timeout={int(timeout)}); "
              f"print(r['output']); open({str(out / 'e2e.done')!r}, 'w').write(json.dumps({{'passed': r['passed']}}))")
    log = open(out / "e2e.log", "w")
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"pid": proc.pid, "log": str(out / "e2e.log")}


def wait(out: str | Path, timeout: float = 540) -> dict:
    """Wait (up to `timeout` s) for a run `start` began: {done, passed, output}. Call again until done."""
    import time
    out = Path(out).expanduser().resolve()
    done = out / "e2e.done"
    t0 = time.time()
    while not done.is_file() and time.time() - t0 < timeout:
        time.sleep(3)
    log = out / "e2e.log"
    tail = "\n".join(log.read_text().splitlines()[-25:]) if log.is_file() else ""
    if not done.is_file():
        return {"done": False, "passed": None, "output": tail}
    return {"done": True, "passed": json.loads(done.read_text()).get("passed"), "output": tail}


def problem(story_pkg: str | Path, out: str | Path) -> str | None:
    """Why `out` holds no passing run for the story as it is now, or None.

    The same test as the game's pull gate: the report passed, for exactly this
    story.json and e2e.json.
    """
    pkg = Path(story_pkg).expanduser()
    f = Path(out).expanduser() / REPORT
    if not f.is_file():
        return f"no end-to-end report at {f}"
    try:
        rep = json.loads(f.read_text())
    except ValueError:
        return f"{f} is not JSON"
    if not rep.get("passed"):
        return "the end-to-end test failed (see report.html)"
    if rep.get("story", {}).get("sha256") != sha256(pkg / STORY_FILE):
        return "story.json changed since this run: run it again"
    tests = pkg / E2E_FILE
    if tests.is_file() and (rep.get("tests") or {}).get("sha256") != sha256(tests):
        return "e2e.json changed since this run: run it again"
    return None


def review_problem(out: str | Path) -> str | None:
    """Why the visual review is missing or unfinished, or None."""
    f = Path(out).expanduser() / REVIEW
    if not f.is_file():
        return f"no visual review ({f})"
    lines = [x.strip() for x in f.read_text().splitlines() if x.strip()]
    if not lines or lines[-1] != REVIEWED:
        return f"{REVIEW} must end with the line {REVIEWED!r}"
    return None


def adopt(story_pkg: str | Path, out: str | Path) -> list[str]:
    """For the story task: take a game-e2e task's passing report into the story's e2e/.

    `out` is the e2e directory the game-e2e task reported (in its package).
    Refuses a report that failed or that tested other content than the story
    has now, and a missing review. Copies report.json, report.html, review.md
    and each size's report; the screenshots stay where they are. Returns the
    files copied.
    """
    pkg = Path(story_pkg).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    why = problem(pkg, out) or review_problem(out)
    if why:
        raise ValueError(f"not adopted: {why}")
    dest = pkg / E2E_DIR
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ADOPTED:
        if (out / name).is_file():
            shutil.copy2(out / name, dest / name)
            copied.append(name)
    for v in SIZES:
        for name in (REPORT, "report.html"):
            src = out / v / name
            if src.is_file():
                (dest / v).mkdir(exist_ok=True)
                shutil.copy2(src, dest / v / name)
                copied.append(f"{v}/{name}")
    return copied


def clear_shots(out: str | Path) -> int:
    """Delete the screenshots under `out` once reviewed; the reports and review stay. Returns how many."""
    n = 0
    for f in Path(out).expanduser().rglob("*"):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            f.unlink()
            n += 1
    return n
