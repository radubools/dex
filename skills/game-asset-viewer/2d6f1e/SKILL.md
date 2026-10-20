---
name: game-asset-viewer
description: Opens game-asset .glb files in an orbitable 3D viewer with the game's camera, tile grid, clips, construction stages, team colours and day/night.
---

# Game asset viewer

One widget, `glb-viewer`, bound to **every `.glb` file** (`bind.json`). It is
how a person looks at what a `game-mesh` / `game-landmarks` /
`game-characters` / `game-environment` task produced, without exporting it to
the game first. Audio packages (`.ogg`, `.wav`) open in dex's built-in player.

What it shows, all read from the file (and the package's `manifest.json` when
there is one beside it):

- **Camera** — starts isometric (yaw 45°, pitch 30°, orthographic), orbits like
  the game's MMO camera: drag to rotate, right-drag to pan along the ground,
  scroll to zoom, never below the floor. `Ortho` toggles perspective; `Iso
  view` resets.
- **Scale** — a floor grid of 1-unit tiles, aligned so the manifest's
  footprint (or the bounds) covers whole tiles. Sun with shadows plus sky light.
- **Clips** — every animation clip: pick, play/pause, speed, loop.
  `idle` plays first if there is one.
- **Construction** — when the model has `stage_*` nodes, a `Build` slider shows
  them bottom-up in name order; a `scaffold` node is shown while unfinished.
- **Team** — a material named `team` is tinted with each of the game's
  `TEAM_COLORS`.
- **Night** — raises emissive strength (lit windows) and dims the sun.
- **Stats** — triangles, nodes, materials, clips, bounds in tiles, footprint,
  stage count.

So for a model to show well here, follow the `game-mesh` conventions: Y up,
1 unit = 1 tile, origin at ground level, `stage_NN` nodes, a `team` material,
emissive textures for lights. Nothing for a task to call — writing the `.glb`
is enough.
