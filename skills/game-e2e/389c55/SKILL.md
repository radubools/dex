---
name: game-e2e
description: End-to-end tests of the lessons ("stories") of the games in ~/games — plays a story package in its game in a real browser (headless Chromium, desktop and phone, every route) with the engine's story-e2e.mjs, checks every beat and its e2e.json assertions, reviews every beat on a few contact sheets, and hands the story task a passing, reviewed report it adopts. Story tasks never run the test themselves; they start a game-e2e task as a dependency in the End-to-end tests thread.
---

# Game e2e — a lesson played in its game

A story is not done until it has been **played in the game itself, in a real
browser, and passed**. The game's pull refuses a new or changed story without
a passing report for its exact `story.json` and `e2e.json`
(`~/games/engine/design/17_story_e2e.md`). You run that test, for a story task
that asked for it. You are a dependency of that task: it is blocked until you
finish, and your final message is what it reads.

## What you are given

The brief names:

- the **story package** (its directory, `~/dex/assets/<project>/story-<name>/`),
- the **game** (`~/games/<game>`),
- what to run: the **full** run (the default), or a **segment** up to a beat
  while the story is still being fixed,
- anything the story task wants looked at.

## What you never do

- **Never edit the story.** Not `story.json`, not `e2e.json`, nothing in its
  package: it is another task's, and dex lets you read it, not write it. You
  report what is wrong, with the beat and the size; the story task fixes it
  and continues you to run again.
- **Never install anything into the game.** The story task pulls what the
  lesson needs (models, its reading, its captions) before it asks you.
- Never pick a port or pass `--port`: each run's dev server gets one leased
  from dex.

## Running it

Everything you make goes in **your own package**: `out = TASK_DIR / "e2e"`.

```python
from utils import e2e_run
r = e2e_run.run(story_pkg, game_root, out)        # full: every route, desktop and phone, in parallel
r["passed"], r["output"]                          # out/report.json; out/<size>/report.html and shots/
```

- **A segment**, when the brief asks for one (fast feedback while a section
  is being fixed; never the report that counts):
  `e2e_run.segment(story_pkg, game_root, until="s2-fix", out=out)`; one size
  by default (`viewports=("phone",)` for the phone). It plays a cut copy in
  `out/segments/<beat>/`.
- **A run longer than one shell command**: `e2e_run.start(story_pkg,
  game_root, out)`, then `e2e_run.wait(out)` (blocks up to 9 minutes) until
  `done`. No sleep or polling loops of your own.
- About a minute per 40 beats per size. Several runs at once are safe.

It starts the game's dev server on its own port with the story added in
memory (the game's files are never touched), plays every route in headless
Chromium at desktop and phone size, and checks every beat: no console, page
or request errors; no act the lab refused; every `{…}` has a value; every
highlighted entity on screen and not under the card or bench; every
highlighted UI target visible; the canvas renders; every route reaches the
end; and the story's own `e2e.json` assertions (`expect`, `text`, `visible`,
`ui`, with its `do`, `routes` and `inputs`).

If the story has no `e2e.json`, or `story.e2e_tests_problems(pkg)` lists
missing assertions, say so in your final message: writing them is the story
task's job.

## The visual review (required)

**Read sheets, never single screenshots.** A run makes hundreds of shots
(every beat, both sizes, the card's two faces, every route), and a model takes
only so many images in one request: the local Qwen on Splash refuses more
than **64**, and the request carries your whole session, every image of this
attempt and of **every earlier attempt** (a run again resumes the same
session). Over the limit, the task can never run again. So:

```python
from utils import e2e_review
r = e2e_review.sheets(out)      # out/review-sheets/sheet-<n>.jpg, and sheets.json: what each holds
```

It puts every beat of the main route on a few sheets, desktop and phone and
the card's "more" face side by side, and adds a cell only where another route
looks different, the routes that look alike sharing one. A cell whose beat
failed a check is red and names it. Usually 4 to 8 sheets.

- **Budget: at most 12 images an attempt, 40 over the whole task.** Count
  every image you open. Open each sheet once; when a cell needs a closer
  look, open that one shot, and it counts.
- When the sheets run past the budget, read them in this order: failed
  beats, beats the story task changed, the main route, then the others; say
  in the review which sheets you did not read.

On the sheets, look at every beat at both sizes for:

- what the cue talks about covered by the card or the bench;
- labels or panels overlapping until unreadable;
- a camera shot that misses what the cue is about;
- objects left over from an earlier scene;
- a highlight ringing the wrong thing;
- a stand-in drawn where an asset is missing (say which).

Write `out/review.md`: one line per beat that needs attention, `- <beat id>
(<size>): what is wrong, and what would fix it`, ending with the line
`reviewed: desktop, phone`. A review with nothing to fix is just that line.

Then `e2e_run.clear_shots(out)`: the screenshots exist only for the review
(a few hundred MB per run); the reports and the review stay.

## Your final message

The story task acts on it, so it stands on its own:

1. **passed** or **failed**, and whether it was the full run or a segment;
2. `e2e_run.problem(story_pkg, out)`: `None` means the report is good for the
   story as it is now;
3. each failure: beat id, size, route, the check that failed and its detail;
4. the review's findings, beat by beat;
5. **the report directory** (`out`, its absolute path): the story task adopts
   it from there with `e2e_run.adopt(story_pkg, out)`.

## Run again

When the story task has fixed what you found, it continues you
(`continue_task`) to run again, in the same package: run the full test again
(or the segment it names), and review only the beats it changed and the ones
that failed, with `e2e_review.sheets(out, only=[beat ids])`: your session
still holds every image you opened before, and they count against the 40.
Rewrite `review.md` for this run.
