---
name: game-story
description: Lesson scripts ("stories") for the games in ~/games — the story format the engine's story runner plays (schema 1, and schema 2 with values, game reads, expressions, branches, several acts per beat, number inputs and framed highlights), the writing rules, the catalogue of act actions per algorithm, and write/validate/install of kind "story" packages.
---

# Game story — lessons the game plays as data

A **story** is a lesson plan the game's story runner plays at a station: a
list of **beats**. Each beat shows one short cue, may move the camera, spawn
or remove assets, run one game action, highlight something, play a sound or
change the music, then waits — for "next", for the player to do something,
for a few seconds, or for the player to pick the next step from a
multiple-choice question. The choices are how the player builds the
algorithm, piece by piece. A story ends by handing the station over to a
sandbox.

A story is pure data, so there is nothing to render: you write JSON, check it
with the validator, and read it back as an outline.

```python
from utils import story, catalog   # catalog is game-mesh's; it rebuilds the game's index
```

Read `utils/API.md` for every signature.

## The format (schema 1; schema 2 below adds to it)

```json
{ "schema": 1, "id": "knn", "title": "k-nearest neighbours",
  "summary": "Classify a point by asking its neighbours.",
  "algorithm": "knn",
  "assets": ["environment/marker-sphere", "environment/query-star"],
  "beats": [
    { "id": "intro", "say": "Each dot is a fruit. Colour is its kind.",
      "camera": {"target": [0, 0], "distance": 9, "yaw": 30, "pitch": 45, "duration": 1.5},
      "spawn": [{"id": "q", "asset": "environment/query-star", "at": [1.5, -0.5], "scale": 1, "color": "highlight"}],
      "remove": ["old-id"],
      "act": {"action": "knn.query", "args": {"at": [1.5, -0.5]}},
      "highlight": {"entity": "q"},
      "sfx": "reveal",
      "music": {"track": "study-calm", "intensity": "focus"},
      "wait": {"choice": {"prompt": "How do we guess its kind?", "options": [
        {"label": "Look at the closest dots", "correct": true,  "feedback": "Yes. Neighbours tend to share a kind."},
        {"label": "Look at the biggest group", "correct": false, "feedback": "That ignores where the point is."}]}} }
  ] }
```

| field | meaning |
|---|---|
| `id` | kebab-case, equal to the package name. |
| `algorithm` | the algorithm id, the same string the actions use (`knn`, `kmeans`, `svm`, …; see the catalogue). |
| `level` | optional: `easy` (the default when absent), `medium` or `hard`. See "Levels". |
| `assets` | every `<kind>/<name>` the story spawns: the game preloads them. |
| beat `say` | the cue, **at most 140 characters**. |
| `camera` | `target` `[x, z]` in station-local units; `distance`, `yaw`, `pitch` (degrees), `duration` (seconds) optional. |
| `spawn` | `{id, asset, at:[x,z], scale?, clip?, color?}`. Ids are unique among what is in the scene. `clip` plays an animation clip; `color` is a palette name or `#rrggbb`. |
| `remove` | ids spawned earlier. |
| `act` | one action from `utils/actions.json`, with its `args`. The game implements these. |
| `highlight` | `{entity: id}` (something in the scene, or a data point `p<N>`) or `{ui: selector}`. |
| `sfx` | a sound package name (`sfx/<name>` in the game's index). |
| `music` | `{track?, intensity?}`: a music package name, and 0–1 or a stem `calm` / `focus` / `intensity`. |
| `wait` | `"next"`, `{"action": name}` (wait until the player does it), `{"time": s}`, or `{"choice": {prompt, options:[{label, correct, feedback}]}}`. |

**Coordinates** are station-local: 1 unit = 1 tile, origin at the centre of
the station's data floor, `x` right, `z` forward (Y up is the game's). Data
sits in `[-5, 5]` on both axes unless the story says otherwise. Things that
rise (regression heights, kernel lifts, loss surfaces) use Y, which the game
handles; stories only place things on the floor.

**The palette** (`story.PALETTE`, Okabe–Ito, colour-blind safe), used by every
asset and story: `class-0` blue `#0072B2` (sphere), `class-1` orange `#E69F00`
(cube), `class-2` green `#009E73` (cone), `class-3` pink `#CC79A7`
(octahedron), `positive` `#56B4E9`, `negative` `#D55E00`, `highlight` yellow
`#F0E442`, `neutral` grey `#9A9A9A`, `ink` `#1F2328`. Classes are always told
apart by shape *and* colour.

## Schema 2: values, branches, several acts, better highlights

A story that says `"schema": 2` keeps every schema 1 field and meaning, and
can use everything below. The game's engine implements it
(`~/games/engine/design/16_story_v2.md` is the spec), and **validation is the
engine's own**: `validate_story(pkg, lab=game_root)` runs the engine's
`validateStory` with the game's actions, data point ids and reads, so a story
that validates here is one the game accepts. Without a game root a schema 2
story does not validate.

### Values

- **Story variables**: an act's `"as": "fit"` stores what the action returned
  (`fit.w`, `fit.loss`); a beat's `"set": {"twice": "fit.w * 2"}` computes one;
  a choice's `"as"` stores the picked option's `value` (or its label), and an
  option's own `"set"` runs when it is picked; an input's `"as"` stores what
  the player typed. Variables survive a resume.
- **Game reads**: `$`-paths the game answers live, always current:
  `$data.count`, `$model.param.w`, `$data.p3.x`, `$result.knn.vote.cls`. The full
  list is the **game reads** table in the catalogue below (`story.reads(game_root)`
  gives it live). A read of something that is not there is `null`.
