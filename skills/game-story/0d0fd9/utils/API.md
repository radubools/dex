## `bench`

Simulate Model Workshop's bench beat by beat: which panels a story leaves up, and which ui.hide misses.

- `PANEL_ACTIONS` — constant
- `bench_trace(beats: Iterable[dict], *, list_ids: bool=False) -> list[tuple[str, dict[str, str | None]]]` — The panels up after each beat: [(beat id, {panel key: title or None})]; list_ids=True lets a ui.hide list hide each key.
- `ui_hide_misses(beats: Iterable[dict], *, list_ids: bool=False) -> list[str]` — Ids of beats whose ui.hide removes nothing (nothing up under it, or a list id unless list_ids=True, as the game now accepts).

## `rng`

The engine's seeded RNG (core/rng.js makeRng, mulberry32), bit-exact, to recompute what a seeded game action draws.

- `make_rng(seed: int) -> Callable[[], float]` — A callable returning the same floats in [0, 1) as the engine's makeRng(seed), in the same order.

## `story`

Stories: lesson scripts the game's story runner plays, packaged as kind "story".

- `SCHEMA` — constant
- `KIND` — constant
- `STORY_FILE` — constant
- `EXPORT_SUBDIR` — constant
- `SAY_MAX` — constant
- `CHOICE_EVERY` — constant
- `BEATS_SOFT` — constant
- `BEATS_HARD` — constant
- `LEVELS` — constant
- `NAME` — constant
- `ID` — constant
- `ASSET` — constant
- `HEX` — constant
- `PALETTE` — constant
- `STEMS` — constant
- `PROJECT_DIR` — constant
- `actions() -> dict[str, dict]` — The `act` catalogue the game implements: `{name: {algorithm, summary, args}}`.
- `known_assets(game_root: str | Path | None=None, packages_dir: str | Path | None=None) -> set[str]` — `kind/name` of every asset in the game's index and in a project's packages.
- `fmt_number(value: Any, digits: int=2) -> str` — A number as the game prints it: whole as is, else JS toFixed(digits), real minus "−", no "−0".
- `fmt_fixed(value: Any, digits: int=2) -> str` — A number as the game's num() prints it (model.params notes, model.explain): always `digits` decimals, real minus, no "−0.00".
- `fmt_sig(value: Any) -> str` — A density as the game's sig() prints it (naive Bayes model.explain): 3 decimals ≥ 0.1, 3 significant digits ≥ 1e−4, else "1.58e−266".
- `fmt_score(value: Any) -> str` — A score as Model Workshop's evaluation labels print it (fmt in evaluation.js: eval.score MSE, CV means, AUC): 2 decimals below 10, 1 below 100, else none; "–" if not finite.
- `fmt_percent(value: Any) -> str` — A share as Model Workshop's pct() prints it (accuracy, recall, precision, F1 labels and meters): Math.round(value × 100) then "%".
- `validate_story(package_or_dict: str | Path | dict, game_root: str | Path | None=None, *, known: Iterable[str] | None=None, packages_dir: str | Path | None=None) -> dict` — Check a story (a package directory or the story dict) against schema 1.
- `spawned_assets(beats: Iterable[dict]) -> list[str]` — Every `kind/name` the beats spawn, once each in first-seen order: a story's `assets` list.
- `write_story(package: str | Path, story: dict, *, name: str, title: str='', tags: Sequence[str]=()) -> Path` — Write `story.json` and `manifest.json` into `package`.
- `export_story(package: str | Path, game_root: str | Path) -> dict` — Copy a valid story package into `<game_root>/public/assets/story/<name>/`.
- `story_packages(directory: str | Path) -> list[Path]` — Every story package at or below `directory`.
- `export_all_stories(directory: str | Path, game_root: str | Path) -> list[dict]` — Export every story package under `directory`.
- `simulate(package_or_dict: str | Path | dict, game_root: str | Path) -> list[dict]` — Play a story through the game's own action handlers (storysim.mjs, needs node): one snapshot dict per beat.
- `outline(package_or_dict: str | Path | dict) -> str` — The story as a readable playthrough (Markdown): the review a model's previews give.