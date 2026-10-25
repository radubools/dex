## `motion`

Procedural animation clips for `rigs` characters: poses in, baked node keys out.

- `ALWAYS_TRANSLATE` — constant
- `smooth(u: float) -> float` — Smoothstep 0..1 easing.
- `pulse(t: float, t0: float, t1: float, peak: float=1.0, low: float=rigs.HIDDEN) -> float` — `peak` between t0 and t1, `low` elsewhere — for a muzzle flash scale.
- `keyframes(keys: Sequence[tuple[float, Pose]], *, ease: bool=True) -> PoseFn` — A pose function blending key poses [(time, pose)]; a key missing from a pose is neutral.
- `merge(*poses: Pose) -> Pose` — Combine poses; rotations and offsets of the same part add up, scales multiply.
- `mirror(pose: Pose) -> Pose` — The same pose with left and right swapped (Y/Z rotations and X offsets negated).
- `leg_ik(thigh: float, shin: float, forward: float, down: float) -> tuple[float, float, float]` — Two-bone leg IK in the sagittal plane: (thigh, shin, foot) X rotations in degrees.
- `thigh_angles(rig: rigs.Rig, pose: Pose) -> tuple[float, float]` — (left, right) thigh X angles relative to the hips, as `bake` resolves a humanoid pose (leg IK included).
- `bake(rig: rigs.Rig, name: str, duration: float, pose_fn: PoseFn, *, loop: bool=True, fps: float=30.0, min_keys: int=16, show: Iterable[str]=(), hide: Iterable[str]=(), extra_times: Iterable[float]=(), **info) -> gltf.Clip` — Sample `pose_fn` over `duration` s into a clip on the rig's built scene; loops close exactly.
- `class Gait` — A planned walk/run cycle: everything derived from leg length and travel speed.
- `class Style` — Personality knobs shared by all of a character's clips.
- `plan_gait(body: rigs.Body, speed: float, *, run: bool=False, style: Style | None=None) -> Gait` — Choose period, stride and stance so a planted foot moves at exactly `speed` tiles/s.
- `stance(style: Style) -> Pose` — The relaxed base pose: arms a little out, elbows a little bent, the style's hunch.
- `locomotion(rig: rigs.Rig, gait: Gait, style: Style | None=None) -> PoseFn` — The walk/run pose function for a gait: IK feet on a stance/swing path, arms in counter-swing.
- `rest_points(rig: rigs.Rig, *, include_hidden: bool=False) -> np.ndarray` — Every vertex of the rig in the rest pose, in the root frame.
- `part_world(rig: rigs.Rig, pose: Pose, part: str) -> np.ndarray` — The 4x4 root-frame transform of a rig part in a pose (legs IK-solved as `bake` does).
- `reach_arm(rig: rigs.Rig, pose: Pose, side: str, target: Sequence[float], guess: Sequence[float], *, palm: float | None=None, max_bend: float=160.0) -> tuple[np.ndarray, float]` — Humanoid arm (x, y, z) and forearm x angles putting a hand's centre on `target`: (angles, miss in tiles).
- `idle(rig: rigs.Rig, style: Style | None=None, *, period: float=2.4) -> gltf.Clip` — Breathing idle: chest rise, slow look-around, a little knee give (loop).
- `walk_cycle(rig: rigs.Rig, name: str, speed: float, *, run: bool=False, style: Style | None=None, **bake_kw) -> Gait` — Bake a no-slide walk or run cycle authored for `speed` tiles/s; returns the gait.
- `work_hammer(rig: rigs.Rig, style: Style | None=None, *, period: float=0.9, prop: str='hammer', hide: Iterable[str]=()) -> gltf.Clip` — Hammering swing with the right arm (loop); `event` is the impact time.
- `work_direct(rig: rigs.Rig, style: Style | None=None, *, period: float=2.2, hide: Iterable[str]=()) -> gltf.Clip` — A foreman's work: read the clipboard in the left hand, look up, point and jab (loop).
- `work_smash(rig: rigs.Rig, style: Style | None=None, *, period: float=0.7, name: str='work') -> gltf.Clip` — Alternate fists pounding down in front — a creature wrecking a structure (loop).
- `attack_shotgun(rig: rigs.Rig, style: Style | None=None, *, period: float=1.1, fire: float=0.12, gun: str='shotgun', stowed: Iterable[str]=('shotgun_back',), flash: str='muzzle_flash', hide: Iterable[str]=()) -> gltf.Clip` — Aim, fire with recoil and a muzzle flash, settle (loop, one shot per cycle: period = cooldown).
- `attack_melee(rig: rigs.Rig, style: Style | None=None, *, period: float=1.0, strike: float=0.5) -> gltf.Clip` — Double-fist overhead smash with a lunge (loop, one blow per cycle: period = cooldown).
- `hit(rig: rigs.Rig, style: Style | None=None, *, duration: float=0.45, hide: Iterable[str]=()) -> gltf.Clip` — A flinch back and recover (one-shot, ends in the base stance so it blends out).
- `die(rig: rigs.Rig, style: Style | None=None, *, duration: float=1.3, forward: bool=False, hide: Iterable[str]=()) -> gltf.Clip` — Stagger, topple onto the back (or face), bounce once and lie still (one-shot, holds the last pose).
- `cheer(rig: rigs.Rig, style: Style | None=None, *, period: float=0.9, kind: str='jump', hide: Iterable[str]=()) -> gltf.Clip` — Celebrate: `jump` (hop, both arms pumping) or `stomp` (side-to-side gloat, one fist up) — loop.
- `slam(rig: rigs.Rig, style: Style | None=None, *, duration: float=1.7, impact: float=0.9) -> gltf.Clip` — A big creature's ground slam: crouch, leap, both fists into the ground, recover (one-shot).
- `standard_clips(rig: rigs.Rig, *, walk_speed: float, run_speed: float, style: Style | None=None, attack: str='shotgun', attack_period: float=1.0, work: str='hammer', cheer_kind: str='jump', fall_forward: bool=False, hide_when_busy: Iterable[str]=()) -> dict[str, dict]` — Bake all of `catalog.CHARACTER_CLIPS` onto a built humanoid rig; returns `rig.clip_info`.
- `turret_clips(rig: rigs.Rig, *, cooldown: float=0.5) -> dict[str, dict]` — The standard clip names for a `rigs.turret`: scan, fire with recoil, shake, collapse.
- `check(path, clip_info: dict[str, dict], *, feet: Sequence[str]=('foot_l', 'foot_r'), max_slip: float=0.02, floor_tolerance: float=0.01) -> dict` — Read a written `.glb` back and check its clips: loops close, nothing under the floor

