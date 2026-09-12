---
name: yoga-sacrum-offset-follows-the-pelvis
description: Step the sacrum back along the pelvis's own facing, not the stance direction, or it sits off the midline and no shared check catches it.
metadata:
  type: project
---

`BOTTOM_OF_SACRAL_SPINE` is usually placed a few centimetres behind the hip
midpoint. Once the pelvis is rotated relative to the feet, stepping back along
the world/stance direction puts the sacrum nearer one hip than the other —
0.141 m vs 0.125 m at a 16° pelvic turn. Step along the pelvis's own facing
(`cross(pelvis_right, up)`) instead and both come out equal.

**Why:** `skeleton.check_limb_symmetry` walks the bilateral limbs only, so the
sacrum–hip pair is invisible to it and the figure ships asymmetric.

**How to apply:** when a pose turns the pelvis against the feet, derive every
pelvis-relative landmark from `pelvis_right`, and assert the two sacrum–hip
distances match to 1 mm as a package-local check. Related:
[[yoga-supine-pose-axes]], [[yoga-prone-pose-axes]].
