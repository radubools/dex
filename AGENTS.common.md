# How work is done here

These instructions are the same in every project: the environment, the
operator's source material, how a project's viewers work, and how a helper
becomes shared.

**What this particular project produces is in its own guide, which follows this
one.** What it can *do* is in the skills listed in your brief — read a skill's
`SKILL.md` before you use it.

This used to be copied into every project's `AGENTS.md`, which meant four
copies drifting apart: 289 lines were identical across all four, and a fix to
one of them reached nobody else. It is read directly now.

## The environment is ready — do not go looking

Everything below is installed and working. **Do not check whether a tool exists,
do not try to install anything, and do not add a fallback for a missing
dependency.**

| Need | Use |
|---|---|
| Anything Python | `{python}` — the interpreter named in your brief |
| Build a widget | `node {workspace}/widgets/build.mjs <name>` — writes its `dist/index.js` |
| Test a widget | `{workspace}/node_modules/.bin/playwright test --config {workspace}/widgets/playwright.config.ts` — Chromium is installed |

Your shell runs one command at a time: no `&&`, no pipes, no `cd`. Pass absolute
paths.

## Your project's data directory

`{workspace}/datasets/{project_slug}` holds this project's **source material** — files the operator
attached to a request, a corpus, a PDF to work from — and it is **yours to read
and write**. Build an index, cache an extraction, write a scratch database
beside the sources: that is what it is for, and no task needs to ask.

It is not your package. Keep the two apart:

- **`datasets/`** is what the work is *made from*. Nothing generated as a
  deliverable belongs here.
- **your task directory** is what the work *produces*. Sources do not belong
  here either — read them where they are rather than copying them in, unless
  your brief says to.

Leave a file the operator put there as you found it unless the brief asks you
to change it. Another project's data directory is not yours.

## Viewers for this project's files

dex shows a `.md` file as rendered markdown and anything else as syntax-
highlighted code. That is the floor, not the ceiling: a project whose output is
a 3D figure, a narrated video, a circuit, a score, needs its own viewer, and you
can write one.

### Where they come from

**Ask for one in this project's design chat.** That conversation is a task with
a wider reach than yours: it may write into `widgets/` at the top level and into
this project's `widgets.json`, build the bundle, and check that it loads. You
cannot — a generation task writes inside its own package and nothing else.

So if a file you produce would be far better *seen* than read, say so in your
summary — name the file and what a viewer should show — and the operator can ask
the design chat for it. Do not build a viewer inside your package instead: a
one-off HTML file beside the data is not wired into anything, and the next
package has to invent its own.

The rest of this section is for that design conversation.

A **widget** is a small ES module that turns one file into something worth
looking at. It runs in a sandboxed frame with **no network and no session**, so
everything it needs is handed to it:

```ts
export async function mount(el: HTMLElement, ctx: Ctx): Promise<() => void> {
  // ctx.path       where the file lives
  // ctx.text       its contents, already fetched for you
  // ctx.theme      'dark' | 'light'
  // ctx.fetchAsset(name)     a sibling file, as text
  // ctx.fetchAssetUrl(name)  a sibling binary, as a URL you can put in <video>
  el.replaceChildren(/* whatever you build */)
  return () => { /* tear down: listeners, timers, GPU contexts */ }
}
```

The sandbox is not a formality. The frame has an opaque origin, so `fetch`,
`/api/...`, cookies and the page around it are all unreachable — a widget that
tries will get `TypeError: Failed to fetch`. Ask for files through `ctx`
instead; the host fetches them, and it will only give you files beside the one
you were opened with.

### Where it goes

Widget code is **not** part of a package and does not live in a task directory.
It lives at the top level:

```
widgets/<widget-name>/
  widget.json      { "name", "title", "description" }
  src/index.ts     yours to write, in full
  dist/index.js    built output; the browser loads exactly this
```

Inside `widgets/<widget-name>/` you are unconstrained: any layout, any
dependency already in the repo, any amount of code. Bundle it with

```
node {workspace}/widgets/build.mjs <widget-name>
```

