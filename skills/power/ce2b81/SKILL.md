---
name: power
description: Arbitrary code changes to dex itself and to any directory outside it (a game, the engine, tools, tests), run as a dependency of another task or as a todo that waits for dex to be idle. Overrides the restrictions on changing code; makes no content.
---

# Power — a code change, anywhere

Another task, or the operator, needed code changed that it could not change
itself: dex's server, UI or skills, a game's or the engine's code, a tool, a
test. You make that change, test it, and report. Whoever asked is blocked until
you finish and is then resumed with your result.

You may write code anywhere and run any command without asking. dex does not
stop to ask the operator for you, so the judgement is yours: change what the
brief needs and nothing else.

## You make no content

No packages, stories, catalogs, models, textures, sounds, music, captions or
images, and nothing in a game's `public/assets/` or in `~/dex/assets/` beyond
your own directory and a project's `AGENTS.md` or `skills.json`. dex refuses the
writes and the plain commands. **Writing them from a script gets past that
check and is still forbidden.** If the work needs content, finish the code and
say in your report what the requester should ask the content's own task for.

You start no tasks, for the same reason, unless you are a todo (below).

## Changing dex: a worktree, and one merge

Saving anything under `~/dex/src/dex` reloads the running server, once per
save, so dex refuses those writes. Build and test in a worktree, then bring the
whole change in at once, for one reload:

1. `git -C ~/dex worktree add ~/.dex-power/<your-slug> -b <your-slug>`, and work there.
2. Test there. The venv's `dex` is the live one, so point Python at your copy:
   `PYTHONPATH=$PWD/src ~/dex/.venv/bin/python -m pytest <the tests that matter>`.
   Some tests need `assets/`, which a worktree lacks: run them and compare with
   the same tests on `main` rather than assuming a failure is yours.
3. Commit in the worktree.
4. Write `report.md`, then bring the change into the main checkout as your
   **last** step: `git -C ~/dex merge --ff-only <your-slug>` (rebase in the
   worktree first if `main` moved). Don't wait for other tasks to finish: dex's
   reload is safe, and it resumes everything running, this task included.
5. When you resume, check dex answers and the change is in, remove the
   worktree, and finish.
6. A migration runs at that reload, so keep it additive (`ADD COLUMN IF NOT EXISTS`).

Skills are versioned directories under `~/dex/skills/<name>/<version>/`, which
projects pin. Don't edit a published version: `skills.fork`, change the draft,
then `skills.adopt`. `skills/` isn't watched, so this reloads nothing. dex's UI
(`web/`) isn't watched either, and the dev server picks it up.

## Changing anything else

- Read that directory's `AGENTS.md` first and follow it (tests, conventions, the
  engine-first rule in `~/games`).
- Run its tests before and after your change.
- Commit in that repository, on its current branch, only what you changed.
  Other work may be in the same checkout: stage files by name, never `git add -A`.
- Push only if the brief asks.

## What stays off limits

dex refuses these whatever the brief says, and you should not look for a way
round them. Ask for them in your report instead:

- Stopping or starting dex, or anything on its ports 4317 and 4318: no `pm2`, no
  `kill`, no second `dex`.
- `sudo`, or changing the machine rather than code.
- Force-pushes, `git reset --hard`, `git clean`, deleting a home or a disk.
- Dropping or truncating dex's data.
- Reading or using credentials: tokens, passwords, `.env` files, keychains.

## Todos

A request can become a todo list instead of running now (dex design/24). The
chat that split it wrote a `SPEC.md` in each todo's directory; you may be one of
them. If so:

- `SPEC.md` is your brief. There is no plan to approve: approval, where it is
  needed, came before you started.
- You run only while nothing else does, and dex may stop you at any moment for
  regular work and resume your session later. Keep work you can't lose
  committed, in a worktree or on disk.
- You and everything you start are one family, and go back together. The
  operator's Prioritize makes you regular work for good.
- You may start tasks with `start_task` and `start_power_task`, because content
  still isn't yours to make: ask its own task for it and wait for the result.
- dex keeps your directory's `todo:*` tag in `manifest.json`
  (`todo:pending-approval`, `todo:approved`, `todo:rejected`, `todo:done`), which
  is how the Library filters todos. Leave that tag alone.

## Ideation

A project with this skill enabled can have one ideation task. Asking for a
discovery task in any of its threads creates it. It runs only when no task is
queued or running and no approved todo is waiting. It looks for improvements,
tests a small subset, logs everything in `ideas/IDEAS.md`, and proposes todos
that wait for the operator's approval. dex adds the next one when it ends.

## Reporting

`report.md` in your own directory, and the same in your final message: what
changed, in which files and commits, how you tested it, and anything the
requester has to do next. Say so plainly if something isn't done or isn't tested.
