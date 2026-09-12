---
name: yoga-hooked-elbow-lands-below-the-knee
description: In a lunge twist the elbow hooked "outside the front knee" ends up below it, and the upper arm is what presses the knee.
metadata:
  type: project
---

An elbow hooked outside a front knee does not sit level with it. From a
shoulder folded over the thigh (~0.62 m) a 0.30 m upper arm reaches ~0.43 m,
which is below the 0.50 m knee — so the elbow hangs outside the *shin*, and
what actually contacts the knee is the shoulder–elbow bone passing it.

**Why:** I banded the check on `LEFT_ELBOW` being level with `RIGHT_KNEE` and it
failed on a correct figure; the band, not the pose, was wrong. Reaching the
elbow up to knee height instead needs a lateral trunk shift that puts the
centre of mass outside the feet.

**How to apply:** check the hook as `point_to_bone(knee, shoulder, elbow)` in
0.04–0.12 m plus a lateral offset `elbow.x - knee.x` of 0.04–0.13; let the
elbow's own height run from the knee down to a bit above the ankle. See
[[yoga-lunge-hands-land-ahead-of-front-foot]] and
[[yoga-brief-mixes-entry-and-held-shape]] — the same trap of typing a cue's
words as coordinates.
