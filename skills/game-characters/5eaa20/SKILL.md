---
name: game-characters
description: Animated low-poly characters — parametric blocky rigs, procedural clips with no-slide locomotion, and the Many Builders cast as GLB packages.
---

# Game characters — rigs, clips and the cast

Characters are real 3D GLB models seen from every side under a free orbit
camera: blocky low-poly parts, **rigid node animation** (no skinning), every
clip in `catalog.CHARACTER_CLIPS` inside the one file. Built on `game-mesh`
— its conventions (Y up, faces +Z, 1 unit = 1 tile, origin at the feet,
unique node names, 1500-triangle budget) all apply.

```python
from utils import rigs, motion, units, preview, catalog
```

Read `utils/API.md` for every signature.

## The cast — rebuild or extend it

`units.build_unit(name, out_dir)` writes a validated package (GLB,
`previews/`, `manifest.json`); `units.build_all(out_root)` does all five:
`builder`, `foreman`, `clone`, `boss` (extra `slam` clip), `turret`. Speeds,
cooldowns and radii are read from the game's `src/config.js`
(`units.game_config()`), so **re-run after rebalancing speeds**.

The enemies are an **original** creature, the *Grump* (squat frost-furred
grouch: underbite, tusks, red nose, unibrow, toque; the boss is the horned,
icicle-crowned *Grump Elder*). The game's text names a cartoon character —
never model, name or imitate any existing copyrighted character.

## The rig and the pivot rule

`rigs.humanoid(name, rigs.Body(...))` gives the hierarchy
`<name> → body → hips → leg_l/shin_l/foot_l, torso → head, arm_l/forearm_l/hand_l`
(and `_r`), plus `hand_r_socket` for tools. **Every moving part is its own
node with its origin at the joint**; its meshes are authored in that local
frame, hanging along -Y. Character's left is **+X**. Negative X rotation
swings a limb forward.

- New character: change `Body` proportions (`Body().scaled(k, arm_w=…)` for a
  big variant), then decorate with `rig.add(part, rigs.block(w, h, d, colour, x=, y=, z=))`
  in that part's frame. Mirror left→right with `rigs.mirror_x`.
- Tools/weapons: `rig.prop(name, parent, meshes, hidden=True)`. Props are
  shown/hidden per clip by scale (`show=`/`hide=` on clips); every clip keys
  every prop, so nothing leaks between clips.
- Non-humanoids: build a `rigs.Rig` with `rig.part(...)` (see `rigs.turret`).
- `rig.build()` makes the `gltf.Scene`; then bake clips; then `scene.write`.

## Materials and team tinting

Three materials per character (few draw calls — hundreds on screen):
`palette` (all flat colours, one atlas; colour names are mesh material names,
add with `rig.color`), `glow` (emissive colours), and **`team`** — clothing
the game tints: it sets that material's base colour to `TEAM_COLORS[team]`
(it ships as `TEAM_COLORS[0]`, `extras.tint = "team"`). Put a mesh in it by
naming its material `rigs.TEAM`. Keep `team` on large, visible-from-above
surfaces (vest, shoulders) so teams read at a distance.

## Clips

`motion.standard_clips(rig, walk_speed=, run_speed=, attack="shotgun"|"melee",
work="hammer"|"direct"|"smash", …)` bakes all eight. Or one at a time:
`idle`, `walk_cycle`, `work_hammer`, `attack_shotgun`, `attack_melee`, `hit`,
`die`, `cheer`, `slam`.

- **Add a clip**: write a pose function or `motion.keyframes([(t, pose), …])`
  and `motion.bake(rig, name, duration, fn, loop=…, event=…)`. A pose is
  `{"arm_r": (x, y, z) degrees, "hips.pos": offset, "prop.scale": s,
  "_foot_l": (forward, lift)}`. Legs are solved by IK: feet stay planted
  unless the pose moves them, so crouches never sink a foot. `_ik: 0` for
  falls. `motion.mirror(pose)` swaps sides. Loops close exactly (`bake` copies
  the first key to the last).
- **Locomotion is computed**: `plan_gait` picks period/stride/stance so a
  planted foot moves at exactly the authored speed (stubby creatures use
  `Style(hop=True)`). The manifest records each clip's `speed`; the game plays
  it with `timeScale = actual speed / clip speed`, and `locomotion.clip` names
  the clip matching the unit's move speed. `event` is the impact/fire time.
- Non-looping: `hit` (ends in the base stance), `die` (holds lying down),
  `slam`. Only `die` may go below the floor.

## Checking it — always

- `motion.check(glb, rig.clip_info)` — loops close, nothing under the floor
  (except `die`), planted-foot slip < 0.02 tiles. `units.write_package` runs it
  and `catalog.validate_package`; both must be clean.
- **Look at the previews** (`previews/closeup.png`, `clip_<clip>.png`, and
  `turntable.png` on the tile floor for scale). In clip strips check: limbs
  turn about their joints (no gaps, nothing detaching), walk/run alternate
  legs with opposite arms, feet on the floor bottom edge, props appear only in
  their clip, the silhouette reads from every side at small size.
- Budget: `catalog.TRIANGLE_BUDGET["character"]` (1500); the cast uses 500–700.

## Shipping

`catalog.export(package_dir)` copies a validated package into the game and
registers it in `public/assets/index.json`. It refuses packages that fail.
