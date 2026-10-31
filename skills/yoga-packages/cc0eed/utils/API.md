## `manifest`

Validate a package's manifest.json, above all its tag spelling.

- `DIFFICULTY_TAGS` — constant
- `CHAKRA_TAGS` — constant
- `MUSCLE_VALUES` — constant
- `GROUPS` — constant
- `check_tags(tags, strict_muscles=False)` — Check tag grammar, vocabulary, counts and ordering.
- `validate_manifest(manifest, poses_dir=None, strict_muscles=False)` — Check a manifest's shape, its tags, and that files.poses is what exists.