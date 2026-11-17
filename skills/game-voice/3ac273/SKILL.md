---
name: game-voice
description: Spoken captions for the games in ~/games — the words to say for every text a story's card shows (its line, a question, the "more" paragraphs behind ?, the green or red hint after an answer), with markdown and maths read out and runtime values kept as slots, in one kind "voice" package per story (`voice/<story>`, started by that story's task). The game speaks the words itself, live in the browser, in the voice the player picks. Runtime numbers are filled in from a play-through of the story, or inferred from the beat's context. No audio is made.
---

# Game voice — spoken captions for a game's stories

A game built on the engine speaks its story cards when they show: the beat's
line (`say`) and its question (a choice's or a typed answer's `prompt`), the
`more` paragraphs when the player opens them, and the hint after an answer
(an option's `feedback`, an input's `right` / `wrong`). It speaks them live,
in the player's browser (the engine's `PhononSpeech`), in the voice the player
picks. What it says comes from here: **the readable words of every text**.
This skill makes no audio: no clips, no text-to-speech model, no ffmpeg.

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

The game's engine loads every `voice` package it has and merges their
lookups by key, so a text shared by two stories has the same words in both,
which is harmless. The older game-wide package (`voice/<game>`, made with
`make_captions`) still holds the stories captioned before this; it is not
grown any more.

Every package manifest carries `tags` as `group:value` strings, because dex's
Library builds its filter groups from the group: any `tags` you pass to
`write_package` are grouped too, and keep `type:voice`.

It never edits a story: the stories belong to their own tasks, and a
changed `story.json` would need a new end-to-end test before the game takes
it. Instead each text's words are keyed by `v.text_key(text)` of the text
**exactly as the story writes it** (FNV-1a 64 of its UTF-8 bytes); the engine
computes the same key from the text it is about to show and speaks the words
it finds (`engine/src/story/captions.js`). A text that has changed since has
no entry and is simply not spoken until the captions are made again.

```
manifest.json   kind "voice", name, files ["captions.json"], schema, words, live, unspoken,
                captions (sha256), tags
captions.json   schema 2, all the game loads, on every start:
                {schema 2,
                 words: {key: "<what to say>"},          every text that has words
                 live:  {key: "<words with {…} slots>"}} runtime texts only
texts.json      [{key, text, words, how, uses}]: for review, stays in dex
spoken.json     yours: {key: words} — what to say for a text, over everything else
```

That is the whole package. `texts.json` lists every text with what is said for
it (`words`), how that was decided (`how`) and where it is used (`uses`: story,
beat, field). It is for reading and is not installed: the game downloads
`captions.json` on every start, so it holds only the lookups.

**`live`** lets the game say the numbers the player actually sees. For a
runtime text it is the text read as the skill reads it (maths and markdown
read out, maths letters in uppercase) with every runtime value left as a slot
the engine's story runner fills exactly as it fills the card
(`engine/src/story/expr.js` `interpolate`): `{expr}` stays as written, format
suffix and all, and `\val{expr}` inside maths becomes `{expr}`
(`$$\hat y = \val{fit.w:2} x$$` → "Y hat equals {fit.w:2} X"); braces said
aloud are doubled. The engine speaks `live` filled in when a text has it,
else `words`. A text gets no `live` when a slot does not hold a number or a
word or two (a list, a sentence, an error message): `v.live_words` judges
that from the text's `words`, which say what one play-through filled in,
then from what the package's other texts show of the same slot, and
otherwise trusts only a plainly numeric slot (a format `:2` or `:%`,
arithmetic, `round(…)`/`len(…)`, a `$model.…`, `$data.count…` or
`$code.hits…` read). `live` is read from the text, so a hand fix to a
text's words in `spoken.json` is not in it: a family the reader gets wrong
is fixed in the reader here, with a test.

### Packages made when there were clips

Packages made before this carry recorded clips (`voice/*.ogg`, a `clips`
lookup, a `clip` per text; schema 1 has no `words` at all). **`validate_voice`
fails on them**, and the game cannot pull them, until they are made words
only:

```python
v.drop_clips(pkg)   # {name, schema, files, bytes, words, live, ok}
```

It builds `words` and `live` as `v.game_captions` does (a schema 2 package
keeps its own words; a schema 1 package's come from its `texts.json`),
deletes `voice/*.ogg` and the `voice/` folder, and rewrites `captions.json`,
`texts.json` and `manifest.json`. Running `make_story_captions` on such a
package does the same as part of remaking it.

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
  comparison signs become words. A story link `[[story/x|label]]` is read as
  its label by `speech.speakable` (which `live` uses); `v.speakable` leaves
  it as written, so put such a text's words through `v.link_words` into
  `spoken.json`. `v.latex_words` covers the common commands;
  read a sample of the results and put a better reading for any text that
  comes out wrong in `spoken.json` (or improve `latex_words` here, with a test,
  when a whole family is wrong).
- **A symbol's letter is spoken in uppercase**: speech says "Y", "W",
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

## Making them

The brief names the story's package. This task's package directory is the
captions package:

```python
pkg = Path(".")                                        # this task's package: voice/<story>
story_pkg = Path("..") / "story-<name>"                # the brief names it
out = v.make_story_captions(story_pkg, pkg, game_root=GAME)
print(out["summary"], len(out["skipped"]), len(out["missing"]))   # {plain, filled, override, skip, total}
```

`make_story_captions` plans that one story's texts and writes the package's
words: `captions.json`, `texts.json` and the manifest. It takes seconds.

Then work through `out["skipped"]` (texts with runtime values nothing filled
in): infer their words as above, add them to `spoken.json`, and call
`make_story_captions` again. Read a sample of `texts.json` (the maths ones
especially) and fix any reading that comes out wrong in `spoken.json`.

**Done** means all of:

- `v.validate_voice(pkg)["ok"]`;
- `out["missing"] == []`, i.e. `v.missing_words(story_pkg, pkg)` is empty:
  **every text of the story (`v.story_texts`) has `words`**;
- the summary gives the counts (`summary`), how many texts have `live`, and
  the package's size.

A text you truly cannot make speakable keeps the task from being done: say
which and why in your final message, and report it as not done, so the story
task that waits on you follows up.

## New and changed stories

A story's captions task is continued with a message when its story changes:
`make_story_captions` again plans the story as it is now. The game-wide
package of the stories captioned before is remade with
`v.make_captions(project_dir, pkg, name="<game>", game_root=GAME)`, which plans
every story in the project; it is only for that package.

## The game's side

Nothing here writes into a game. The game pulls the package like any other
(`~/games/tools/pull_dex_assets.py <game>`, which installs kind `voice` with
`v.export_voice(pkg, game, clips=False)`): `captions.json` goes in as the
package has it, and any clips an earlier export left in the game are removed.
`clips=True` is accepted and only warns: there are no clips to install. Its
engine loads `captions.json` and speaks the words on the audio mixer's
`voice` channel, and the player turns captions on and off, and picks the
voice, from the 🗣 button in the game's ☰ menu.
