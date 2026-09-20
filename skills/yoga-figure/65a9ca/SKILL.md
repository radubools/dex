---
name: yoga-figure
description: Build and measure a posed human skeleton in 3D — vector maths, limb placement against joint limits, and the measurements that say whether a pose is physically possible.
---

# Figure

Two modules for putting a human skeleton into a position and then checking that
a person could actually hold it.

Neither imports the other, and neither knows anything about yoga's vocabulary —
no tags, no chakras, no manifest. A project that needed anatomy and nothing else
could take this on its own.

| Module | For |
|---|---|
| `utils/rig.py` | Placing limbs: vector maths, leaning and rotating, solving a joint to a target |
| `utils/skeleton.py` | Measuring what was placed: landmarks, bone lengths, joint angles, balance |

Read `utils/API.md` in the project directory for the signatures — it is
regenerated from these docstrings on every run, so it is never stale.

## Using it

The modules land in the project's `utils/`, so a package script reaches them
the usual way:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import rig, skeleton
```

## Changing it

**Edit the skill, not the copy.** `assets/<project>/utils/` is materialised
from the skills a project has enabled and is overwritten whenever that happens;
an edit made there is an edit that disappears. Write to
`skills/yoga-figure/utils/` and the change reaches every project that has this
enabled, with a new version to say so.

## What it will not do

It does not know what a *pose file* is. Reading and writing them, validating
their tags, and deciding what belongs in a package are `yoga-packages`; drawing
one is `yoga-pose-viewer`. Keep it that way — the moment this module imports a
manifest it stops being reusable by anything that is not yoga.
