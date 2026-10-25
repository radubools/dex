## `story_catalog`

Story catalogs: which lessons a game has, how they are grouped, laid out and ordered.

- `KIND` — constant
- `CATALOG_FILE` — constant
- `SCHEMA` — constant
- `LAYOUTS` — constant
- `EXPORT_SUBDIR` — constant
- `NAME` — constant
- `STORY` — constant
- `new_catalog(id: str, title: str, *, layout: str='linear', blurb: str='', **fields: Any) -> dict` — An empty catalog: the game's id (kebab-case), title, and how its sections are laid out.
- `tile(id: str, title: str, story: str | None=None, **fields: Any) -> dict` — A tile: `story` defaults to `story/<id>`. Fields: blurb, icon, thumbnail, at, parent, after, placeholder.
- `placeholder(id: str, title: str, story: str | None=None, **fields: Any) -> dict` — A tile for a story that does not exist yet: shown as coming soon.
- `section(id: str, title: str, *, layout: str='grid', tiles: Iterable[dict]=(), **fields: Any) -> dict` — A section. Fields: blurb, icon, thumbnail, columns (grid), perRow (linear), variants, placeholder, at, after.
- `find_section(cat: dict, section_id: str) -> dict | None` — 
- `add_section(cat: dict, sec: dict, *, replace: bool=False) -> dict` — Append a section; one with the same id is kept unless `replace`. Returns the catalog's section.
- `add_tiles(cat: dict, section_id: str, tiles: Iterable[dict], *, replace: bool=False) -> list[dict]` — Add tiles to a section (tiles whose id it has are kept, unless `replace`). Returns the section's tiles.
- `story_keys(cat: dict) -> list[str]` — Every story the catalog points at, variants included (`story/knn`, `story/knn-medium`, …).
- `mark_ready(cat: dict, known: Iterable[str]) -> list[str]` — Clear `placeholder` on tiles whose story (every variant's) is in `known`; a section whose
- `read_catalog(package_or_dict: str | Path | dict) -> dict` — 
- `installed_catalog(game_root: str | Path, name: str | None=None) -> dict | None` — The catalog the game has installed now (the one named `name`, or its only one): start edits from it.
- `known_assets(game_root: str | Path) -> set[str]` — What the game has installed now: its index's `kind/name` keys.
- `write_catalog(package: str | Path, cat: dict, *, name: str | None=None, title: str='', files: Sequence[str]=(), tags: Sequence[str]=()) -> Path` — Write `catalog.json` and `manifest.json` into `package` (refuses a catalog with structural errors).
- `has_engine(game_root: str | Path) -> bool` — Whether the game has the engine (with its catalog module) and node to run it.
- `validate_catalog(package_or_dict: str | Path | dict, game_root: str | Path | None=None, *, known: Iterable[str] | None=None) -> dict` — Check a catalog: {ok, errors, warnings, stats, engine}.
- `layout(package_or_dict: str | Path | dict, game_root: str | Path) -> dict` — Where everything sits, from the engine: {master, sections: {id: {pos: {id: [x, y]}, edges, width, height}}}.
- `outline(package_or_dict: str | Path | dict, game_root: str | Path | None=None) -> str` — The catalog as indented text: sections, their layout, tiles and their state (✓ in the game, … placeholder).
- `catalog_packages(directory: str | Path) -> list[Path]` — Every catalog package at or below `directory`.
- `preview_catalog(package: str | Path, game_root: str | Path, *, cell: int=150) -> list[Path]` — Draw the master view and each section, as the engine lays them out, into `previews/`.
- `export_catalog(package: str | Path, game_root: str | Path) -> dict` — Copy a valid catalog package into `<game_root>/public/assets/catalog/<name>/` and rebuild the index.