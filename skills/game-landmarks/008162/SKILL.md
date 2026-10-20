---
name: game-landmarks
description: Modular building blocks and the nine many-builders landmarks, assembled from them as staged, animated GLB packages.
---

# Game landmarks: build buildings from blocks

A landmark is a real 3D model made of reusable **blocks** (walls, roofs,
lattices, decks, gates) that snap together on the tile grid. The game uses
its **construction stages** to show build progress, and shows its **scaffold**
while work is going on. This skill sits on `game-mesh`, and all of that skill's
conventions apply: Y up, 1 unit = 1 tile, origin at the footprint centre on
y = 0, unique node names, budgets, previews.

```python
from utils import blocks, landmarks, catalog
report = landmarks.build_landmark("tower", "out/tower")   # GLB + previews + manifest, validated
```

`utils/API.md` lists every signature.

## The landmark contract (the game relies on it)

| | |
|---|---|
| Footprint | Exactly the `LANDMARKS` entry in `~/games/many-builders/src/config.js`. `w` runs along +X, `h` along +Z. The front faces +Z. Overhangs may stick out a little (validation warns past +0.6). |
| Height | Follows the 2D part heights (`catalog.px`, 32 px = 1 unit). You can go somewhat taller and add more detail. |
| Root | One node named after the config key (`tower`, `109`). |
| Stages | `stage_00` … `stage_NN`, 6–10 of them, children of the root, **bottom-up**: foundation → structure → facade → roof → details. Every stage rests on earlier ones, so nothing floats when the game shows stages 0..k. Each stage has `extras.label`. |
| Scaffold | One `scaffold` node, a child of the root (tube scaffolding, plus a `crane` with a slewing `crane_jib` for tall builds). The game shows it while building. |
| Moving parts | Each moving part is its own node, inside the stage that builds it, with its pivot at the joint (`beacon`, `sign`, `lantern`, `noren`, `signal_N_red`). Clips: `beacon`/`sign`/`signals` blink with STEP scale (never scale 0), `lantern`/`noren` sway, `crane` slews. |
| Budget | 20 000 triangles for each landmark, scaffold included. Each block stays under 2 000. |

## Designing a new landmark

1. Add a `Spec(key, title, (w, d), style, px_height)` to `landmarks.SPECS`, and a
   builder `def _mykey(a: Assembly)` to `BUILDERS`.
2. In the builder, place blocks with `P(block, x, y, z, yaw)`. Stack them on
   sockets: `y = base.sockets["top"][1]`. For an exact wall chain use
   `block.socket("right", x, y, z, yaw)`.
3. Group the meshes into stages in build order, using `a.stage("walls", meshes, more_meshes)`.
   Moving parts go in with `a.detail(name, meshes, at=pivot, stage=st)`, and
   their clips with `a.blink(...)` / `a.sway(...)`.
4. Finish with `a.scaffold(P(blocks.scaffold(w, d, h)), crane=(height, jib, (x, y, z), yaw))`.
   Keep the crane mast clear of the building, and point the jib away from it
   (the jib swings ±35°).
5. Build it with `landmarks.build_landmark(key, out_dir)`. The report must be `ok`.

Pick a style from `blocks.STYLES` (`temple`, `shop`, `brick`, `steel`, `skytree`,
`modern`, `granite`, `palace`, `street`), or add one. A style recolours the
material **roles** (`wall`, `roof`, `glass`, `steel`, `steel_alt`, `facade`,
`column`, `stone`, …). Blocks name roles, never colours, so one landmark reads
as one design. Depict real buildings generically: no logos, brand names or
readable text on signs (`sign`/`screen` panels stay abstract).

## Adding a block

Write a function in `blocks.py` with defaults for every parameter. Build the
meshes in the block's own frame: origin at the footprint centre, on y = 0,
front +Z. Return `_block(name, meshes, **extra_sockets)`, which computes the
size and the standard `top/bottom/±x/±z` sockets. Then register the function
in `BLOCKS`. Wind every face outward (the tests check the signed volume),
because the preview culls back faces. For a polygon whose winding you are
unsure of, use `_closed()`. Moving sub-parts go in `Block.children`
(`{name: (block, pivot)}`). Export a single block to check it, or to ship it:
`blocks.export_block("torii", "out/torii", style="temple")`.

The kit: `slab plinth battered_base stairs wall window_wall door_wall
facade_box curtain_wall pillar colonnade beam truss lattice_tower
lattice_segment spire deck_ring deck_box roof_flat roof_gable roof_hip
roof_pyramid roof_mansard roof_curved roof_dome pagoda_tier torii lantern
stone_lantern railing railing_ring sign_panel noren street_lamp
traffic_light beacon tree incense_burner bridge_arch scaffold crane`.
`lattice_tower` accepts a `shape(y, width, sides)` function, which gives
morphing sections (Skytree's triangle-to-circle), and `mat=f(y)` for painted
bands.

## Checking the previews — open every PNG

`build_landmark` writes these previews to `previews/`. Scaffold is hidden
except in `construction.png`.

- `render.png`, `turntable.png`: does it read as the landmark from **all four**
  corners? Check the proportions against the real building and that the
  footprint is filled sensibly.
- `closeup.png`: look for gaps, z-fighting slabs, parts poking through roofs,
  and floating bits (lamps and trees must sit on a surface).
- `stage_strip.png`: each stage adds a visible, sensible chunk, from the
  bottom up, and no stage floats over a missing one.
- `construction.png`: the scaffold wraps the work and the crane clears the
  building.
- `top.png` (tall models) and `clip_<name>.png`: close-ups of the spire and of
  each animated part over its clip.

## Shipping

`catalog.export("out/tower")` copies a validated package into the game's
`public/assets/landmark/<key>/` and registers it in `index.json`. Only export
when the task asks you to.
