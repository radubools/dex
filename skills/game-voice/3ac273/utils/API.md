## `link_words`

Speakable words for story cross-links ([[story/x|label]]) in caption text.

- `link_words(text: str, prefix: str='llm-') -> str` — Replace [[story/x|label]] with its label and a bare [[story/<prefix>x-y]] with "the x y story".

## `speech`

Story text read out as words with brackets, limits and code read as a teacher would, and every runtime text filled from the game.

- `latex_words(src: str) -> str` — LaTeX (the inside of $$…$$) read out as words, roughly as a teacher would say it.
- `plain_words(text: str) -> str` — Markdown, symbols and code outside math, as words; a single-letter variable in uppercase.
- `speakable(text: str) -> str` — A story text as words to speak: markdown and code read out, $$…$$ math read out, a link as its label.
- `code_words(plain: str) -> str` — Plain text (no $$…$$) with string lists, URL escapes (%20), XML tags and / between operands turned into words.
- `speakable_code(text: str) -> str` — speakable, after code_words on the parts outside $$…$$: for texts that quote code (lists of strings, %20, tags, /).
- `rest_filled_texts(package: str | Path, game_root: str | Path, *, timeout: float=300) -> dict[str, str]` — {key: text as the game shows it} for a story's `more`, every option's feedback and `wrong`, on every played path.

## `voice`

Spoken captions for a game's stories: the words to say for every story text, in one `voice` package.

- `KIND` — constant
- `CAPTIONS_FILE` — constant
- `SPOKEN_FILE` — constant
- `TEXTS_FILE` — constant
- `CLIPS_DIR` — constant
- `SCHEMA` — constant
- `EXPORT_SUBDIR` — constant
- `text_key(text: str) -> str` — A story text's caption key: FNV-1a 64 of its UTF-8 bytes, 16 hex digits (the engine's textKey).
- `is_runtime(text: str) -> bool` — Whether the game fills values into this text when it shows it.
- `story_texts(story: dict) -> list[dict]` — Every text a story's cards show and the game speaks: [{beat, field, text}].
- `GREEK` — constant
- `COMMANDS` — constant
- `lone_letter(src: str, i: int) -> bool` — Whether src[i] is a letter with no letter on either side: in maths, a symbol.
- `prose_letters(text: str) -> str` — Plain text (no $$…$$) with its single-letter variables in uppercase: "slide k to 4" -> "slide K to 4".
- `latex_words(src: str) -> str` — LaTeX (the inside of $$…$$) read out as words, roughly as a teacher would say it.
- `speakable(text: str) -> str` — A story text as words to speak: markdown dropped, $$…$$ math read out.
- `slot_values(text: str, words: str | None) -> dict[str, bool]` — {placeholder: whether `words` says a number or short word for it}, for a runtime text a reader matches; else {}.
- `live_words(text: str, words: str | None=None, *, known: dict[str, bool] | None=None) -> str | None` — A runtime text's words with its values kept as `{expr}`, for the game to fill in as the card does.
- `spoken_lookups(texts: Iterable[dict]) -> tuple[dict[str, str], dict[str, str]]` — captions.json's `words` and `live` from texts.json's items ({key, text, words}).
- `link_words(text: str, *, prefix: str='llm-') -> str` — Story links `[[story/x|label]]` replaced by their label, a bare `[[story/x]]` by "the lesson on x".
- `filled_texts(package: str | Path, game_root: str | Path) -> dict[str, str]` — {key: the text as the game shows it} for the runtime texts a play-through fills in.
- `story_packages(project_dir: str | Path) -> list[Path]` — Every story package in a dex project (its task directories), newest manifest per story name.
- `plan(packages: Iterable[str | Path], *, game_root: str | Path | None=None, spoken: dict[str, str] | None=None) -> list[dict]` — What to speak for every text of these story packages.
- `summary(items: list[dict]) -> dict` — How many texts are spoken each way, and how many are left unspoken.
- `read_spoken(package: str | Path) -> dict[str, str]` — The package's spoken.json ({key: words}), or {}.
- `write_package(package: str | Path, *, name: str, items: list[dict], tags: Iterable[str]=('type:voice',)) -> dict` — Write captions.json, texts.json and manifest.json for the package's texts; returns the manifest.
- `validate_voice(package: str | Path) -> dict` — {ok, errors, warnings}: a words-only voice package the game can load.
- `missing_words(story: str | Path | dict, package: str | Path) -> list[dict]` — The texts of a story (`story_texts`) the voice package has no `words` for: [{beat, field, text, key}].
- `game_captions(package: str | Path) -> dict` — The captions.json a game gets for this package (schema 2): {schema, words, live}.
- `drop_clips(package: str | Path) -> dict` — Make a package with recorded clips (schema 1 or 2) words only, in place.
- `export_voice(package: str | Path, game_root: str | Path, *, clips: bool=False) -> dict` — Install a voice package into `<game_root>/public/assets/voice/<name>/` and rebuild the index.
- `make_captions(project_dir: str | Path, package: str | Path, *, name: str, game_root: str | Path | None=None, **legacy: Any) -> dict` — The game-wide package: plan every story in the project and write its words.
- `make_story_captions(story_package: str | Path, package: str | Path, *, game_root: str | Path | None=None, tags: Iterable[str]=(), **legacy: Any) -> dict` — One story's spoken words, in a voice package of its own, named after the story.