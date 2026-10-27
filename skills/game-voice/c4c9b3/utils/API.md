## `voice`

Spoken captions for a game's stories: one clip per story text, in one `voice` package.

- `KIND` — constant
- `CAPTIONS_FILE` — constant
- `SPOKEN_FILE` — constant
- `TEXTS_FILE` — constant
- `CLIPS_DIR` — constant
- `SCHEMA` — constant
- `DEFAULT_VOICE` — constant
- `DEFAULT_MODEL` — constant
- `OPUS_KBPS` — constant
- `EXPORT_SUBDIR` — constant
- `text_key(text: str) -> str` — A story text's caption key: FNV-1a 64 of its UTF-8 bytes, 16 hex digits (the engine's textKey).
- `is_runtime(text: str) -> bool` — Whether the game fills values into this text when it shows it.
- `story_texts(story: dict) -> list[dict]` — Every text a story's cards show and the game speaks: [{beat, field, text}].
- `GREEK` — constant
- `COMMANDS` — constant
- `latex_words(src: str) -> str` — LaTeX (the inside of $$…$$) read out as words, roughly as a teacher would say it.
- `speakable(text: str) -> str` — A story text as words to speak: markdown dropped, $$…$$ math read out.
- `filled_texts(package: str | Path, game_root: str | Path) -> dict[str, str]` — {key: the text as the game shows it} for the runtime texts a play-through fills in.
- `story_packages(project_dir: str | Path) -> list[Path]` — Every story package in a dex project (its task directories), newest manifest per story name.
- `plan(packages: Iterable[str | Path], *, game_root: str | Path | None=None, spoken: dict[str, str] | None=None) -> list[dict]` — What to speak for every text of these story packages.
- `summary(items: list[dict]) -> dict` — How many texts are spoken each way, and how many are left unspoken.
- `clip_name(words: str, voice: str=DEFAULT_VOICE, model: str=DEFAULT_MODEL) -> str` — A clip's file name: a hash of what makes it (voice, model, words).
- `synthesize_one(words: str, out: Path, *, voice: str=DEFAULT_VOICE, model: str=DEFAULT_MODEL, kbps: int=OPUS_KBPS, timeout: float=120) -> Path` — Speak `words` with the local TTS model (ollama) and write them to `out` as mono Opus.
- `synthesize(items: list[dict], package: str | Path, *, voice: str=DEFAULT_VOICE, model: str=DEFAULT_MODEL, workers: int=8, max_seconds: float | None=None) -> dict` — Make the clips these items need that the package does not have yet.
- `read_spoken(package: str | Path) -> dict[str, str]` — The package's spoken.json ({key: words}), or {}.
- `write_package(package: str | Path, *, name: str, items: list[dict], voice: str=DEFAULT_VOICE, model: str=DEFAULT_MODEL, tags: Iterable[str]=('type:voice',), prune: bool=True) -> dict` — Write captions.json, texts.json and manifest.json for the clips the package has; returns the manifest.
- `validate_voice(package: str | Path) -> dict` — {ok, errors, warnings}: a voice package the game can load.
- `export_voice(package: str | Path, game_root: str | Path) -> dict` — Install a voice package into `<game_root>/public/assets/voice/<name>/` and rebuild the index.
- `make_captions(project_dir: str | Path, package: str | Path, *, name: str, game_root: str | Path | None=None, voice: str=DEFAULT_VOICE, model: str=DEFAULT_MODEL, workers: int=8, max_seconds: float | None=None) -> dict` — The whole job, resumable: plan every story in the project, make the missing clips, write the package.