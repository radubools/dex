---
name: yoga-full-lotus-shins-cannot-stack
description: Full lotus is out of reach for an adult's equal femur/tibia, and the mid-thigh fallback leaves the two shins unable to clear each other
metadata:
  type: project
---

A tibia the same length as the femur (0.42 m each, which is adult proportion)
cannot put a foot in the opposite hip crease once the knees are wide enough to
be a lotus: knees 0.45 m apart sit ~0.40 m forward of the hips, and the span
from one knee to the far crease is ~0.47 m. The foot lands at mid-thigh instead.

**Why:** from mid-thigh the two shins run alongside each other rather than
crossing at a point, and no arrangement separates them by more than ~2 cm.
Raising the top knee is cancelled by its ankle, which rides the *lower* thigh —
the separation works out to `(top_rise·(1-t_bot) − bot_rise·(1-t_top))/2`, which
is tiny for any plausible rises. Chasing it drives the knee past 150° of flexion.

**How to apply:** for a lotus-named pose, build the crossed seat — state where
each ankle crosses to (~0.15 m past the midline, ~0.20 m forward) and solve the
knee with `rig.two_bone`, which puts the knee angle under the hip-to-ankle
distance where you can read it. A ±0.035 m ankle-rise difference then gives the
top shin a clean ~7 cm over the bottom one. Say in the build docstring that it
is a crossed seat and why. See [[yoga-hand-to-foot-grip-solve-reach-first]] for
the same lesson in a different limb.
