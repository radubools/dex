"""Validate a package's manifest.json, above all its tag spelling.

The Library builds its filters out of the tag strings themselves, so a
misspelt value is a new pill rather than an error. The chakra tags are the
sharp edge: the colour emoji is part of the value, and `chakra:muladhara`
without it makes a second pill for a chakra that already has one. The exact
strings live here so no package has to retype them.

    from utils import manifest

    problems = manifest.validate_manifest(
        json.loads(path.read_text()), poses_dir=path.parent / "poses")
"""

#: The difficulty tags, one per pose, each led by its colour.
DIFFICULTY_TAGS = (
    "difficulty:\U0001f7e2easy",
    "difficulty:\U0001f7e1medium",
    "difficulty:\U0001f534hard",
)

#: The seven chakra tags, verbatim. Copy, never retype.
CHAKRA_TAGS = (
    "chakra:\U0001f534muladhara",
    "chakra:\U0001f7e0svadhisthana",
    "chakra:\U0001f7e1manipura",
    "chakra:\U0001f7e2anahata",
    "chakra:\U0001f535vishuddha",
    "chakra:\U0001f7e3ajna",
    "chakra:⚪sahasrara",
)

#: The preferred muscle values. A new one is allowed when none of these fits,
#: so an unlisted value is reported as a warning, not a failure.
MUSCLE_VALUES = (
    "hamstrings", "quadriceps", "glutes", "hip-flexors", "adductors",
    "abductors", "calves", "ankles", "core", "obliques", "lower-back",
    "spine", "chest", "shoulders", "upper-back", "lats", "triceps",
    "biceps", "forearms", "wrists", "neck", "pelvic-floor",
)

#: The groups this project filters by. A new group splits the filters.
GROUPS = ("difficulty", "chakra", "muscle")

_ALLOWED_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def check_tags(tags, strict_muscles=False):
    """Check tag grammar, vocabulary, counts and ordering.

    Returns a list of problems. Unlisted muscle values are reported only when
    `strict_muscles` is true, since the project allows a new value where none
    of the listed ones fits.
    """
    problems = []

    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return ["'tags' must be a flat list of strings"]
    if not tags:
        return ["'tags' is empty"]

    groups = []
    for tag in tags:
        if tag.count(":") != 1:
            problems.append(f"{tag!r} must have exactly one colon")
            continue
        group, value = tag.split(":", 1)
        if not group or not value:
            problems.append(f"{tag!r} has an empty half and names nothing")
            continue
        groups.append(group)
        if group != group.lower() or not set(group) <= _ALLOWED_CHARS:
            problems.append(f"group in {tag!r} must be lowercase kebab-case")
        if group.endswith("s") and group not in ("difficulty",):
            problems.append(f"group in {tag!r} should be singular")
        if group not in GROUPS:
            problems.append(
                f"{tag!r} invents the group {group!r}; this project filters by "
                f"{', '.join(GROUPS)}")
        # A value may lead with an emoji; the rest must be kebab-case.
        letters = "".join(c for c in value if c.isascii())
        if letters != letters.lower() or not set(letters) <= _ALLOWED_CHARS:
            problems.append(
                f"value in {tag!r} must be lowercase kebab-case "
                f"(no spaces, no underscores)")

    difficulties = [t for t in tags if t.startswith("difficulty:")]
    if len(difficulties) != 1:
        problems.append(f"expected exactly one difficulty tag, found {difficulties}")
    for tag in difficulties:
        if tag not in DIFFICULTY_TAGS:
            problems.append(
                f"{tag!r} is not one of the three difficulty tags; each is led by "
                f"its colour: {', '.join(DIFFICULTY_TAGS)}")

    chakras = [t for t in tags if t.startswith("chakra:")]
    if not 1 <= len(chakras) <= 3:
        problems.append(f"expected one to three chakra tags, found {len(chakras)}")
    for tag in chakras:
        if tag not in CHAKRA_TAGS:
            problems.append(
                f"{tag!r} is not one of the seven chakra tags; the colour is part "
                f"of the value, so this makes a second pill for one chakra")

    muscles = [t for t in tags if t.startswith("muscle:")]
    if not 2 <= len(muscles) <= 5:
        problems.append(f"expected two to five muscle tags, found {len(muscles)}")
    if strict_muscles:
        for tag in muscles:
            value = tag.split(":", 1)[1]
            if value not in MUSCLE_VALUES:
                problems.append(f"{tag!r} is not one of the listed muscle values")

    expected = (["difficulty"] * len(difficulties) + ["chakra"] * len(chakras)
                + ["muscle"] * len(muscles))
    if len(groups) == len(expected) and groups != expected:
        problems.append(
            f"tags should read difficulty, then chakras, then muscles; got {groups}")

    return problems


def validate_manifest(manifest, poses_dir=None, strict_muscles=False):
    """Check a manifest's shape, its tags, and that files.poses is what exists.

    `poses_dir` is the package's `poses/` directory; pass it and the listed
    files are compared against what is on disk.
    """
    problems = []

    if not manifest.get("pose"):
        problems.append("'pose' is missing or empty")
    problems += check_tags(manifest.get("tags"), strict_muscles=strict_muscles)

    files = manifest.get("files")
    if not isinstance(files, dict) or not isinstance(files.get("poses"), list):
        problems.append("'files.poses' must be a list of pose filenames")
        return problems

    listed = sorted(files["poses"])
    if poses_dir is not None:
        on_disk = sorted(p.name for p in poses_dir.glob("*.json"))
        if listed != on_disk:
            problems.append(
                f"'files.poses' is {listed} but poses/ holds {on_disk}")

    return problems
