---
name: narrated-video
description: Plays a generated video with its narration track and caption sidecar.
---

# Narrated video

A widget for reviewing a render **with the words it was built around**. Bound
to `.mp4` and `.webm`; it finds the caption sidecar beside the video.

This is a viewer and nothing else — no modules, nothing to import. Any project
that renders narrated video can enable it.

## Rendering

It does not render anything. That is `dex.tools.render_manim`, which ships with
dex; the project guide has the command.