## `rigs`

Parametric blocky character rigs: a tree of rigid parts, each pivoting at its joint.

- `HIDDEN` — constant
- `DEFAULT_TEAM_COLOR` — constant
- `DEFAULT_COLORS` — constant
- `block(w: float, h: float, d: float, color: str, *, x: float=0.0, y: float=0.0, z: float=0.0) -> mesh.Mesh` — A w x h x d box centred on (x, y, z), in material/colour `color`.
- `mirror_x(m: mesh.Mesh) -> mesh.Mesh` — Mirror a mesh left/right (X), keeping faces outward — left part to right part.
- `loft(rings: Sequence[Sequence[float]], color: str, *, caps: bool=True) -> mesh.Mesh` — A flat-shaded solid through chamfered-rectangle rings (y, w, d, chamfer[, dx, dz]), listed in order of y.
- `class Part` — One rig part: becomes one glTF node.
- `class Rig` — A named tree of parts plus palette; `build()` makes the scene, `nodes` maps names to nodes.
- `class Body` — Proportions of a blocky humanoid, in tiles. Defaults are a builder (~0.8 tall with a hat).
- `humanoid(name: str, body: Body | None=None, *, colors: dict[str, str] | None=None, glow: Iterable[str]=(), team_color: str=DEFAULT_TEAM_COLOR) -> Rig` — A blocky humanoid rig (hierarchy in the module doc) with default block meshes per part.
- `class TurretSpec` — Proportions of an auto turret, in tiles.
- `turret(name: str, spec: TurretSpec | None=None, *, colors: dict[str, str] | None=None) -> Rig` — An auto turret rig: base → yaw (turns about Y) → pitch (tilts about X) → barrel (recoils on Z) → muzzle_flash.

## `units`

The Many Builders cast as animated GLB packages: builder, foreman, clone, boss, turret.

- `CONFIG_JS` — constant
- `DEFAULT_CONFIG` — constant
- `MATERIAL_NOTES` — constant
- `game_config(path: str | Path=CONFIG_JS) -> dict[str, dict[str, float]]` — Numeric fields of BUILDER, CLONE, BOSS and TURRET from the game's config.js (defaults if absent).
- `shotgun_mesh() -> list[mesh.Mesh]` — A pump shotgun along +Z, top +Y, origin at the grip; muzzle at z = 0.26.
- `hammer_mesh() -> list[mesh.Mesh]` — A claw hammer continuing the arm (-Y from the grip), striking face toward -Z.
- `flash_mesh(size: float=1.0) -> list[mesh.Mesh]` — A muzzle-flash star pointing along -Y (the barrel of a hand-held gun).
- `builder_rig(name: str='builder', *, foreman: bool=False) -> rigs.Rig` — The construction worker: hard hat, hi-vis `team` vest, tool belt, shotgun; foreman variant.
- `GRUMP_COLORS` — constant
- `ELDER_COLORS` — constant
- `grump_body() -> rigs.Body` — Proportions of a Grump (the clone): squat, wide, tiny legs and stubby arms.
- `grump_rig(name: str='clone', *, elder: bool=False) -> rigs.Rig` — A Grump (clone), or with `elder` the boss: 3x, long gorilla arms, horns, glowing eyes, ice spikes.
- `make(name: str, cfg: dict | None=None) -> tuple[rigs.Rig, dict]` — The built, animated rig for a unit name plus its manifest fields (nothing written).
- `closeup_turntable(path: str | Path, out: str | Path, *, yaws=(0, 45, 90, 135, 180, 270), size: int=200, pitch: float=20.0)` — The rest pose from several sides, zoomed to the character (no floor) — judge silhouettes here.
- `closeup_strip(path: str | Path, out: str | Path, clip: str, *, frames: int=8, size: int=180, yaw: float=60.0, pitch: float=15.0)` — Evenly spaced frames of a clip, zoomed on one shared frame whose bottom edge is the floor (y=0).
- `write_package(rig: rigs.Rig, meta: dict, out_dir: str | Path, *, previews: bool=True, frames: int=8) -> dict` — Write a built rig as a character package: GLB, previews, manifest; validate and check its clips.
- `build_unit(name: str, out_dir: str | Path, *, previews: bool=True, cfg: dict | None=None) -> dict` — Build one unit (builder, foreman, clone, boss, turret) as a validated package in `out_dir`.
- `build_all(out_root: str | Path, *, previews: bool=True) -> dict[str, dict]` — Build every unit into `out_root/<name>/`; returns {name: report}, also saved as `report.json`.