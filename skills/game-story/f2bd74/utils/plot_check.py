"""plot.function formulas compiled and evaluated by the game engine's own plot compiler, without a browser."""
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def engine_plot(game_root) -> Path:
    """The engine's plot module a game uses: ~/games/<game>/../engine/src/plot/index.js."""
    return Path(game_root).expanduser().resolve().parent / "engine" / "src" / "plot" / "index.js"


def formula_values(fn: str, envs=(), game_root="~/games/model-workshop") -> dict:
    """{fn, vars, values} of formula `fn` at each env ({name: value}) as the engine computes it (non-finite as text), or {fn, error} when it does not parse."""
    return formulas_values([(fn, envs)], game_root)[0]


def formulas_values(items, game_root="~/games/model-workshop") -> list[dict]:
    """formula_values for several (fn, envs) pairs in one node run."""
    payload = json.dumps([{"fn": fn, "at": list(envs)} for fn, envs in items])
    out = subprocess.run(["node", str(HERE / "plot_check.mjs"), str(engine_plot(game_root)), payload],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)
