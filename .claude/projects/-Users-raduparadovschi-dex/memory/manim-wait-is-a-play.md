---
name: manim-wait-is-a-play
description: Scene.wait is implemented as self.play(Wait(...)), so overriding Scene.play to track elapsed time double-counts pauses and clamps them.
metadata:
  type: reference
---

In this manim version `Scene.wait(d)` ends with `self.play(Wait(run_time=d))`.
So a `TeachingScene` that overrides `play` to accumulate elapsed time sees every
pause twice, and an override signature like `def play(self, *args, run_time=1.0, **kwargs)`
silently rewrites every wait to 1.0s by passing its default down.

Hook `play` only, take the span from `kwargs["run_time"]` when present and
otherwise from `getattr(arg, "run_time", ...)` on the animations (a bare wait
arrives as a `Wait` carrying the right `run_time`), and do not override `wait`
at all. Symptom when you get it wrong: sections render at exactly the animation
time with no padding, and every `self.wait(1.4)` comes out as 1.0s.

Alternative mechanism for the same goal: [[fit-manim-sections-to-cues]].
Related: [[manim-section-length-dry-run]], [[render-manim-narration-shortest]].
