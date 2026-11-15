---
name: game-voice
description: Spoken captions for the games in ~/games — every text a story's card shows (its line, a question, the "more" paragraphs behind ?, the green or red hint after an answer) spoken by a local text-to-speech model (ollama's phonon, voice Preston), one clip per text, in one kind "voice" package per story (`voice/<story>`, started by that story's task) that the game pulls and plays as the cards show. Runtime numbers are filled in from a play-through of the story, or inferred from the beat's context.
---

# Game voice — spoken captions for a game's stories

A game built on the engine speaks its story cards when they show: the beat's
line (`say`) and its question (a choice's or a typed answer's `prompt`), the
`more` paragraphs when the player opens them, and the hint after an answer
(an option's `feedback`, an input's `right` / `wrong`). The clips come from
here, made with a local model:

```bash
ollama run phonon --voice Preston "Once upon a time, a little alpaca lived at the top of a very tall mountain." > test.wav
```

```python
from utils import voice as v
GAME = Path.home() / "games" / "<game>"      # the project brief names the game folder
```

Read `utils/API.md` for every signature.

## One package per story, keyed by text

Each story's captions are **its own package of kind `voice`**, named after the
story (`voice/llm-svd` for `story/llm-svd`), tagged `type:voice` and
`story:<name>`, made by a task of its own in the project's captions thread
(slug `voice-<story name>`, e.g. `voice-llm-svd`). The story's task starts it
as a dependency once the story is done, waits for it blocked, and pulls it
into the game; a later change to the story continues the same captions task.
One task per story means stories never queue behind one another's captions,
and no two tasks ever write the same package.

The game's engine loads every `voice` package it has and merges their clips
by key, so a text shared by two stories may have a clip in both: that is the
same file (a clip is named by its words), harmless, and copied rather than
made again (`reuse_clips`). The older game-wide package (`voice/<game>`, made
with `make_captions`) still holds the stories captioned before this; it is
not grown any more.

Every package manifest carries `tags` as `group:value` strings, because dex's
Library builds its filter groups from the group: any `tags` you pass to
`write_package` are grouped too, and keep `type:voice`.

It never edits a story: the stories belong to their own tasks, and a
changed `story.json` would need a new end-to-end test before the game takes
it. Instead each clip is keyed by `v.text_key(text)` of the text **exactly as
the story writes it** (FNV-1a 64 of its UTF-8 bytes); the engine computes the
same key from the text it is about to show and plays the clip it finds
(`engine/src/story/captions.js`). A text that has changed since has no clip
and is simply not spoken until the captions are made again.

```
manifest.json   kind "voice", name, files ["captions.json"], voice, model, clips, unspoken, captions (sha256), tags
captions.json   {schema 1, voice, model, clips: {key: "voice/<file>.ogg"}}: all the game loads, on every start
texts.json      [{key, text, words, how, clip, uses}]: for review, stays in dex
spoken.json     yours: {key: words} — what to say for a text, over everything else
voice/*.ogg     mono Opus, 24 kbps (the engine plays .ogg); named by a hash of voice, model and words
```

`texts.json` lists every text with what is said for it (`words`), how that was
decided (`how`) and where it is used (`uses`: story, beat, field). It is for
reading and is not installed: the game downloads `captions.json` on every
start, so it holds only the clips.

## What is said

`v.plan(packages, game_root=GAME, spoken=…)` decides, for each distinct text:

| `how` | when | `words` |
|---|---|---|
| `override` | the key is in the package's `spoken.json` | that |
| `plain` | the text has no runtime values | `v.speakable(text)` |
| `filled` | runtime values, and a play-through filled them in | `v.speakable(the text as shown)` |
| `skip` | runtime values, nothing filled them in | none: **yours to infer** |

- **Markdown and math are read out**: `**bold**` and `` `code` `` lose their
  marks, `$$…$$` LaTeX becomes words (`\frac{a+b}{2}` → "A plus B, all over 2",
  `O(n^2)` → "O of N squared", `\hat y` → "Y hat", `\mu` → "mu"), arrows and
  comparison signs become words. `v.latex_words` covers the common commands;
  read a sample of the results and put a better reading for any text that
  comes out wrong in `spoken.json` (or improve `latex_words` here, with a test,
  when a whole family is wrong).
- **A symbol's letter is spoken in uppercase**: the TTS model says "Y", "W",
  "X" as letter names but reads a lowercase y, w, x as a word or part of one.
  So `$$\hat y = w x + b$$` is spoken "Y hat equals W X plus B", `x_i`
  "X I", and in prose a lone letter is a variable: "slide k to 4" → "slide K
  to 4", `arr[i]` → "arr at I". A run of letters (`kx`, `tail`, `\text{…}`) is
  a name and keeps its case. The article "a" stays a word unless it cannot
  be one: next to an operator, an index or a call (`a = []`, `b + a`, `a[i]`),
  before punctuation or a verb (`take a, then b`, `a has digits`) or after
  another lone letter (`b b a`). Inside `` `code` `` every lone letter is a
  variable. Letters in a dotted name (`node.p`, `e.g.`), after an apostrophe
  and inside a runtime `{…}` are left alone. Only the **words** change: the
  text and its key stay exactly as the story writes them
  (`v.prose_letters` in prose, the lone-letter branch of `latex_words` in
  maths, in both `voice` and `speech`). A `spoken.json` entry is used as
  written, so write its letters in uppercase too.
- **Runtime values** (`{…}` outside `$$…$$`, `\val{…}` inside): the game
  fills them in as the card shows. `v.filled_texts(package, GAME)` plays the
  story through the game's own code with the game's seeds (the game-story
  skill's `story.simulate`), so the line, the question and the right answer's
  feedback of every beat on the main path get **the very values a player
  sees**. That leaves the `more` paragraphs and the other options' feedback:
  **infer sensible numbers from the beat's context** — the same beat's filled
  line, the snapshot's variables and data (`story.simulate` returns them per
  beat), the story's code and inputs — and write the full sentence to
  `spoken.json`. Where no sensible number can be inferred, say the sentence
  without it (rephrased so it still reads) rather than leave it unspoken; a
  number that is a little off is better than silence. Never speak a raw
  `{…}` or `\val{…}`.

## Making them (resumable)

The brief names the story's package. This task's package directory is the
captions package:

```python
pkg = Path(".")                                        # this task's package: voice/<story>
story_pkg = Path("..") / "story-<name>"                # the brief names it
out = v.make_story_captions(story_pkg, pkg, game_root=GAME, max_seconds=480)
print(out["summary"], out["synth"], out["reused"])     # {plain, filled, override, skip, total}, {made, had, failed, left}
```

`make_story_captions` plans that one story's texts, copies the clips other
voice packages of the project already have, makes the missing ones (several at
once; a clip is made once and kept under its name), and writes `captions.json`
and the manifest. A tool call has a time limit, so it stops starting new clips
after `max_seconds`: **call it again until `left` is 0**. `failed` lists clips
the model could not make, with why; retry them once, then report what still
fails.

Then work through `out["skipped"]` (texts with runtime values nothing filled
in): infer their words as above, add them to `spoken.json`, and run
`make_story_captions` again. Finish with `v.validate_voice(pkg)` (must be ok), and
listen to a handful: `afplay voice/<file>.ogg` will not play Opus, so convert
one with `ffmpeg -i voice/<file>.ogg /tmp/x.wav` and check its length and
level (`ffmpeg -i … -af volumedetect -f null -`) instead.

**Done** means: every text of the story has a clip, except texts you could
not make speakable, each listed with why in your summary; `validate_voice`
is ok; the summary gives the counts (`summary`) and the package's size.

## New and changed stories

A story's captions task is continued with a message when its story changes:
`make_story_captions` again makes only what is missing and drops clips no
text uses any more (the package is pruned once nothing is `left`). The
game-wide package of the stories captioned before is remade with
`v.make_captions(project_dir, pkg, name="<game>", game_root=GAME)`, which plans
every story in the project; it is only for that package.

## The game's side

Nothing here writes into a game. The game pulls the package like any other
(`~/games/tools/pull_dex_assets.py <game>`, which installs kind `voice` with
`v.export_voice`), its engine loads `captions.json` and speaks on the audio
mixer's `voice` channel, and the player turns captions on and off from the
🗣 button in the game's ☰ menu.