- **In text** (`say`, `title`, a choice's prompt, option labels and feedback,
  an input's prompt, `right` and `wrong`): `{expr}` is replaced by the value,
  formatted by the game's number rule; `{expr:2}` forces 2 decimals,
  `{expr:%}` a percentage. **`{{` and `}}` are literal braces** (set notation
  `{{p0, p1}}`). A cue with a `$` read re-renders while it shows. The 140
  characters are counted as written, before substitution.
- **In act args**: a string that is exactly `{expr}` becomes the value itself
  (a number, a list); a longer string gets the text; lists and objects are
  walked: `{"action": "measure.distance", "args": {"from": "{far.id}", "to": "p0"}}`.
- **Values that are not there show "–"**: the checker fails any beat whose
  rendered text has one.

### Expressions

Used in `when`, `branch[].if`, `set`, `wait.until`, `wait.input.answer` and
inside `{…}`. Plain and safe (no code):

- numbers, `'text'`, `true`, `false`, `null`;
- names: a story variable path (`vote.cls`, `fit.w`) or a game read (`$model.param.w`);
- `+ - * / %`, `== != < <= > >=`, `&& || !`, `? :`, parentheses;
- functions `abs min max round(x, digits) floor ceil sqrt exp ln pow len`.

A read's segments may hold `-` and `:` (entity ids like `centroid-1`), so
**put a space before a minus that follows a `$` read**: `$data.count - 1`,
never `$data.count-1`. Arithmetic on `null` gives `null`; comparisons with it
are false (a condition on a value that is not there is false).

### Branching

- `"when": expr` on a beat: the beat is skipped when it is false.
- `"goto": "beat-id"`: where to go after this beat (default: the next one).
- `"branch": [{"if": expr, "goto": "id"}, …]`: checked when the beat is
  done; the first true wins, else `goto`, else the next beat.
- `"end": true`: the story finishes after this beat.
- a choice option's `"goto"` (and `"set"`): picking it, then Continue, goes
  there. A **wrong option with a `goto`** sends the player to a remedial beat
  (which explains and `goto`s back to the question) instead of letting them
  pick again. `choice.as` stores the picked option's `value` (or label).
- `"section": "Ravine"` (≤ 40 characters): a heading on the card from this
  beat on, until another beat names a section.

Loops are allowed; a beat entered more than 50 times stops the story. Every
beat must be reachable from the first, and every branch must lead back to the
main line and on to the end: the checker plays every option that has a
`goto` as its own playthrough, and each must finish.

### Several actions

`act` may be a list. It runs in order, each act awaited; a
`{"parallel": [act, act]}` group starts its acts together and waits for all.
`as` works on each act.

```json
"act": [
  {"action": "data.scatter", "args": {"dataset": "custom", "points": [[0,0,0],[2,1,1]]}},
  {"parallel": [{"action": "knn.query", "args": {"at": [1, 0]}}, {"action": "model.params"}]},
  {"action": "knn.vote", "as": "vote"}
]
```

Every act still goes through the station's lab machine: an act the machine
does not run alongside (`stop: false`) stops the one before it. So `parallel`
is for things that should appear together and do not interrupt each other
(panels, settings, a formula); a run that animates goes on its own.

### Waits

- `{"until": expr}`: done once the expression is true (checked every
  frame): `"$data.count >= 8"` after "add three points", `"$param.k == 5"`
  after "set k to 5".
- `{"input": {"prompt", "answer": expr, "tolerance": 0.01, "as", "right", "wrong"}}`:
  a number field; within the tolerance is right. `wrong` is the hint after a
  miss, `right` the feedback. The answer is an expression over game values,
  so it is right for whatever data the player has. Tolerance about half of
  the last shown digit.
- `{"action": ["a", "b"]}`: any of several player actions.
- `"next"`, `{"time": s}` and `{"choice": …}` as before.

### Highlights

```json
"highlight": [
  {"entity": "p3", "callout": "the outlier"},
  {"entities": ["centroid-1", "centroid-2"]},
  {"ui": "meter:loss", "callout": "watch this"}
]
```

- a list, or one object as in schema 1; `entities` rings several things
  (no callout on an `entities` item).
- `callout`: a short label with a pointer, **at most 40 characters**.
- **Framing**: when a highlighted thing is off screen or behind the bench,
  the camera turns to bring every highlighted entity into view; `"frame":
  false` on a highlight opts out. `"camera": {"frame": ["p3", "p7"]}` frames
  on purpose (instead of a fixed `target` and `distance`, for a shot about
  particular things).
- Off-screen arrows point at what is still out of view; UI targets open
  their folded panel (the bench) by themselves.
- Highlight ids: data points `p0`, `p1`, …, what the story spawned, and
  what the game's actions put on the stage (its `ENTITIES`; see "Game reads").

### Checking and playing a schema 2 story

`story.check_lab(story, game_root)` (part of `validate_story(..., lab=)`)
plays it through the game's own engine runner and lab machines:

- acts in order and parallel groups together, `as`, `set` and `$` reads;
- `when` and the branches;
- inputs answered with their `answer`'s value;
- `until` taken as the player's task;
- every choice option with a `goto` as its own playthrough.

It fails on:

- a refused act;
- a wait the player cannot do;
- a "–" in any shown text;
- an input answer that is not a number;
- a playthrough that does not finish.

`story.explore(story, game_root)` returns the playthroughs themselves, and
each snapshot has `shown` (the rendered cue, title, prompt, options and
feedback) and `vars`. `story.outline(pkg)` shows sections, act lists,
`parallel` groups, `as`, highlights with callouts, inputs and routes.

## Math: LaTeX between `$$…$$`

**Every formula a story shows is LaTeX between double dollar signs**, never
Unicode maths. The engine typesets it (MathJax, inline SVG) wherever text
appears (`~/games/engine/design/18_math.md`):

- the story card: `say`, `title`, the choice prompt, options and feedback,
  the input prompt and feedback, and callouts;
- the bench: the plaque (`ui.formula`), meter, slider and chart labels, and
  `ui.table` cells and headers, `ui.steps` lines and `ui.note`;
- stage labels.

```json
"say": "The slope is $$\\hat w = \\frac{\\sum (x_i-\\bar x)(y_i-\\bar y)}{\\sum (x_i-\\bar x)^2}$$."
```

- **In JSON, backslashes are doubled**: `\\hat`, `\\frac`, `\\sum`, `\\sqrt`.
- **Write as LaTeX, not as Unicode or slashes**:
  - `ŷ` → `\\hat y`, `x̄` → `\\bar x`, `Σ` → `\\sum`;
  - `√(…)` → `\\sqrt{…}`, `x²` → `x^2`, `xᵢ` → `x_i`;
  - `≤` → `\\le`, `·` → `\\cdot`, `‖w‖` → `\\lVert w \\rVert`;
  - a fraction written with `/` → `\\frac{a}{b}`;
  - a minus inside a formula is a plain `-`.
- **Plain words stay text**: only the maths goes inside `$$…$$`
  ("the slope $$w$$ grows", not `$$\\text{the slope } w$$`). Prose numbers
  ("3 points", "k is 5") stay plain text too.
- **Values inside a formula** use `\\val{expr}` (schema 2), with `:2` or `:%` as in
  `{…}`: `"$$\\hat w = \\val{fit.w:3}$$"`. Inside `$$…$$` braces belong to LaTeX,
  so `{expr}` works only outside formulas. Act args take the same:
  `{"action": "ui.steps", "args": {"lines": ["$$r_2 = y_2 - \\hat y_2 = \\val{r2:2}$$"]}}`.
- **Short formulas in cues.** The card is small on a phone: a cue holds a
  short formula. The derivation goes in `ui.steps`, one formula per line, and
  the formula being built on the plaque (`ui.formula`). A formula counts as
  half its LaTeX source toward the 140-character `say` limit and the
  40-character callout limit.
- **A `$$` that does not close is an error**, in text and in any act arg.
- **Bad LaTeX fails the end-to-end test**: it shows as red source in the
  game, and the run says which beat. In the visual review, a formula that
  renders too small or overflows the card on a phone counts as a finding.
- Formulas the game writes itself (explain lines, measure labels, parameter
  notes) are the game's. Quote them through reads; don't retype them.

## Writing rules

- **Visuals first.** The scene explains; the cue only names what the player is
  looking at. If a beat needs more than 140 characters, it is two beats or a
  missing animation.
- **One idea per beat.** Move the camera to it, show it, say it.
- **A choice every 3–5 beats**, each one choosing the algorithm's next step:
  what to measure, which way to move, when to stop. By the end, the player
  has assembled the whole algorithm from their own picks.
- **Wrong options are plausible misconceptions** the learner might really
  hold, never jokes, and their feedback is kind and short: what would happen,
  and why the right step works better. Every option has feedback. When the
  answer is wrong, the game shows its feedback and lets them pick again.
- **Show the consequence** right after a correct choice: the next beat runs
  the action the player chose.
- **Plain words.** Name the maths after it has been seen (`ui.formula` for a
  short formula on the plaque), never before.
- Use the mentor (`character/<mentor>` with `guide.*` actions) to point and
  react, sparingly: a cheer after a correct build, think while computing.
- `sfx` on reveals and results; music at `calm` while reading, `focus` when
  the player acts, `intensity` while training runs.
- **Finish with a sandbox hand-off**: the last beat runs `sandbox.open` with
  the tools and sliders the player has earned, and waits for an action.
- **Length: aim for at most 28 beats** (a soft limit: validation warns above
  it). A deep lesson that needs the room may run longer, up to the **hard
  limit of 100 beats** (an error above it). Keep a choice every 3–5 beats
  whatever the length.

## Levels

Every algorithm has three lessons, one per level, sharing the same
`algorithm` id, actions and sandbox. The package name says the level:
`<algorithm>` (easy), `<algorithm>-medium`, `<algorithm>-hard`; `write_story`
adds the `level:<level>` tag for medium and hard.

| Level | Teaches | How |
|---|---|---|
| easy | the idea, built from choices | the rules above |
| medium | the formulas and the mechanism, **computed on small real numbers** | 3–8 points on a 0.5 grid (`data.scatter` with `dataset: "custom"`), so every number can be checked by hand. Build each formula term by term: the symbolic form on the plaque (`ui.formula`), the worked arithmetic in `ui.steps`, per-point numbers in `ui.table`, charts in `ui.chart`; then let the live actions confirm them (`data.values`, `data.table`, `model.params`, `model.explain`). **Compute every number you state** with the game's own algorithm definitions and double-check it: the game recomputes them live, and a mismatch is a bug. Choices ask for the next term, the next number or the sign of a gradient. |
| hard | edge cases, limitations, failure modes and advanced topics, **each with a clear visual scenario** | 3–6 scenarios. Each one is set up visibly (`data.outlier`, `data.scale`, `data.shift`, `data.flipLabels`, `data.imbalance`, a dataset the method cannot fit), the failure shown (`ui.note` with `tone: "warn"`, a `warning-marker` at the spot), its cause explained, and the remedy tried and shown working, or shown to be missing. Choices ask the player to predict the failure or pick the remedy. |

**Clear the bench between scenes.** Panels stay until hidden. When a hard
lesson moves to a new scenario, or a medium one to a new formula, `ui.hide`
the earlier panels (`steps:<id>`, `table:<id>`, `chart:<id>`, `note`,
`explain`, `values`) and call `model.params` again with a title that fits the
new scene (or none), so nothing from the last scene lingers.

Write medium and hard for an expert reader: precise terms once they have
been shown, real numbers, no hand-waving, every claim visible on the floor.
`ui.note` text, like a cue, is at most 140 characters.

## The lab machine (what a beat may ask for)

Every station in the game is a **lab machine** (the engine's
`@games/engine/lab`): a set of states (gradient descent: `empty`, `ready`,
`rolling`, `out`), the inputs each state accepts, and what each input does
there. Story beats are inputs to that machine, exactly like the player's
tools (`tool:<id>`), bench buttons (`button:<id>`) and sliders
(`slider:<param>`), so the lesson, its sandbox and free play follow one set of
rules:

- **Every input stops what is still running** first (an animation jumps to
  its end, the rest of a multi-step run is dropped), unless the machine says
  it runs alongside. Nothing queues behind a roll.
- **A state refuses what makes no sense there**, with a reason the player
  sees ("Drop a ball first.", "The ball has left the surface: drop a new
  one."). A refused beat does **not** run in the game, so every beat after it
  plays over a scene that is not there. Never count on an action quietly
  setting things up (stepping a ball that was never dropped); make the beat
  that sets it up explicit.
- **Starting over is an input too.** To restart a scene, send the input that
  resets the lab (gradient descent: `gd.ball`, which stops the roll and puts
  a fresh ball down), rather than piling more steps on the old state.
- **`wait: {action}`** must be something the player can do in that state:
  the action itself, or a control whose steps run it (the drop tool runs
  `gd.ball`, ▶ Roll runs `gd.step`).
- **Inputs can carry random or player-given args**: a machine step may say
  `{"$random": "spot"}` (a new random place each time) or `{"$input": "at"}`
  (where the player clicked). Stories pass fixed args, so a lesson always
  plays the same way.

Read the station's machine before writing beats:
`story.lab_machine(algorithm, game_root)` returns it as Markdown. A station
without its own machine uses the default one, which accepts every action and
stops what is running first. `story.check_lab(story, game_root)` plays the
story through the game's real machine and handlers and lists every beat the
lab refuses, with the state it was in (`states` gives the state after each
beat); `validate_story(..., lab=game_root)` includes it.

## End-to-end test in the game (required for every story)

A story is not done until it has been **played in the game itself, in a
real browser, and passed**. The game's pull refuses a new or changed story
without a passing run for its exact `story.json` (and `e2e.json`). The test
is the engine's (`~/games/engine/design/17_story_e2e.md`); this skill runs it
from dex:

```python
r = story.e2e(pkg, "~/games/model-workshop")   # ~1 min per 40 beats; several can run at once
r["passed"], r["output"]                        # report: pkg/e2e/report.json, report.html, shots/
```

It starts the game's dev server on its own port with the story added in
memory (the game's files are never touched), plays every route in headless
Chromium at desktop and phone size, and checks every beat:

- no console or page errors, and no failed requests;
- no act the lab refused;
- every `{…}` has a value;
- every highlighted entity is on screen and not under the card or bench;
- every highlighted UI target is visible;
- the canvas renders;
- every route reaches the end.

### Test in segments, then once in full

A full run plays every route from the first beat, at both sizes. On a long
lesson with several remedial routes, that takes many minutes. Don't use it
as the loop you fix in. Work in segments:

1. **One section at a time.** While you build or fix a section, test the
   lesson only up to that section's last beat, at one size:

   ```python
   r = story.e2e_segment(pkg, "~/games/model-workshop", until="s2-fix")   # desktop; viewports="phone" for the phone
   ```

   It plays a cut copy, ending at that beat, with gotos past it dropped, in
   `e2e/segments/<beat>/`. A 10-beat segment takes about 15 s, against about
   45 s for the whole lesson.
2. **Batch your fixes.** Fix everything a run found, then run again. Don't
   run after each single change.
3. **Look only at what changed** while iterating: the screenshots of the
   beats you changed or that failed. They are in the segment's `e2e/shots/`.
4. **Once at the end, the full run**, both sizes, every route:
   `story.e2e(pkg, game_root)`. That report (`e2e/report.json`) is the one
   the game's pull and `done` read; a segment's report never counts. Then do
   the full screenshot review of every beat at both sizes.
5. **A run longer than one shell command**: start it with
   `story.e2e_start(pkg, game_root)` and call `story.e2e_wait(pkg)` (blocks
   up to 9 minutes) until `done`. Don't write your own sleep or polling
   loops around the tool.

### The story's own assertions: `e2e.json`

Every story package has an `e2e.json` next to `story.json`, with **real
assertions on what the lesson claims**:

```json
{
  "schema": 1,
  "beats": {
    "vote": {"expect": ["vote.cls == 1", "$data.count == 5", "$model.param.k == 3"],
             "text": ["Class 1 wins"], "visible": ["p3"], "ui": ["meter:loss"]},
    "add":  {"do": [{"action": "data.add", "args": {"at": [1, 2], "cls": 1}}]},
    "fix":  {"do": [{"control": "slider:lr", "value": 0.05}], "expect": ["$model.loss.mse < 0.5"]}
  },
  "routes": [{"name": "picks Manhattan", "choose": {"metric-q": 2}}],
  "inputs": {"r2": 0.8}
}
```

- **`expect`**: expressions over story variables and `$` reads that must be
  true once the beat has settled.
  - Assert the numbers the cues state, e.g. `"abs($model.param.w - 1.2) < 0.005"`.
  - Assert the outcome each beat is about (the winner, the cluster, the
    sign of the gradient).
  - At least one per beat that computes something.
- **`text`**: strings the card must show.
- **`visible`**: entity ids that must be on screen.
- **`ui`**: bench items that must be visible.
- **`do`**: what the player does on that beat: an `action` with `args`, or a
  `control` with a `value` or `at`.
  - **Every `wait.until` needs a `do` that makes it true.**
  - Awaited actions are played through the game's controls by themselves.
- **`routes`**: extra runs picking other options.
- **`inputs`**: what to type, when not the expected answer.

`story.e2e_skeleton(pkg)` writes a starting point: the beats that need an
`expect` or a `do`, marked TODO. `story.e2e_tests_problems(pkg)` lists what is
still missing.

### Visual review (required)

Open the screenshots (`pkg/e2e/shots/`, both viewports, every route) and
**look at every beat at both sizes**. Look for:

- things the cue talks about covered by the card or the bench;
- labels or panels overlapping until unreadable;
- a camera shot that misses what the cue is about;
- objects left over from an earlier scene;
- a highlight ringing the wrong thing.

Fix the story, run the test again, and look again. Record the review in
`pkg/e2e/review.md`: one line per beat that needed attention (what was wrong,
what you changed), ending with the line `reviewed: desktop, phone`.

### Done

`story.done(pkg, game_root)` is the definition of done, and must return `ok`:

1. `validate_story` is ok (format, the game's engine, its lab machines,
   every route);
2. `e2e.json` holds real assertions;
3. the end-to-end run passed for exactly this `story.json` and `e2e.json`;
4. `e2e/review.md` records the visual review.

`validate_story` alone warns "not done" while the run is missing or stale.

## Making a story package

1. Read the project brief's asset vocabulary and `story.actions()`. Only use
   assets that exist: `story.known_assets()` lists this project's packages
   (`story.known_assets(game_root)` adds what a game already has installed).
2. Build the story dict and `story.write_story(pkg, story_dict, name=...,
   title=..., tags=["algo:<algorithm>"])`. It writes `story.json` and
   `manifest.json` (kind `story`, always tagged `type:story` and `story`),
   refusing structural errors.
3. `story.validate_story(pkg, known=story.known_assets(game_root), lab=game_root)`
   (required for schema 2: the game's engine validates it)
   → `ok`, with `game_root` the game the project brief names: this also plays
   the story through the station's lab machine and refuses beats the lab
   would refuse. Read the `warnings` too (an asset listed and never used, a
   long stretch without a choice, no sandbox hand-off) and fix them unless
   there is a reason.
4. Write `e2e.json` (above), run `story.e2e(pkg, game_root)`, fix the story
   until it passes, review every screenshot at both sizes and write
   `e2e/review.md`; `story.done(pkg, game_root)` must be `ok`.
5. Write `story.outline(pkg)` to `previews/outline.md` and **read it end to
   end** as a learner would. That is this skill's preview: is each beat one
   idea, does every choice follow from what was just shown, is every wrong
   option a real misconception?
6. The validated, tested package is the deliverable. Games pull it from dex (see
   game-mesh, "How a package reaches a game"): the pull calls
   `story.export_story(pkg, game_root)`, which copies it to
   `<game_root>/public/assets/story/<name>/` and rebuilds the game's
   `public/assets/index.json` (via game-mesh's `catalog.rebuild_index`, so
   game-mesh must be enabled too). It refuses a story whose assets are not in
   that game's index or the project's packages, so a game pulls the models
   and sounds before the stories. A task exports only when its brief asks,
   with an explicit `game_root`.

An action the game does not have yet is not invented in a story: validation
refuses unknown names. If a lesson truly needs one, add it to
`utils/actions.json` in this skill (with `algorithm`, `summary`, `args`) and
say so in the task's final message, so the game can implement it.

## Game reads (the `$` paths of schema 2)

What Model Workshop answers (`~/games/model-workshop/src/reads.js`, `READS`;
`story.reads(game_root)` lists them live).

- `<pointId>` is `p0`, `p1`, …;
- `<entityId>` is a stage id (`centroid-1`, `svm:line`);
- `<action>` is an action name (`knn.vote`).

**Parameter rows are read by key.** A plain name (`w`, `b`, `k`) is its own
key. Other names are spelled out, with symbols as words joined by `_`:

- `∂L/∂w` → `dL_dw`, `L(w, b)` → `L_w_b`;
- `‖∇L‖` → `norm_grad_L`, `lr limit` → `lr_limit`;
- `λ1` → `lambda_1`, `σx|c` → `sigma_x_c`, `Σ hinge` → `sum_hinge`.

`$model.paramKeys` lists the current model's keys in table order: replay to
the beat with `story.simulate` to see them.

**Highlights may ring what the actions make** (the game's `ENTITIES` id
families: `ball`, `q`, `centroid-1`, `svm:line`, `halo:p3`, …), as well as
data points and what the story spawned. Ring only an id an earlier act has
actually made by that beat: the validator knows the families, but only the
end-to-end test sees what is on the stage ("highlights in view: … not
found"). Each action's catalogue entry names the ids it makes.

| path | what | example |
|---|---|---|
| `$data.count` | number of data points | `8` |
| `$data.count.<cls>` | number of points of class <cls> | `3` |
| `$data.labelled` | number of points that have a class | `8` |
| `$data.classes` | number of different classes among the points | `2` |
| `$data.<pointId>.x` | a point's x (pointId as in highlights: p0, p1, …) | `1.5` |
| `$data.<pointId>.z` | a point's z (the floor's second axis) | `-0.8` |
| `$data.<pointId>.y` | a point's y value (regression target, height), null if it has none | `2.4` |
| `$data.<pointId>.cls` | a point's class, null if unlabelled | `1` |
| `$model.name` | the current model, null before one is built | `"knn"` |
| `$model.param.<key>` | a row of the model's parameters (what model.params shows), by its key: the row's name when it is a plain name (w, b, k), else paramKey(name): Greek letters and symbols spelled out, the rest joined by _ ("∂L/∂w" → dL_dw, "L(w, b)" → L_w_b, "‖∇L‖" → norm_grad_L, "σx/c" → sigma_x_c, "λ1" → lambda_1, "lr limit" → lr_limit) | `3` |
| `$model.paramKeys` | the keys model.param.<key> takes for the current model, in table order | `["w", "b", "L_w_b", "dL_dw"]` |
| `$model.loss.<kind>` | the model's loss of that kind (mse, mae, zeroone, logloss, …) | `0.42` |
| `$model.predict.<pointId>` | the model's prediction for a point (a class, or a number) | `1` |
| `$model.prob.<pointId>` | the model's probability of class 1 for a point | `0.67` |
| `$model.cluster.<pointId>` | the cluster the model puts a point in | `2` |
| `$model.value.<pointId>` | the model's value (ŷ) at a point's (x, z) | `2.1` |
| `$param.<name>` | a bench slider or choice, as the player set it | `0.1` |
| `$stage.<entityId>.x` | a stage entity's station-local x (ids may have ':' and '-') | `0.4` |
| `$stage.<entityId>.y` | a stage entity's height | `0` |
| `$stage.<entityId>.z` | a stage entity's station-local z | `-1.2` |
| `$stage.<entityId>.exists` | true while the entity is on the stage | `true` |
| `$result.<action>` | what the action last returned (the story's or the player's run) | `{"cls": 1, "votes": {"0": 1, "1": 2}, "tie": false}` |
| `$result.<action>.<field…>` | a field of it; list items by index (result.knn.neighbors.0.id) | `1` |
| `$state.<domain>.<key…>` | raw lab state (ctx.state), the escape hatch: no promises it stays | `3` |
| `$lesson.level` | easy, medium or hard | `"easy"` |
| `$lesson.algorithm` | the station's algorithm id | `"knn"` |
| `$lesson.mode` | story or sandbox | `"story"` |

## The action catalogue

Actions that take text (`ui.formula`, `ui.steps`, `ui.table`, `ui.note`,
chart titles in `ui.chart`) accept `$$…$$` LaTeX in it, and `\\val{…}` / `{…}`
values in schema 2.

`utils/actions.json` holds the full list with argument descriptions; the
game reads the same file. `args` in a story should use those names.

**general**

- `guide.clip`(clip, loop) — Play a clip on the robot mentor.
- `guide.moveTo`(at) — Walk the mentor to a spot beside the station.
- `guide.pointAt`(entity) — Mentor turns and points at an entity.
- `ui.formula`(text) — Show a short formula on the station plaque.
- `ui.slider`(param, min, max, step, value) — Give the player a slider bound to a parameter.
- `ui.tool`(tool) — Enable a player tool.
- `ui.hide`(id) — Remove a slider, tool, the formula plaque or a bench panel.
- `sandbox.open`(algorithm, tools, sliders) — Hand the station over to free play.
- `ui.table`(id, title, columns, rows) — Show a table of numbers on the bench (the story's own numbers).
- `ui.steps`(id, title, lines, append) — Show worked arithmetic, one line per step.
- `ui.note`(text, tone) — A callout on the bench.
- `ui.chart`(id, title, kind, labels, values, series, mark) — A chart drawn from the story's numbers.
- `model.params`(title) — Show the current model's parameters, live.
- `model.explain`(at, id) — The current model's calculation for one point, with its numbers.
- `measure.distance`(from, to, metric, id, show) — A ruler or brace between two things, labelled with the distance.

**data**

- `data.scatter`(dataset, n, classes, noise, seed, points) — Spawn a dataset as class markers on the grid.
  Custom `points` are rows `[x, z, class, y]` (class defaults to 0, `y` the height for regression, optional), never objects; the game names them `p0`, `p1`, … in order, and those are the ids `model.explain`, `measure.distance`, `data.values`, `data.flipLabels` and `highlight {entity}` take.
- `data.label`(show, ids) — Reveal or hide class colours/shapes on points.
- `data.add`(at, cls, id) — Add one point.
- `data.highlight`(ids, cls, where) — Pulse a subset of points.
- `data.feature`(axis, name) — Name an axis after a feature.
- `data.normalize` — Animate rescaling features to zero mean, unit spread.
- `data.noise`(amount) — Jitter the points.
- `data.height` — Lift each point to its target value (regression view).
- `data.clear` — Remove all points.
- `axes.show`(x, z, y, range) — Show axes, grid and ticks.
- `axes.hide` — Hide the axes.
- `data.values`(show, fields, ids, digits) — Label points with their live numbers. T1, t2: each point's coordinates on PC1 and PC2, from the PCA model
- `data.table`(fields, ids, title) — A live table of points and their numbers on the bench. T1, t2 columns, as for data.values
- `data.outlier`(at, cls, y, id) — Add one outlier, marked with the warning marker.
- `data.scale`(axis, factor) — Stretch one feature (animated): unequal feature scales.
- `data.shift`(dx, dz, cls) — Move all points, or one class: distribution shift.
- `data.duplicate`(ids, times) — Add copies of points.
- `data.imbalance`(cls, keep) — Keep only some points of a class.
- `data.flipLabels`(fraction, seed, ids) — Flip labels (label noise); flipped points are marked.

**foundations**

- `loss.show`(kind) — Show the loss meter for the current model.
- `foundations.step`(lr, loss, steps) — One (or more) real gradient steps on the line rule f = w1·x + w2·z + b under the chosen loss of y·f; the working (gradient terms, w ← w − η·∂L/∂w, loss and mistakes before → after) goes on the bench and the line slides. With 'zeroone' the gradient is 0, nothing moves, and the bench and a floor label say why

**gradient-descent**

- `loss.surface`(params, shape) — Show the loss landscape as a surface over two parameters.
- `gd.ball`(at) — Drop the parameter marker on the loss surface.
- `gd.gradient` — Draw the gradient arrow at the marker.
- `gd.step`(lr, steps) — Take gradient steps; the marker rolls downhill.
- `gd.setRate`(lr) — Change the learning rate.
- `gd.momentum`(beta) — Turn momentum on.
- `gd.stochastic`(batch) — Use mini-batches: noisy steps.
- `gd.rescale`(axis, factor) — Rescales one input so the loss surface reshapes (e.g. the ravine rounds into a bowl), animated. The ball keeps the same model (w becomes w/s), its trail is squeezed along that axis, and a bench panel shows the coefficients, curvatures λ, κ and the lr limit 2/λmax before and after.
- `gd.restarts`(at, lr, steps, clear) — Up to 6 coloured balls rolled at once from the given starts, each with its own trail (full-batch gradient, current momentum). Each ends in the minimum its start leads to; balls that reach the lowest loss glow and are labelled 'lowest loss', the others 'stuck at loss …' or 'flew out'. A bench table lists start, end and loss.
- `gd.decay`(from, to, steps, schedule) — Rolls the ball with a learning rate that decays from `from` to `to` over `steps` (momentum and mini-batches as set), with the trail and the loss chart as in gd.step, plus a bench chart of the lr per step.

**linear-regression**

- `regression.line`(slope, intercept) — Place a line with a given slope and intercept.
- `regression.showResiduals`(show) — Draw residual segments from points to the line.
- `regression.squareResiduals`(show) — Grow each residual into a square (squared error).
- `regression.fitStep`(lr, steps) — Gradient step(s) on the line.
- `regression.solve` — Jump to the least-squares line.
- `regression.poly`(degree) — Fit a polynomial curve.
- `regression.plane` — Fit a plane over two features.
- `regression.robust`(loss, delta, labels) — Refits the line under a chosen loss: least squares, exact least absolute deviations, or Huber by reweighted least squares. The earlier line stays as a grey ghost, the line moves to the new fit, each point is labelled with its loss, and a bench bar chart shows every point's loss with the largest marked.
- `regression.weighted`(weights, edges, ids, w, rest, labels) — Weighted least squares. Each point gets a halo sized by its weight and a '×w' label, the earlier line stays as a grey ghost, and the line moves to the solution of the weighted normal equations, which params/explain show with their numbers.

**logistic-regression**

- `logistic.sigmoid`(show) — Show the sigmoid squashing scores to 0–1.
- `logistic.setParams`(w, w1, w2, b) — Sets the model to a chosen guess without fitting; later logistic actions and fitStep work from it
- `logistic.probability` — Colour points by predicted probability.
- `logistic.fitStep`(lr, steps) — Gradient step(s) on log-loss.
- `logistic.threshold`(value) — Move the decision threshold.
- `logistic.boundary`(show) — Show the decision boundary.
- `logistic.penalty`(lambda, kind, steps, lr) — Adds a weight penalty to the mean log-loss (L2 ½λ‖w‖² or L1 λ‖w‖₁, b never penalised) and trains under it from the current model; the S-curve or wall slides as the weights shrink, with a ‖w‖ meter, a ‖w‖-by-step chart and a weights bar chart
- `logistic.features`(extra, steps, lr) — Adds polynomial features with their own weights (starting at 0) and trains, so the boundary curves; on the floor the p = threshold contour is drawn as glowing segments (labelled 'circle r = …' when it is a circle), on a row one cut wall per crossing

**knn**

- `knn.query`(at) — Place the query star.
- `knn.distances`(metric) — Draw distance lines and rings from the query (circles for Euclidean, a diamond for Manhattan, a square for Chebyshev).
- `knn.neighbors`(k) — Highlight the k nearest points.
- `knn.vote`(weighted, power) — The k nearest vote; the star takes the winner's colour. With weighted: true each neighbour's weight 1/d^power floats over it, the bench shows the weight per class as bars and a table, and the model votes this way until a plain knn.vote.
- `knn.setK`(k) — Change k.
- `knn.regions`(show) — Paint decision regions over the grid.
- `knn.concentration`(dims) — Show distances bunching up as dimensions grow (curse of dimensionality).
- `knn.metric`(metric) — Switches the distance without drawing new lines: halos and the ring<k> (circle, diamond or square), the vote, any distance lines and rings already shown, the regions, predict and explain all follow.

**kmeans**

- `kmeans.init`(k, method, centroids) — Drop k centroids.
- `kmeans.assign` — Each point joins its nearest centroid.
- `kmeans.update` — Each centroid moves to the mean of its points.
- `kmeans.iterate`(steps) — Repeat assign and update.
- `kmeans.inertia` — Show total within-cluster distance.
- `kmeans.elbow`(kmax) — Plot inertia against k.
- `kmeans.seedOdds`(first) — k-means++ seeding made live: the chosen centroids drop in; every point gets a tag 'D² … · P …' and a ring sized by P = D²/ΣD²; the bench table lists D² and P with the likeliest marked and ΣD² underneath.
- `kmeans.seedPick`(id, seed) — Adds the next k-means++ centroid, either a given point or drawn with P ∝ D² exactly as the game's k-means++ draws it, then redraws the odds for the new set.

**hierarchical-clustering**

- `cluster.distances`(linkage) — Show distances between clusters.
- `cluster.merge`(steps, linkage) — Merge the two closest clusters; the dendrogram grows a link.
- `cluster.dendrogram`(show) — Show the dendrogram beside the data.
- `cluster.cut`(height) — Cut the dendrogram at a height.
- `cluster.matrix`(linkage) — A live table on the bench of the distance between every two current clusters under the linkage. The smallest entry (the next merge) is marked in both symmetric cells, and a foot line names it. It follows every cluster.merge, linkage change and point change.

**decision-tree**

- `tree.root` — Start a tree: every point in one node.
- `tree.impurity`(node, measure) — Show a node's impurity bar.
- `tree.split`(node, feature, threshold) — Split a node with a plane on one feature.
- `tree.bestSplit`(node) — Sweep thresholds and keep the purest split.
- `tree.grow`(depth) — Grow greedily to a depth.
- `tree.leaf`(node) — Close a node as a leaf with its majority class.
- `tree.prune`(depth) — Prune back to a depth.
- `tree.predict`(at) — Drop a query down the tree.

**random-forest**

- `forest.bag`(tree, seed) — Draw a bootstrap sample (with replacement).
- `forest.featureSubset`(m) — Restrict a tree to a random subset of features.
- `forest.grow`(trees, depth) — Grow several trees side by side.
- `forest.vote`(at) — The trees vote on a query.
- `forest.regions`(show) — Show the averaged decision regions.

**gradient-boosting**

- `boost.init` — Start from a constant prediction.
- `boost.residuals`(show) — Show the residuals of the current ensemble.
- `boost.addStump`(lr, steps) — Fit a stump to the residuals and add it.
- `boost.weights`(round) — AdaBoost: each seed's marker grows with its weight. With round: the weights after that round, a weight tag on each seed, and the round's reweighting on the bench: the stump, the seeds it gets wrong, ε, α = ½ln((1−ε)/ε), w·e^(∓α), Z and the weights after.
- `boost.shrink`(lr) — Change the learning rate.
- `boost.loss`(kind, delta, show) — Refits the gradient-boosting stumps so far under squared, absolute or Huber loss. Each point gets a tag with its plain residual r and the pseudo-residual g = −∂L/∂F that the next stump fits. A bench table lists r, g and each point's loss, and the point with the biggest share is marked.
- `ada.candidates`(round, top) — Every stump AdaBoost tries in one round, on the bench, best first: the wall's rule, its weighted error ε and the α it would get. The best is drawn on the floor as a highlighted wall labelled with its ε.

**svm**

- `svm.line`(angle, offset) — Place a separating line.
- `svm.margin`(show) — Show the margin bands.
- `svm.supportVectors` — Halo the support vectors.
- `svm.maximize` — Widen the margin to its maximum.
- `svm.softMargin`(C) — Allow violations with penalty C.
- `svm.kernelLift`(kernel, gamma) — Lift points into 3D with a kernel feature.
- `svm.kernelProject` — Cut the lifted data with a plane; drop the curve back to 2D.

**naive-bayes**

- `bayes.prior` — Show class priors as bars.
- `bayes.gaussian`(cls, feature) — Draw a class's bell curve over a feature.
- `bayes.likelihood`(at) — Read the likelihoods at a query.
- `bayes.posterior`(at) — Multiply prior and likelihoods; normalise.
- `bayes.independence` — Show features treated separately (the naive assumption).
- `bayes.regions`(show) — Paint the posterior's decision regions.
- `bayes.logPosterior`(at) — The posterior at a point in log space: per class ln P(c) + ln p(x|c) + ln p(z|c) as worked steps, the largest score m, Σ e^(s − m), log-sum-exp and P(c|x) = e^(s − lse); log-score labels by the star, posterior bars that never underflow to 50/50, and a bench chart of e^(s − m)
- `bayes.calibrate`(T) — Temperature scaling P(c|x) ∝ e^(ln joint / T): a reliability table (6 confidence bins: count, mean p and share right, before and after) with log-loss, claims ≥ 0.99, boldest miss and 'calls unchanged' lines underneath; the model's prob, log-loss, posterior bars and explanation use T from then on

**pca**

- `pca.center` — Move the cloud's centre to the origin.
- `pca.covariance` — Show the spread ellipse.
- `pca.axis`(component) — Draw a principal axis.
- `pca.rotate`(angle) — Spin a candidate axis; show the projected variance.
- `pca.project`(components) — Project points onto the top components.
- `pca.variance` — Show explained-variance bars.
- `pca.reconstruct` — Lift projections back; show what is lost.

**neural-network**

- `nn.perceptron`(inputs) — Build a single neuron with inputs and a bias.
- `nn.layer`(neurons, activation, init, value) — Add a hidden layer (up to 6). Init 'constant' starts every weight at value and every bias at 0, so each layer's neurons are copies. A bench note, model.params and model.explain show they stay identical, with identical gradients, through training.
- `nn.weights`(show) — Show weights as connection thickness and colour.
- `nn.forward`(input) — Send an input through; neurons glow with activation.
- `nn.activation`(fn) — Swap the activation function.
- `nn.loss` — Compare the output with the target.
- `nn.backward` — Pulse gradients backward along the connections.
- `nn.update`(lr) — Nudge the weights by their gradients.
- `nn.boundary`(show) — Show the network's decision region.
- `nn.gradients` — Gradient size per layer: vanishing or exploding gradients.

**evaluation**

- `train.step`(steps) — Run training steps for the current model.
- `train.run`(epochs) — Train for some epochs, animated.
- `train.pause` — Pause training.
- `train.curve`(series) — Show learning curves.
- `eval.split`(train, val, test, seed, stratify) — Splits the points into train / validation / test, with a coloured ring per point and a count chart. seed picks the shuffle (same seed, same split). stratify keeps each class's share in every group. Returns the ids per group and counts per class.
- `eval.score`(metric, on) — Show a metric.
- `eval.confusion` — Show the confusion matrix.
- `eval.crossValidate`(folds, seed, keep) — k folds take turns as validation; a bar per fold and the mean. With keep, each fold's fitted curve (regression) or score (classification) stays on the floor in its own colour, with mean ± sd
- `eval.complexity`(value) — Set model complexity (degree, depth, k).
- `eval.regularize`(strength) — Add a penalty on large weights.
- `eval.roc` — ROC curve and AUC of the current classifier.
- `eval.threshold`(value) — Move the decision threshold; confusion matrix and metrics follow. With the logistic model it also moves the model's own threshold (clamped to 0.01–0.99) and its wall or contour.
- `eval.calibration`(bins, table) — Reliability diagram: the scores in equal-width bins, each bin's mean predicted probability against its observed share of class 1, with the diagonal of perfect calibration and the expected calibration error (ECE) on a meter; a 3D board on the back wall's left half and a bin table