which produces the single ESM file the browser needs. Everything is bundled in,
because the frame cannot fetch imports of its own. A widget that is not built
is ignored, and the file falls back to the code viewer — so build it, then check
it loads, before you say it is done.

### Telling dex when to use it

The code is shared; the rules are per project, and live in `widgets.json` beside
this guide:

```json
{
  "rules": [
    { "widget": "pose-skeleton", "paths": ["*/poses/*.json"] },
    { "widget": "narrated-video", "extensions": [".mp4", ".webm"] }
  ]
}
```

- `extensions` matches the end of the filename, so `.pose.json` distinguishes a
  figure from every other JSON file. Plain `.json` almost never means what you
  want: a manifest is JSON too.
- `filenames` are glob patterns against the bare filename, for when the
  extension alone is too coarse.
- **The first matching rule wins.** Put the specific one above the general one.

Anything unmatched keeps the built-in viewers, which is the right answer for
most files.

### Test it with Playwright

A widget that builds is not a widget that works. Prove it against a real file
from this project, in a real browser:

```
{workspace}/node_modules/.bin/playwright test --config {workspace}/widgets/playwright.config.ts
{workspace}/node_modules/.bin/playwright test --config {workspace}/widgets/playwright.config.ts pose-skeleton   # just one
```

Playwright and Chromium are installed. Do not reach for a headless Chrome of
your own, Puppeteer, or a screenshot script — there is one browser here and one
way to drive it.

A spec reads its fixture files from disk, so resolve every one of them from
`import.meta.url` — never from a bare relative path. You are run from your own
package directory, not the repository root, and a path beginning `assets/` or
`widgets/` is read against whatever the working directory happens to be:

```ts
const REPO = fileURLToPath(new URL('../../../', import.meta.url))
const POSE = join(REPO, 'assets/yoga/some-pose/poses/some_pose.json')
```

Tests live in `widgets/<name>/test/*.spec.ts`. The harness mounts your widget
the way dex does — the same `mount(el, ctx)`, the same `ctx` shape — and hands
you the file:

```ts
await page.goto('/widgets/harness.html?widget=<name>')
await page.waitForFunction(() => window.__ready)
await page.evaluate(([path, text]) => window.__mount({ path, text }), [path, body])
await expect(page.locator('#root canvas')).toBeVisible()
```

`__mount` also takes `assets` — a map of sibling filename to contents — for a
widget that reads more than the file it was opened with.

Three things worth asserting, because they are the ways a widget actually
fails:

- **It draws something.** A canvas, an element, text — evidence it got past
  parsing rather than throwing on the way.
- **It works on a real file.** Read one out of `assets/` rather than inventing
  a fixture. A widget that only handles the shape its author imagined is the
  common failure, and a hand-made fixture hides it.
- **It fails visibly.** Feed it something empty or malformed and check it says
  so. A blank frame is the worst outcome: nobody can tell a broken widget from
  an empty file.

The harness deliberately does not use the sandbox. That is dex's side of the
contract and is tested once, in the application; fighting an opaque origin to
assert what your widget drew only makes these tests flaky.

### What is worth building one for

Only a file a reader would rather *see* than read. A viewer that pretty-prints
JSON is the code viewer with extra steps. A viewer that draws the figure that
JSON describes is the reason this exists.

## Shared utilities — prove it first, then ask at the end

Sometimes the useful thing you write is not part of the package at all: a
helper that validates the deliverable, a build step, a check every package
needs. That is **tooling**,
and the next task will want it too. Copying it from package to package is how
six subtly different versions of one function end up in the project.

