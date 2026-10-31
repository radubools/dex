---
name: game-voice
description: Spoken captions for the games in ~/games — every text a story's card shows (its line, a question, the "more" paragraphs behind ?, the green or red hint after an answer) spoken by a local text-to-speech model (ollama's phonon, voice Preston), one clip per text, in one kind "voice" package per game that the game pulls and plays as the cards show. Runtime numbers are filled in from a play-through of the story, or inferred from the beat's context.
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

## One package per game, keyed by text

All of a game's captions live in **one package of kind `voice`**, named after
the game (`model-workshop`), tagged `type:voice`, in the project's captions
thread. Every package manifest carries `tags` as `group:value` strings,
because dex's Library builds its filter groups from the group: any `tags`
you pass to `write_package` are grouped too, and keep `type:voice`.

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
  marks, `$$…$$` LaTeX becomes words (`\frac{a+b}{2}` → "a plus b, all over 2",
  `O(n^2)` → "O of n squared", `\hat y` → "y hat", `\mu` → "mu"), arrows and
  comparison signs become words. `v.latex_words` covers the common commands;
  read a sample of the results and put a better reading for any text that
  comes out wrong in `spoken.json` (or improve `latex_words` here, with a test,
  when a whole family is wrong).
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

```python
pkg = Path(".")                     # this task's package directory
out = v.make_captions(Path("..").resolve(), pkg, name="<game>", game_root=GAME, max_seconds=480)
print(out["summary"], out["synth"])  # {plain, filled, override, skip, total}, {made, had, failed, left}
```

`make_captions` plans every story package in the project (the newest of each
story name), makes the clips that are missing (several at once; a clip is
made once and kept under its name), and writes `captions.json` and the
manifest. A tool call has a time limit, so it stops starting new clips after
`max_seconds`: **call it again until `left` is 0**. `failed` lists clips the
model could not make, with why; retry them once, then report what still fails.

Then work through `out["skipped"]` (texts with runtime values nothing filled
in): infer their words as above, add them to `spoken.json`, and run
`make_captions` again. Finish with `v.validate_voice(pkg)` (must be ok), and
listen to a handful: `afplay voice/<file>.ogg` will not play Opus, so convert
one with `ffmpeg -i voice/<file>.ogg /tmp/x.wav` and check its length and
level (`ffmpeg -i … -af volumedetect -f null -`) instead.

**Done** means: every text of every story has a clip, except texts you could
not make speakable, each listed with why in your summary; `validate_voice`
is ok; the summary gives the counts (`summary`) and the package's size.

## New and changed stories

A story written or changed after the captions were made has texts with no
clip. The captions task is continued with a message ("caption the new
stories"): `make_captions` again makes only what is missing and drops clips
no text uses any more (`write_package(prune=True)` once nothing is `left`).

## The game's side

Nothing here writes into a game. The game pulls the package like any other
(`~/games/tools/pull_dex_assets.py <game>`, which installs kind `voice` with
`v.export_voice`), its engine loads `captions.json` and speaks on the audio
mixer's `voice` channel, and the player turns captions on and off from the
🗣 button in the game's ☰ menu.
