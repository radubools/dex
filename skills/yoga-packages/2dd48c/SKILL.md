---
name: yoga-packages
description: The Yoga project's package conventions — its tag vocabulary (difficulty, chakra, muscle) and the manifest validation that enforces spelling, counts and ordering.
---

# Packages

What a Yoga package must look like, as something a script can check rather than
something a reader has to remember.

| Module | For |
|---|---|
| `utils/manifest.py` | `check_tags` and `validate_manifest`, plus the vocabularies they check against |

The vocabularies are the point: `DIFFICULTY_TAGS`, `CHAKRA_TAGS` and
`MUSCLE_VALUES` are closed sets, and a tag that is merely plausible is still
wrong. Validation catches the spelling, the counts, the ordering, and that
`files.poses` names what is actually on disk.

Note that a chakra tag carries a coloured-circle emoji inside its value —
`chakra:🟡manipura` — which is a deliberate exception to the otherwise ASCII
tag rule. `check_tags` knows; hand-written checks usually do not.

## Using it

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.manifest import validate_manifest
```

## Changing it

**Edit the skill, not the copy.** `assets/<project>/utils/` is materialised
from the enabled skills and overwritten when that happens, so an edit made
there disappears. Write to `skills/yoga-packages/utils/`.

Changing a vocabulary is not a small edit: every package already tagged under
the old one becomes wrong, and nothing here will tell you which. Widen a set
rather than renaming its members where you can.

## What it will not do

It does not do geometry. Placing and measuring a skeleton is `yoga-figure`, and
this module should never import it — a manifest check that needed a rig would
mean a project could not validate its packages without also taking on the
anatomy.
