---
name: game-story
description: Lesson scripts ("stories") for the games in ~/games — the schema-1 story format the engine's story runner plays, the writing rules, the catalogue of act actions per algorithm, and write/validate/install of kind "story" packages.
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

## The format (schema 1 — the engine implements exactly this)

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
| `highlight` | `{entity: id}` (something in the scene) or `{ui: selector}`. |
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

Write medium and hard for an expert reader: precise terms once they have
been shown, real numbers, no hand-waving, every claim visible on the floor.
`ui.note` text, like a cue, is at most 140 characters.

## Making a story package

1. Read the project brief's asset vocabulary and `story.actions()`. Only use
   assets that exist: `story.known_assets()` lists this project's packages
   (`story.known_assets(game_root)` adds what a game already has installed).
2. Build the story dict and `story.write_story(pkg, story_dict, name=...,
   title=..., tags=["algo:<algorithm>"])`. It writes `story.json` and
   `manifest.json` (kind `story`, always tagged `type:story` and `story`),
   refusing structural errors.
3. `story.validate_story(pkg, known=story.known_assets())` → `ok`. Read the `warnings` too (an
   asset listed and never used, a long stretch without a choice, no sandbox
   hand-off) and fix them unless there is a reason.
4. Write `story.outline(pkg)` to `previews/outline.md` and **read it end to
   end** as a learner would. That is this skill's preview: is each beat one
   idea, does every choice follow from what was just shown, is every wrong
   option a real misconception?
5. The validated package is the deliverable. Games pull it from dex (see
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

## The action catalogue

`utils/actions.json` holds the full list with argument descriptions; the
game reads the same file. `args` in a story should use those names.

**general**

- `guide.clip`(clip, loop) — Play a clip on the robot mentor.
- `guide.moveTo`(at) — Walk the mentor to a spot beside the station.
- `guide.pointAt`(entity) — Mentor turns and points at an entity.
- `ui.formula`(text) — Show a short formula on the station plaque.
- `ui.slider`(param, min, max, step, value) — Give the player a slider bound to a parameter.
- `ui.tool`(tool) — Enable a player tool.
- `ui.hide`(id) — Remove a slider, tool or the formula plaque.
- `sandbox.open`(algorithm, tools, sliders) — Hand the station over to free play.
- `ui.table`(id, title, columns, rows) — Show a table of numbers on the bench (the story's own numbers).
- `ui.steps`(id, title, lines, append) — Show worked arithmetic, one line per step.
- `ui.note`(text, tone) — A callout on the bench.
- `ui.chart`(id, title, kind, labels, values, series) — A chart drawn from the story's numbers.
- `model.params`(title) — Show the current model's parameters, live.
- `model.explain`(at, id) — The current model's calculation for one point, with its numbers.
- `measure.distance`(from, to, metric) — A ruler or brace between two things, labelled with the distance.

**data**

- `data.scatter`(dataset, n, classes, noise, seed, points) — Spawn a dataset as class markers on the grid.
  Custom `points` are rows `[x, z, class, y]` (class defaults to 0, `y` the height for regression, optional), never objects; the game names them `p0`, `p1`, … in order, and those are the ids `model.explain`, `measure.distance`, `data.values` and `data.flipLabels` take.
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
- `data.values`(show, fields, ids, digits) — Label points with their live numbers.
- `data.table`(fields, ids, title) — A live table of points and their numbers on the bench.
- `data.outlier`(at, cls, y, id) — Add one outlier, marked with the warning marker.
- `data.scale`(axis, factor) — Stretch one feature (animated): unequal feature scales.
- `data.shift`(dx, dz, cls) — Move all points, or one class: distribution shift.
- `data.duplicate`(ids, times) — Add copies of points.
- `data.imbalance`(cls, keep) — Keep only some points of a class.
- `data.flipLabels`(fraction, seed, ids) — Flip labels (label noise); flipped points are marked.

**foundations**

- `loss.show`(kind) — Show the loss meter for the current model.

**gradient-descent**

- `loss.surface`(params, shape) — Show the loss landscape as a surface over two parameters.
- `gd.ball`(at) — Drop the parameter marker on the loss surface.
- `gd.gradient` — Draw the gradient arrow at the marker.
- `gd.step`(lr, steps) — Take gradient steps; the marker rolls downhill.
- `gd.setRate`(lr) — Change the learning rate.
- `gd.momentum`(beta) — Turn momentum on.
- `gd.stochastic`(batch) — Use mini-batches: noisy steps.

**linear-regression**

- `regression.line`(slope, intercept) — Place a line with a given slope and intercept.
- `regression.showResiduals`(show) — Draw residual segments from points to the line.
- `regression.squareResiduals`(show) — Grow each residual into a square (squared error).
- `regression.fitStep`(lr, steps) — Gradient step(s) on the line.
- `regression.solve` — Jump to the least-squares line.
- `regression.poly`(degree) — Fit a polynomial curve.
- `regression.plane` — Fit a plane over two features.

**logistic-regression**

- `logistic.sigmoid`(show) — Show the sigmoid squashing scores to 0–1.
- `logistic.probability` — Colour points by predicted probability.
- `logistic.fitStep`(lr, steps) — Gradient step(s) on log-loss.
- `logistic.threshold`(value) — Move the decision threshold.
- `logistic.boundary`(show) — Show the decision boundary.

**knn**

- `knn.query`(at) — Place the query star.
- `knn.distances`(metric) — Draw distance lines and rings from the query.
- `knn.neighbors`(k) — Highlight the k nearest points.
- `knn.vote` — Neighbours vote; the query takes the winning class.
- `knn.setK`(k) — Change k.
- `knn.regions`(show) — Paint decision regions over the grid.
- `knn.concentration`(dims) — Show distances bunching up as dimensions grow (curse of dimensionality).

**kmeans**

- `kmeans.init`(k, method) — Drop k centroids.
- `kmeans.assign` — Each point joins its nearest centroid.
- `kmeans.update` — Each centroid moves to the mean of its points.
- `kmeans.iterate`(steps) — Repeat assign and update.
- `kmeans.inertia` — Show total within-cluster distance.
- `kmeans.elbow`(kmax) — Plot inertia against k.

**hierarchical-clustering**

- `cluster.distances`(linkage) — Show distances between clusters.
- `cluster.merge`(steps, linkage) — Merge the two closest clusters; the dendrogram grows a link.
- `cluster.dendrogram`(show) — Show the dendrogram beside the data.
- `cluster.cut`(height) — Cut the dendrogram at a height.

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
- `boost.weights` — Grow misclassified points (AdaBoost view).
- `boost.shrink`(lr) — Change the learning rate.

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
- `nn.layer`(neurons, activation, init) — Add a hidden layer (up to 6). `init` scales the starting weights relative to Xavier: `large` ×4, `small` ×0.1, `xavier` ×1, or a number.
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
- `eval.split`(train, val, test) — Split the data into train/validation/test.
- `eval.score`(metric, on) — Show a metric.
- `eval.confusion` — Show the confusion matrix.
- `eval.crossValidate`(folds) — Rotate folds; show per-fold scores.
- `eval.complexity`(value) — Set model complexity (degree, depth, k).
- `eval.regularize`(strength) — Add a penalty on large weights.
- `eval.roc` — ROC curve and AUC of the current classifier.
- `eval.threshold`(value) — Move the decision threshold; confusion matrix and metrics follow.
