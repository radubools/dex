"""What a story's end-to-end report found: every failed check, run error and page problem, as data."""
import json
from pathlib import Path


def e2e_failures(report_path) -> list[dict]:
    """Every failed check, run error and page problem in an e2e report.json: [{viewport, route, beat, check, detail}], empty when it passed.

    Reads one report: a full run's `e2e/<viewport>/report.json` or merged
    `e2e/report.json`, or a segment's `e2e/segments/<beat>/e2e/report.json`.
    A run error or page problem has `beat` None and `check` "error" or "problem".
    """
    rep = json.loads(Path(report_path).read_text())
    out = []
    for run in rep.get("runs", []):
        where = {"viewport": run.get("viewport"), "route": run.get("route")}
        for e in run.get("errors", []) or []:
            out.append({**where, "beat": None, "check": "error", "detail": str(e)})
        for p in run.get("problems", []) or []:
            out.append({**where, "beat": None, "check": "problem", "detail": str(p)})
        for b in run.get("beats", []) or []:
            for c in b.get("checks", []) or []:
                if not c.get("ok"):
                    out.append({**where, "beat": b.get("id"), "check": c.get("name"), "detail": c.get("detail")})
            for p in b.get("problems", []) or []:
                out.append({**where, "beat": b.get("id"), "check": "problem", "detail": str(p)})
    return out
