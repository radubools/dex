## `blocks`

Modular building blocks on the tile grid: walls, roofs, lattices, decks, gates — snap-together pieces.

- `palette(style: str='default') -> dict[str, dict]` — The full role -> spec table for a style (defaults overlaid with the style's overrides).
- `define_materials(scene: gltf.Scene, style: str='default', *, size: int=256) -> dict[str, dict]` — Declare every material role of `style` on `scene`; returns the palette used.
- `uv_density(role: str) -> float` — Texture repeats per world unit for a role (what `placed()` re-projects UVs with).
- `class Block` — A reusable piece: meshes in its own frame, bounding size (w, h, d), sockets, moving children.
- `slab(w: float=1.0, d: float=1.0, h: float=0.08, *, mat: str='plinth', edge: str | None=None) -> Block` — A floor slab or flat plinth w x d, h thick; `edge` adds a trim band round its top.
- `plinth(w: float=1.0, d: float=1.0, h: float=0.15, *, steps: int=2, inset: float=0.05, mat: str='stone', top: str | None=None) -> Block` — A stepped stone base: `steps` tiers each inset by `inset`; `top` is the top tier's material.
- `battered_base(w: float=2.0, d: float=2.0, h: float=0.3, *, batter: float=0.12, mat: str='stone', top: str | None='paving') -> Block` — A sloping castle-style stone wall base (ishigaki): footprint w x d, sides leaning in by `batter`.
- `stairs(width: float=0.5, height: float=0.2, depth: float=0.4, *, steps: int | None=None, mat: str='stone', cheeks: str | None=None) -> Block` — A flight of steps rising from the front (+Z, socket `bottom`) to the back (-Z, socket `top`).
- `wall(length: float=1.0, height: float=0.5, thickness: float=0.08, *, mat: str='wall') -> Block` — A plain wall segment along X (outer face +Z); sockets `left`/`right` at its ends for chaining.
- `window_wall(length: float=1.0, height: float=0.5, thickness: float=0.08, *, cols: int | None=None, rows: int=1, win_w: float=0.55, win_h: float=0.55, mat: str='wall', frame: str | None='trim', lit: float=0.3, seed: int=0, both: bool=False) -> Block` — A wall with `cols` x `rows` framed window panes on its +Z face (and -Z with `both`).
- `door_wall(length: float=1.0, height: float=0.5, thickness: float=0.08, *, door_w: float=0.3, door_h: float=0.32, mat: str='wall', door: str='door', frame: str='trim') -> Block` — A wall with a recessed doorway centred on its +Z face.
- `facade_box(w: float=1.0, h: float=1.0, d: float=1.0, *, mat: str='facade', cap: str | None='trim', base: str | None=None, base_h: float=0.0) -> Block` — A building mass whose faces carry the windows texture (cheap facade); optional cap and base band.
- `curtain_wall(w: float=1.0, h: float=1.0, d: float=1.0, *, mullion: float=0.2, floor: float=0.25, mat: str='glass', frame: str='frame', fin: float=0.02) -> Block` — A glass curtain-wall box with vertical mullion fins and floor bands on all four faces.
- `pillar(height: float=0.5, size: float=0.08, *, round_: bool=False, segments: int=8, mat: str='column', base: str | None='stone', cap: str | None=None) -> Block` — A corner pillar or column (square, or round with `round_`), with optional base and capital.
- `colonnade(length: float=1.0, height: float=0.4, count: int=5, *, size: float=0.06, round_: bool=True, mat: str='column', beam: str | None='column', beam_h: float=0.05, base: str | None='stone') -> Block` — A row of `count` columns along X with an architrave beam on top.
- `beam_block(p0: Sequence[float]=(0, 0, 0), p1: Sequence[float]=(1, 0, 0), thickness: float=0.04, *, mat: str='steel') -> Block` — One straight beam or lattice member from p0 to p1; sockets `start` and `end`.
- `truss(length: float=1.0, height: float=0.2, *, panels: int=4, thickness: float=0.025, mat: str='steel') -> Block` — A flat Warren truss in the XY plane: top and bottom chords with alternating diagonals.
- `lattice_tower(levels: Sequence[tuple[float, float]]=((0, 1.0), (1, 0.6), (2, 0.3)), *, sides: int=4, shape: Callable[[float, float, int], list] | None=None, leg: float=0.05, brace: float=0.025, mat: RoleFn='steel', rings: bool=True, cross: bool=True, sub: int=1) -> Block` — A lattice tower through `levels` [(y, width)]: legs, horizontal rings and X-bracing per panel.
- `lattice_segment(w0: float=1.0, w1: float=0.6, h: float=1.0, *, panels: int=3, **kw) -> Block` — A straight tapering four-legged lattice section, base w0 to top w1 over h; stacks on `top`.
- `spire(height: float=1.0, radius: float=0.05, *, sections: int=3, mat: RoleFn='steel', ring: str | None='steel_alt', segments: int=8, tip: str | None=None) -> Block` — An antenna/spire: stacked cylinders narrowing upward with ring collars, and an optional tip light.
- `deck_ring(radius: float=0.4, height: float=0.25, *, sides: int=16, levels: int=1, glass: str='glass', trim: str='steel_alt', flare: float=0.25) -> Block` — A round observation deck: flared underside, `levels` glass bands with trim rings, capped top.
- `deck_box(w: float=1.0, h: float=0.25, d: float=1.0, *, floors: int=2, glass: str='glass', trim: str='steel_alt') -> Block` — A square observation deck: glazed floors between trim slabs, slightly wider roof slab.
- `roof_flat(w: float=1.0, d: float=1.0, *, thickness: float=0.04, parapet: float=0.05, mat: str='roof_flat', trim: str='trim') -> Block` — A flat roof slab with a parapet ring.
- `roof_gable(w: float=1.0, d: float=1.0, h: float=0.35, *, overhang: float=0.06, mat: str='roof', trim: str | None='roof_trim') -> Block` — A gable roof, ridge along X, with a ridge cap.
- `roof_hip(w: float=1.0, d: float=1.0, h: float=0.3, *, overhang: float=0.06, mat: str='roof', trim: str | None='roof_trim') -> Block` — A hip roof, four slopes and a ridge along X, with a ridge cap.
- `roof_pyramid(w: float=1.0, d: float=1.0, h: float=0.4, *, overhang: float=0.04, mat: str='roof', finial: str | None='gold') -> Block` — A pyramid roof (square towers, lantern caps) with an optional finial.
- `roof_mansard(w: float=1.0, d: float=0.6, h: float=0.3, *, inset: float=0.1, mat: str='roof', top: str='roof_trim', dormers: int=0, dormer_mat: str='trim') -> Block` — A mansard roof: steep lower slopes, low hip on top, optional dormers along both long sides.
- `roof_curved(w: float=1.0, d: float=1.0, h: float=0.4, *, overhang: float=0.2, lift: float=0.1, thickness: float=0.04, gable: bool=False, steps: int=5, segments: int=4, mat: str='roof', trim: str | None='roof_trim', ornaments: str | None=None) -> Block` — An East Asian roof: concave slopes, eaves sweeping up at the corners, thick eave edge, ridge cap.
- `roof_dome(radius: float=0.3, height: float=0.35, *, sides: int=8, mat: str='roof', drum: float=0.0, drum_mat: str='trim', finial: str | None='gold') -> Block` — A faceted dome (octagonal by default) on an optional drum, with a finial.
- `pagoda_tier(w: float=0.5, wall_h: float=0.25, *, roof_h: float | None=None, overhang: float | None=None, lift: float=0.06, wall: str='column', roof: str='roof', trim: str='roof_trim', bracket: str='wood_dark', railing_mat: str | None=None) -> Block` — One pagoda storey: walls with corner posts, a bracket band, a wide curved roof; `top` is where the next sits.
- `torii(width: float=0.6, height: float=0.5, *, post: float=0.045, mat: str='column', top: str='roof_trim') -> Block` — A torii gate across X: two posts, tie beam (nuki), upswept black lintel (kasagi).
- `lantern(radius: float=0.12, height: float=0.3, *, mat: str='lantern', band: str='roof_trim', segments: int=10) -> Block` — A paper lantern (chochin): a barrel with dark rings top and bottom; hang it from `top`.
- `stone_lantern(height: float=0.25, *, mat: str='stone', light: str='lamp') -> Block` — A stone garden lantern (toro): base, post, light box, pyramid cap.
- `railing(length: float=1.0, height: float=0.08, *, post_every: float=0.15, mat: str='trim', thickness: float=0.012) -> Block` — A railing along X: posts, a top rail and a mid rail.
- `railing_ring(w: float=1.0, d: float=1.0, height: float=0.08, *, mat: str='trim', post_every: float=0.15) -> Block` — Railings round a w x d rectangle (balconies, deck edges).
- `sign_panel(w: float=0.6, h: float=0.3, *, depth: float=0.03, mat: str='sign', frame: str='frame', posts: float=0.0) -> Block` — A blank signage / screen panel facing +Z in a frame, optionally raised on two posts.
- `noren(w: float=0.5, h: float=0.15, *, panels: int=3, mat: str='fabric', rod: str='wood_dark') -> Block` — Split shop-entrance curtains hanging below a rod; the block's top is the rod.
- `street_lamp(height: float=0.3, *, mat: str='frame', lamp: str='lamp') -> Block` — A street lamp: pole, arm, glowing head over +Z.
- `traffic_light(height: float=0.3, *, mat: str='frame') -> Block` — A traffic signal post facing +Z, with `red` and `green` lamps as child parts (animate their scale).
- `beacon(radius: float=0.03, *, mat: str='light') -> Block` — An aviation warning light (a glowing ball) — make it a child node and blink its scale.
- `tree(height: float=0.4, radius: float=0.15, *, kind: str='pine', trunk: str='wood_dark', leaves: str='foliage') -> Block` — A low-poly tree: `pine` (pruned cloud pads, Japanese garden style) or `round`.
- `incense_burner(size: float=0.2, *, mat: str='bronze', roof: str='bronze') -> Block` — A temple incense burner (jokoro): bowl on legs under a small curved roof on posts.
- `bridge_arch(length: float=1.0, width: float=0.3, height: float=0.2, *, arches: int=2, mat: str='stone', deck: str | None='paving', rail: str | None='stone') -> Block` — A stone arch bridge along X with `arches` round arches (spectacles bridge) and parapets.
- `scaffold(w: float=1.0, d: float=1.0, h: float=1.0, *, bay: float=0.35, lift: float=0.25, pole: float=0.02, mat: str='scaffold', plank: str='scaffold_plank', net: str | None=None, braces: bool=True, plank_every: int=2, depth: float=0.1) -> Block` — Tube-and-board scaffolding wrapping a w x d box up to h: posts, ledgers, boards, diagonal braces.
- `crane(height: float=2.0, jib: float=1.2, *, mat: str='crane', weight: str='wall_alt', cab: str='trim') -> Block` — A tower crane: lattice mast, with the slewing jib as child part `jib` (pivot at the mast top).
- `add_block(scene: gltf.Scene, block: Block, name: str, *, parent: gltf.Node | None=None, translation: Sequence[float]=(0, 0, 0), yaw: float=0.0, **extras) -> gltf.Node` — Add a block as a node subtree: its meshes on `name`, each child part as node `<name>_<child>`.
- `export_block(name: str, out_dir: str | Path, *, style: str='default', block: Block | None=None, **params) -> dict` — Write one block as a `kind="block"` package (GLB, previews, manifest); returns `validate_package`.

## `landmarks`

The game's landmarks, assembled from `blocks` into staged, animated GLB packages.

- `class Spec` — A landmark as the game knows it: config key, title, footprint (tiles along X, Z), style.
- `class Assembly` — Builds one landmark scene: stages in order, detail nodes inside stages, scaffold, clips.
- `P(block: B.Block, x: float=0.0, y: float=0.0, z: float=0.0, yaw: float=0.0) -> list[Mesh]` — Shorthand: a block's meshes placed in the landmark frame.
- `build_scene(key: str) -> Assembly` — Assemble landmark `key` (a config.js LANDMARKS key) into a scene, without writing it.
- `render_previews(glb: str | Path, out_dir: str | Path) -> list[Path]` — Write a landmark's previews: render, turntable, stage_strip, construction, top and clip close-ups.
- `build_landmark(key: str, out_dir: str | Path, *, previews: bool=True) -> dict` — Build landmark `key` as a package in `out_dir` (GLB, previews/, manifest); returns `validate_package`.
- `build_all(out_root: str | Path, *, keys: Sequence[str] | None=None, previews: bool=True) -> dict[str, dict]` — Build every landmark (or `keys`) into `out_root/<key>/`; returns {key: validate_package report}.