---
name: game-mesh
description: Core of the game-asset pipeline — meshes, textures, GLB writing with animation, previews, and package conventions; games pull validated packages from dex.
---

# Game mesh — the core every game asset is built on

Every visual asset for the games in `~/games/` is a **real 3D model** in a
binary glTF (`.glb`) file: textured, with its moving parts as separate nodes
and its animations as clips inside the file. Not pre-rendered sprites — the
game's camera rotates and zooms freely (orbit like an MMO camera), so assets
must look right from every side and at every distance.

```python
from utils import mesh, texture, gltf, preview, catalog
```

Read `utils/API.md` for every signature.

## The conventions (non-negotiable — the game relies on them)

| | |
|---|---|
| Axes | **Y up**, right-handed (glTF's own). Characters face **+Z**. |
| Scale | **1 unit = 1 map tile.** A 2x2 landmark is 2 x 2 units. `catalog.px(z)` converts the 2D renderer's pixel heights (32 px = 1 unit). |
| Origin | **Centre of the footprint, at ground level (y = 0).** Nothing below y = 0. |
| Names | Every node has a unique, meaningful name — the game finds parts by name (`stage_03`, `arm_l`, `barrel`). |
| Shading | Flat-shaded low-poly by default (crisp at any zoom). `smooth=True` for organic shapes. |
| Textures | Seamless, power-of-two (256 default). UVs are world units x `uv_scale`, so blocks sharing a texture line up across seams. |
| Night | Lit windows go in an `emissive_texture`; the game scales emissive strength with time of day. |
| Budgets | `catalog.TRIANGLE_BUDGET`: character 1500, block 2000, environment 3000, landmark 20000. Hundreds of builders are on screen at once. |

## Building a model

1. Make meshes with `mesh` primitives (`box`, `frustum`, `cylinder`, `cone`,
   `sphere`, `gable_roof`, `hip_roof`, `curved_roof`, `extrude`, `lathe`,
   `beam`, `array_around`) and their transforms (`translate`, `rotate_y`, …).
   Each mesh names a material.
2. Make a `gltf.Scene`, declare materials (`scene.material(name, texture=…,
   color=…, roughness=…, metallic=…, emissive_texture=…)`) using `texture`
   generators (`brick`, `tiles`, `planks`, `shingles`, `windows`, `metal`,
   `concrete`, `grass`, `snow`, `wood`, `bark`, `foliage`, `fabric`, `skin`,
   `stripes`, `lattice`, …).
3. Add nodes: `scene.node(name, meshes=[…], parent=…, translation=…,
   rotation=quat)`. Meshes on one node are merged per material automatically.
   **A part that moves must be its own node, pivot at its joint** (put the
   node's translation at the shoulder; build the arm mesh hanging from 0,0,0).
4. Animate: `clip = scene.animation("walk")`, then `clip.rotate(node, times,
   quats)` / `clip.translate(...)` / `clip.scale(...)`. Quaternions are
   `(x, y, z, w)` — use `mesh.quat_euler(x=, y=, z=)`. A looping clip's last key
   must equal its first.
5. `scene.write(path)`.

## Checking it — always, before you finish

- `gltf.validate(path)` → `errors` must be empty. It reads the file back.
- **Look at it.** `preview.render`, `preview.turntable` (4 corners),
  `preview.clip_strip` (frames of a clip), `preview.stage_strip`
  (construction stages) write PNGs from the *written file*. Open them and
  judge: proportions, silhouettes from every side, nothing floating or
  intersecting badly, animation reads clearly. Fix and re-render.
  Save them in the package's `previews/`.
- `catalog.write_manifest(...)`, then `catalog.validate_package(dir)` → `ok`.

## Tags and sets

Every package manifest carries `tags` as `group:value` strings; dex reads the
tags of a task's own top-level `manifest.json` into its library. Always tag the
asset's **type**: `type:player`, `type:enemy`, `type:landmark`,
`type:background`, `type:sfx` or `type:music`. Add more groups when useful
(`family:conifer`, `season:winter`).

A task that makes several packages (a family of trees, a category of sounds)
puts each in its own subdirectory with its own manifest, then writes
`catalog.write_set_manifest(task_dir, name=..., tags=[...])` at the top so the
set itself is tagged. `catalog.packages(dir)` lists a set's packages and
`catalog.export_all(dir, game_root)` installs them all into a game.

## How a package reaches a game

A task's deliverable is the **validated package in its task directory**, with
its previews judged. **Games pull packages from dex; dex does not push them.**
Each game's own `AGENTS.md` says what it takes from which dex project and how
it copies, converts and deploys it; the games in `~/games` share a puller,
`~/games/tools/pull_dex_assets.py`.

The pull installs each package with `catalog.export(package, game_root)`: it
refuses a package that does not validate, copies the listed files to
`<game_root>/public/assets/<kind>/<name>/`, and rebuilds
`<game_root>/public/assets/index.json` (the catalog the game's loader reads)
from every installed manifest, atomically, so concurrent installs cannot drop
each other's entries. `game_root` has no default: nothing in these skills
knows which games exist. A task calls `export` only when its brief explicitly
asks for it and names the game root.

## Audio packages

Sounds and music use the same package, manifest and install path (kinds `sfx`
and `music`; `REQUIRED_FIELDS` lists what their manifests carry). The
generators are the `game-sfx` and `game-music` skills.

## What it will not do

It does not design landmarks, characters or scenery — those are the
`game-landmarks`, `game-characters` and `game-environment` skills, built on
this one. No skinned meshes: animation is rigid node animation, which every
engine plays and which suits the blocky low-poly style.
