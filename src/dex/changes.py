"""How much a task changed: lines of code and lines of assets (design/25).

Counts added and modified lines, never removed ones. A file with a known code
or documentation extension is code; anything else is an asset, which is more
like a document than code. Binary files have no lines, so they count for nothing.

`Tool` writes are counted from the diff dex already builds for them. A file a
script wrote has no diff: it is counted whole when the run ends.
"""

from __future__ import annotations

from pathlib import Path

CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".ipynb", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".html", ".htm",
    ".css", ".scss", ".sass", ".less", ".sh", ".bash", ".zsh", ".fish", ".sql", ".swift", ".m", ".mm", ".kt",
    ".java", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rs", ".go", ".rb", ".php", ".lua", ".r", ".jl", ".pl",
    ".glsl", ".wgsl", ".vert", ".frag", ".toml", ".ini", ".cfg", ".mk", ".cmake", ".gradle",
    # Documentation.
    ".md", ".mdx", ".rst", ".txt", ".tex", ".adoc",
})
CODE_NAMES = frozenset({"Makefile", "Dockerfile", "Procfile", "Gemfile", "Rakefile"})
#: Not the work: dex's own state, and the agent's notes to itself.
NOT_WORK = frozenset({".dex", ".claude", ".git", "node_modules", "__pycache__"})
#: Past this a file is data whose lines mean nothing, and reading it is not free.
MAX_FILE_BYTES = 20_000_000


def is_code(path: str | Path) -> bool:
    path = Path(path)
    return path.suffix.lower() in CODE_SUFFIXES or path.name in CODE_NAMES


def is_work(path: str | Path) -> bool:
    return not NOT_WORK.intersection(Path(path).parts)


def magnitude(lines: int) -> int:
    """The smallest power of ten that bounds `lines`, from 0 to 6: 1 is 0, 11 is 2."""
    return next((n for n in range(6) if lines <= 10 ** n), 6)


def size_tags(code: int, assets: int) -> list[str]:
    """Grouped, so the Library's filters list code and assets apart: `code-lines:2c`."""
    return [f"code-lines:{magnitude(code)}c", f"asset-lines:{magnitude(assets)}a"]


def tally(changes: list[tuple[str, int]]) -> tuple[int, int]:
    """Lines of code and of assets, from (path, lines added or modified)."""
    code = sum(lines for path, lines in changes if is_work(path) and is_code(path))
    assets = sum(lines for path, lines in changes if is_work(path) and not is_code(path))
    return code, assets


def file_lines(path: Path) -> int:
    """A whole file's lines, or 0 for a binary one or one too large to mean anything."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return 0
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data[:8192]:
        return 0
    try:
        return len(data.decode("utf-8").splitlines())
    except UnicodeDecodeError:
        return 0


#: Past this many files the summary names how many more there were.
MAX_LISTED_FILES = 40
#: What a task's closing summary calls the list, so a follow-up can find it.
FILES_HEADING = "**Files changed**"


def _names(names: list[str], limit: int = 3) -> str:
    """`a`, `b`, `c` and 2 more: a document's sections are named as they read."""
    shown = [n if n.startswith("§ ") else f"`{n}`" for n in names[:limit]]
    more = f" and {len(names) - limit} more" if len(names) > limit else ""
    return ", ".join(shown) + more


def describe(
    additions: int,
    deletions: int,
    *,
    created: bool = False,
    scripted: bool = False,
    added: list[str] | None = None,
    changed: list[str] | None = None,
    removed: list[str] | None = None,
    line: int | None = None,
) -> str:
    """One line on what happened to one file, by the definitions or sections it touched.

    `added: \`summary\`; changed \`deliver_messages\` (+40 −2)`. With nothing to
    name, where the edit is: `edited around line 42 (+3 −1)`.
    """
    added, changed, removed = added or [], changed or [], removed or []
    if scripted:
        return f"written by a command ({additions} lines)"
    if created:
        if not added:
            return f"new file ({additions} lines)"
        what = "sections" if all(n.startswith("§ ") for n in added) else "defines"
        return f"new file, {what} {_names(added)} ({additions} lines)"
    parts = [f"{verb} {_names(names)}" for verb, names in
             (("added", added), ("changed", changed), ("removed", removed)) if names]
    if not parts:
        parts = [f"edited around line {line}" if line else "edited"]
    return f"{'; '.join(parts)} (+{additions} −{deletions})"


def files_section(files: dict[str, str]) -> str:
    """The closing summary's list of changed files, path to phrase, or "" for none.

    A summary is what a follow-up starts from in place of the whole session
    (`TaskManager.deliver_messages`), so it says where the work is, not only
    what it was.
    """
    if not files:
        return ""
    paths = sorted(files)
    lines = [f"- `{path}` — {files[path]}" for path in paths[:MAX_LISTED_FILES]]
    if len(paths) > MAX_LISTED_FILES:
        lines.append(f"- … and {len(paths) - MAX_LISTED_FILES} more")
    return f"{FILES_HEADING}\n\n" + "\n".join(lines)
