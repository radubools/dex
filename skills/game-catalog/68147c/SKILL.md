---
name: game-catalog
description: Story catalogs for the games in ~/games — the master view of a game's lessons: sections (kinds of lesson) and their tiles, each pointing at a story with a display title, icon and thumbnail, laid out as a grid, a winding linear path or mind-map trees, with the progression order. Write, check with the game's engine, preview and install kind "catalog" packages, and add placeholder tiles for lessons not written yet.
---

# Game catalog — the map of a game's lessons

A **catalog** says which stories (lessons) a game offers, how they are
grouped, where each sits on the game's map and in what order they are meant
to be played. It is the game's master view: the game draws it (the engine's
`StoryMap`, or the game's own 3D hub) instead of a list hardcoded in its code.

One catalog per game, named after the game (`model-workshop`), kind
`catalog`. It holds no story: each tile only **points at** a story
(`story/<name>`), which the game-story skill writes in its own task.

```python
from utils import story_catalog as sc
GAME = Path.home() / "games" / "<game>"     # the project brief names the game folder
```

Read `utils/API.md` for every signature.

## The format (schema 1)

```json
{ "schema": 1, "id": "model-workshop", "title": "Model Workshop",
  "blurb": "Machine learning, statistics, algorithms.",
  "layout": "linear",
  "sections": [
    { "id": "ml", "title": "Machine learning", "icon": "🧠", "blurb": "…",
      "layout": "grid", "columns": 5,
      "variants": [ {"id": "easy", "title": "Easy", "suffix": "", "icon": "🌱", "blurb": "…"},
                    {"id": "medium", "title": "Medium", "suffix": "-medium"} ],
      "tiles": [
        { "id": "knn", "title": "k-nearest neighbours", "icon": "🧭", "blurb": "Ask the closest points and take a vote.",
          "story": "story/knn", "thumbnail": "environment/station-sign/sign-knn.glb", "at": [4, 0] } ] },
    { "id": "statistics", "title": "Statistics", "icon": "📊", "layout": "linear", "perRow": 4, "placeholder": true,
      "tiles": [ { "id": "mean", "title": "The mean", "story": "story/stats-mean", "placeholder": true } ] },
    { "id": "algorithms", "title": "Algorithms and data structures", "icon": "🧩", "layout": "tree", "placeholder": true,
      "tiles": [ { "id": "array", "title": "Arrays", "story": "story/ds-array", "placeholder": true },
                 { "id": "binary-search", "title": "Binary search", "story": "story/algo-binary-search", "parent": "array", "placeholder": true } ] } ] }
```

| field | where | meaning |
|---|---|---|
| `id` | all | kebab-case; tile ids are unique within their section |
| `title` | all | the display title the game shows |
| `navigation` | catalog | `map` (default) or `subjects`: main menu → subject → classroom (below) |
| `layout` | catalog, section | `grid`, `linear` or `tree` (below); the catalog's lays out its sections, a section's its tiles |
| `icon`, `blurb` | section, tile | an emoji and a one-line pitch |
| `thumbnail` | section, tile | `kind/name/file` of an installed asset, or `thumbs/<id>.png` inside this package (listed in `files`). An image shows on the 2D map; a model file (`.glb`) is for games that draw tiles in 3D |
| `story` | tile | `story/<name>`: the lesson it plays. With variants, the variant's `suffix` is appended |
| `variants` | section | the same tiles at several levels (`story/knn`, `story/knn-medium`, …) |
| `at` | grid item | `[col, row]` from 0; items without it fill free cells row by row |
| `columns` | grid section | how wide the grid is (default about square) |
| `perRow` | linear section | tiles per row of the winding path (default 4) |
| `parent` | tree item | the hub or branch it hangs from; an item with no parent is a hub |
| `after` | any item | ids it should come after (the order); a linear path and a tree's branches already imply theirs |
| `placeholder` | section, tile | its story does not exist yet: expected missing, shown as coming soon |

Any other field is the game's own (Model Workshop's tile ids are its
algorithm ids); the engine ignores what it does not know.

### Layouts