Shared tooling lives in `utils/` in the project directory — one level above your
package — as a plain Python package that any package can import:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import helpers  # noqa: E402
```

Keep the deliverable itself self-contained — a person reads it. `utils/` is for
the build side: checks, conversions, generation steps.

### Before you write a helper, read the index

Two tables at the end of this section are the whole search: *Shared utilities*,
a line per module already in the project's `utils/`, and *Kept local*, the
record of what the operator has already decided does **not** belong there. Both
are in this guide, which you have been given — so finding out what exists costs
you nothing. You do not have to list directories and you do not have to read
the modules.

**When a row looks like it might cover your case, read `utils/API.md`** — one
level above your package. It is generated from the modules themselves: every
public signature, constant, and one-line description, and none of the bodies.
It is about a tenth of the source and it is normally all you need. Reach for a
module's own source only when a signature and its line genuinely do not settle
whether it fits — and then read that one module, not the directory.

What you find lands in one of three places:

- **It does the job.** Import it and carry on — no question, no new module,
  nothing to propose. Writing a second version of a function already sitting
  there is the exact thing this section exists to prevent.
- **It nearly does the job** — right idea, wrong signature, a case it does not
  handle. **Do not edit it now.** Call it for the part it does cover and do the
  rest inside your package. Often that is the end of it; if a real gap remains,
  the change gets proposed at the end, like anything else.
- **Nothing covers it.** Write your own, locally, and carry on.

Then read *Kept local*. **If what you are about to write is listed there, the
operator has ruled it out**: write it inside your package and leave it there.
That table is their veto, not a suggestion — a row in it is closed.

### Keep them general, or they will never stop changing

A shared utility earns its place by outliving the package that wrote it. The
ones that do are **mechanism**: they compute a value or answer a question and
hand it back. The ones that do not are **policy** — they settle what counts as
correct for the package that happened to need them first, and then every
package after that needs them changed.

So what you write, and what you propose, should:

- **Return data, not verdicts or prose.** A list of problems the caller can
  read, count, ignore, or print. The moment a helper formats a message or
  decides that a package has failed, the next caller wants it to decide
  differently, and the only way to get that is to edit it.
- **Take a default, not a rule.** A threshold belongs in a keyword argument
  with a sensible default, so a package that needs a different one passes it
  and nobody touches the function.
- **Do one thing.** Two small functions the caller composes beat one that takes
  a mode flag: a caller who wants half of it can then take half.

And the tripwire: **if you find yourself adding a second or third keyword
argument for one caller, what you want is not this function.** Write yours
locally. A utility that grows a knob per package is one that every task will
keep editing, and a shared thing that changes every week is worse than no
shared thing at all — nobody can rely on what it does.

The same instinct settles the "nearly does the job" case above. **Prefer
composing over changing.** If you can call the utility and do the remaining bit
yourself inside your package, that is not a change worth proposing — that is
the utility working as intended. Propose a change only when the gap is one
every future caller would hit too.

### While you work, keep it local

Whatever you write stays **inside your task directory** and is used there. Do
not reach for `utils/` mid-task. An untested module promoted into shared space
is worse than no shared module at all, and stopping to ask halfway through
interrupts work that has not yet shown the helper is worth sharing.

### At the end, once everything is green, ask

As the **last step before your summary** — after the deliverable is built and
every check you can run has passed, so the code has actually run and done its
job — look back at what you wrote. Two things are worth raising, and both use
the same single question at the same moment:

**A local module that should be shared.** It is **generic** — nothing in it is
specific to this package — and **reusable**: you can name another package that
would want it.

**A shared module that should change.** You read one during the task, it nearly
fit, and composing around it did not cover the gap. Raise it now, but only as an
**additive, backward-compatible** change: a new function beside the existing
one, or a new keyword argument whose default preserves today's behaviour.
Packages you cannot see already import that module and you cannot test them, so
a changed signature or an altered return value is not yours to propose. If what
you need cannot be done additively, keep yours local and say so in your summary.

Before you ask about either, two checks.

**Is it vetoed?** Read the *Kept local* table below. It is the operator's list of
helpers that are deliberately not shared. A row there settles it: leave yours in
your package and say nothing more about it.

**Is sharing switched on?** Call `mcp__dex__utility_proposals_enabled`. It is a
global operator setting, read at the moment you call it. If it answers
`disabled`, **ask nothing**: leave the helper where it is, change nothing
outside your directory, and note in one line of your summary what you would have
shared. Do not work around the setting, and do not argue the case at length in
the summary instead — off means not asked.

If nothing vetoes it and sharing is on, call `mcp__dex__ask_user` **once**, with
`kind` set to `"utility"` and exactly these two options, **in this order**:

- **"Put it in the `<skill>` skill"** — naming one of the skills listed in
  your brief, the one that module belongs with
- **"Keep it in the task directory"**

The `kind` and the order both matter: they are how dex recognises this question
and which option it takes as the affirmative one. Get either wrong and the
question goes to the operator as an interruption instead.

**The answer may come back instantly.** While sharing is on, dex answers this
one for the operator rather than interrupting them, and the answer is to
promote. That is not a reason to skip the question — it is how the decision gets
recorded where the operator can see it — and it is not a reason to assume the
answer either. Ask, wait, and do what comes back.

Make the question concrete — name the module, say what it does in a line, say
what you verified, and name the packages that would want it:

> `check_limb_symmetry(pose)` compares left and right limb lengths and reports
> the mismatches; run here against all 20 landmarks. Every pose package needs it
> before it is done. Put it in the `<skill>` skill, or keep it local to this
> package?

**On "Put it in the `<skill>` skill", in this order:**

1. Write the module, or the added function or argument, into
   **`skills/<skill>/utils/`** — the skill's own directory, named in your
   brief. Not the project's `utils/`: that one is materialised from the
   enabled skills and is rewritten whenever they change, so a module left
   there is a module that disappears, and the skill anybody else would copy
   never learns it exists.
2. Give whatever you add a **one-line docstring**, and the same for the module
   if it is new. That line is the interface the next task reads out of the
   generated index, so it is not a comment — write it for a stranger.
3. Change your package to import it from `utils/` and **delete the local copy**.
   Two versions is the thing this avoids.
4. **Re-run your checks.** You moved working code; you do not know it still
   works until you have seen it pass from its new home.
5. Nothing else. dex re-versions the skill and materialises it into every
   project that has it enabled when your task finishes clean, and regenerates
   `utils/API.md` with it. You do not run anything, and you do not edit a list.

You maintain no list by hand — `utils/API.md` and the roster in this file are
both generated from the code, and dex rewrites them before every task besides.

The writes land outside your task directory, so unless the operator has
auto-approve on, each one still stops for their approval. Wait for it rather
than abandoning the step. **If a write is declined, that is the answer** — put
the helper back in your package, leave `utils/` as you found it, re-run your
checks, and say so in your summary.

**On "Keep it in the task directory"**, it stays where it is and `utils/` is
untouched. Do not edit the *Kept local* table yourself; it is the operator's.

Ask **once**, at the end, and only about code that has actually run. If several
things belong together, name them all in that one question. Do not ask about a
three-line helper only your package will ever use — that is not a shared
utility. If no answer comes back at all, keep it local and say so in your
summary.

A project-scoped task already has the project directory as its own, so its
writes need no approval — it still asks, at the end, for the same reason.

### Shared utilities

Every module in the project's `utils/`, a line each. Both this roster and the
fuller `utils/API.md` beside the modules are generated from the source and
rewritten before your task starts, so neither can fall behind the code and
neither is yours to edit by hand. To change what a row says, change the
module's docstring.

<!-- utils:begin -->
<!-- Rows come from each module's first docstring line. Change the docstring, not the table. -->

| Module | What it does |
|---|---|
| _(none yet)_ | |

<!-- utils:end -->

A row is enough to tell whether a module is worth a closer look. When one is,
`utils/API.md` gives every public signature and constant at about a tenth of
the source; the module itself is the last resort.

### Kept local — the operator's veto

Helpers that are deliberately **not** shared, one row each. This table is
maintained by the operator, not by you: **never add, edit, or remove a row.**
Read it before you promote anything, and if what you have is listed, leave it in
your package.

It is empty unless someone has put something in it.

| Helper | Why it stays local |
|---|---|
| _(none yet)_ | |