- **grid**: tiles at coordinates. Use it where the game has a fixed floor
  plan (Model Workshop's lab: five benches across, three deep).
- **linear**: a winding path, each lesson feeding the next: `perRow` to a
  row, each row turning back the way it came. Use it for a course whose
  lessons build on each other in order.
- **tree**: mind maps. Each hub (a tile with no `parent`) is the centre of its
  own cluster; its branches radiate round it, theirs further out. Several
  hubs are several separate clusters side by side. Use it for topics that
  hang off a central idea (a data structure in the middle, the algorithms
  that use it round it).

The master view lays out the sections the same way, by the catalog's own
`layout`; `linear` with `after` between sections is a progression from one
section to the next.

### Navigation: map or subjects

The catalog's `navigation` says how the player reaches a story:

- **`map`** (the default, and what a catalog without the field is): the
  master view of the sections above, then a section's own map.
- **`subjects`**: an exact hierarchy, **main menu → subject → classroom →
  story**. The main menu offers each section as a subject (its icon, title
  and blurb); choosing one opens that subject's classroom, which holds that
  section's tiles and nothing else. Use it when a game's sections are
  separate subject matters the player picks between (Model Workshop: machine
  learning, statistics, algorithms and data structures), not stages of one
  course.

```json
{ "schema": 1, "id": "model-workshop", "title": "Model Workshop", "navigation": "subjects",
  "sections": [ { "id": "ml", "title": "Machine learning", "icon": "🧠", "blurb": "…", "layout": "grid", "tiles": [] },
                { "id": "statistics", "title": "Statistics", "icon": "📊", "blurb": "…", "layout": "linear", "tiles": [] } ] }
```

Under `subjects`:

- **subjects never chain**: no section has `after` (each is chosen freely
  from the menu) or `parent` (subjects do not nest). The check refuses both;
  `after` between tiles inside a section is still fine;
- every subject needs an `icon` and a `blurb` for the menu (the engine warns
  without them; treat those warnings as errors);
- a story belongs to exactly one subject;
- a section's `layout` still lays out its own 2D map; the classroom stands a
  grid section's tiles at their `at`, and a linear or tree section's in rows
  in catalog order.

`sc.new_catalog(id, title, navigation="subjects")` starts one; to switch an
existing catalog, set `cat["navigation"] = "subjects"` and drop the sections'
`after`. `sc.outline` prints each subject with its classroom, and
`sc.preview_catalog` draws the main menu as `previews/catalog.png`.

### Placeholders

A tile whose story the game does not have is shown "coming soon" and cannot
be played; it goes live by itself as soon as its story is installed. So a new
set of lessons **starts here**: the catalog gets its section and placeholder
tiles, the game pulls it and shows the new section, and the stories follow,
each in its own story task. Mark such tiles (and a section with nothing
playable yet) `"placeholder": true`; the check then expects their stories to
be missing and warns about any other missing one. `sc.mark_ready(cat,
sc.known_assets(GAME))` tidies the flags away the next time you edit the
catalog.

## Doing a catalog task

1. **Start from what the game has.** `sc.installed_catalog(GAME)` is the
   catalog installed now; edit that (or this task's own earlier
   `catalog.json`, on a follow-up). Only a game with none starts from
   `sc.new_catalog(id, title, layout=…)`. Keep every existing section, tile id
   and story path unless the brief says otherwise: players' progress is kept
   by those ids.
2. **Add or change** with `sc.add_section`, `sc.add_tiles` (both keep what is
   there unless `replace=True`), `sc.section`, `sc.tile`, `sc.placeholder`.
   Story names follow the project's slug rules for stories (the project brief
   says them); a story task will be asked to write exactly that name, so pick
   it once and keep it.
3. **Thumbnails**, when the brief asks: point at an installed asset's image,
   or draw small PNGs (PIL, ≤ 256 px) into `thumbs/` and pass them as `files`.
   Without one the game shows the icon.
4. **Write and check**: `sc.write_catalog(TASK_DIR, cat)`, then
   `sc.validate_catalog(TASK_DIR, GAME)` must be `ok` with `engine: True`
   (the game's own engine checked it). Read the warnings: a non-placeholder
   tile whose story is missing is usually a typo.
5. **Preview**: `sc.preview_catalog(TASK_DIR, GAME)` draws the master view and
   each section into `previews/`, as the engine lays them out. Look at them:
   tiles must not crowd, a path should read in order, every hub's branches
   must be its own.
6. **Summarise** with `sc.outline(TASK_DIR, GAME)` in your final message: what
   changed, and which placeholder stories now wait to be written.

"Done" is a valid catalog (checked by the engine) with its previews. Don't
install it into the game: the game pulls it (`pull_dex_assets.py`, which
checks it again against what the game then has).

## A new set of lessons (for stories and chats)

When a chat or a story task is asked for a **new set of lessons** (a new
section, or several new tiles), the catalog changes first, so the game shows
the set (as placeholders) while its stories are written:

1. `find_tasks("catalog")` in the project: the game's catalog task. Continue it
   (`continue_task`) with the new section or tiles, their layout, titles and
   story names; or, with none, `start_task` in the catalog thread (slug prefix
   `catalog-`, the project brief's "Which thread" table).
2. Wait for it (`wait_for_tasks`, or end the turn: dex blocks the task until
   it is done). A chat then pulls it into the game with `--only <its task dir>`.
3. Start one story task per lesson, in the stories thread, each brief naming
   its exact `story/<name>` from the catalog. Wait, pull them as they pass.
   Each tile goes live in the game as its story is installed.

A single new lesson in an existing section needs its tile too: the same
steps, with one tile.

## How a game uses it

The engine's `@games/engine/story` has the format (`validateCatalog`,
`layoutSection`, `layoutCatalog`, `tileStatus`, `nextTile`, `loadCatalog`)
and `@games/engine/ui` its 2D map (`StoryMap`); see the engine's
`design/19_story_catalog.md`. A game loads the catalog from its asset index
(`loadCatalog(index)`), shows the master view, and plays a tile's story.
