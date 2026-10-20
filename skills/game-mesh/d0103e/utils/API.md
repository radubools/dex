## `catalog`

Game-asset conventions, package manifests, validation, and export into the game.

- `UNITS_PER_TILE` — constant
- `PIXELS_PER_UNIT` — constant
- `GAME_ROOT` — constant
- `EXPORT_SUBDIR` — constant
- `KINDS` — constant
- `CHARACTER_CLIPS` — constant
- `TRIANGLE_BUDGET` — constant
- `SAMPLE_RATE` — constant
- `NAME` — constant
- `REQUIRED_FIELDS` — constant
- `MODEL_KINDS` — constant
- `px(pixels: float) -> float` — A height from the 2D renderer's pixels, in world units.
- `write_manifest(package: str | Path, *, name: str, kind: str, files: Sequence[str], title: str='', **fields: Any) -> Path` — Write `manifest.json` for a package; `files` are paths relative to it.
- `read_manifest(package: str | Path) -> dict` — The package's manifest.
- `wav_duration(path: str | Path) -> float` — Length of a WAV file in seconds.
- `validate_package(package: str | Path) -> dict` — Check a package against its kind's contract; `ok` is what "done" means.
- `export(package: str | Path, game_root: str | Path=GAME_ROOT) -> dict` — Copy a valid package's files into the game and register it in `public/assets/index.json`.

## `gltf`

Write and read binary glTF 2.0 (`.glb`): node hierarchies, PBR materials, textures, animations.

- `class Node` — One glTF node: a transform, optional meshes, children.
- `class Material` — A metallic-roughness PBR material, optionally textured and emissive.
- `class Clip` — One animation clip: channels keyed against node translation, rotation or scale.
- `class Scene` — Accumulates nodes, materials and clips, then writes one `.glb`.
- `class Gltf` — A parsed `.glb`: its JSON document and binary chunk.
- `read(path: str | Path) -> Gltf` — Parse a `.glb` file.
- `validate(path: str | Path, *, max_triangles: int | None=None) -> dict` — Check a `.glb` loads and is sane; return a report with `errors`, counts, bounds and clips.

## `mesh`

Low-poly triangle meshes for game assets: primitives, transforms, UVs, merging.

- `class Mesh` — Triangles with one material: positions, normals, UVs, and an index list.
- `translation(x: float, y: float, z: float) -> np.ndarray` — A 4x4 translation matrix.
- `rotation_matrix(axis: Sequence[float], degrees: float) -> np.ndarray` — A 4x4 rotation about `axis` by `degrees` (right-handed).
- `quat_axis_angle(axis: Sequence[float], degrees: float) -> tuple[float, float, float, float]` — A unit quaternion (x, y, z, w) — glTF's order — rotating about `axis`.
- `quat_mul(q: Sequence[float], r: Sequence[float]) -> tuple[float, float, float, float]` — Hamilton product q*r of two (x, y, z, w) quaternions: r first, then q.
- `quat_euler(x: float=0.0, y: float=0.0, z: float=0.0) -> tuple[float, float, float, float]` — Quaternion from Euler degrees, applied X then Y then Z (intrinsic ZYX).
- `quat_matrix(q: Sequence[float]) -> np.ndarray` — 3x3 rotation matrix of an (x, y, z, w) quaternion.
- `from_faces(positions: Sequence[Sequence[float]], faces: Iterable[Sequence[int]], material: str='default', *, smooth: bool=False, uv_scale: float=1.0, uvs: np.ndarray | None=None) -> Mesh` — Build a mesh from shared vertices and polygon faces (fanned, counter-clockwise).
- `merge(meshes: Iterable[Mesh], material: str | None=None) -> Mesh` — Concatenate meshes into one; they must share a material unless one is given.
- `group(meshes: Iterable[Mesh]) -> list[Mesh]` — Merge meshes per material: one mesh per material, fewest draw calls.
- `box(w: float, h: float, d: float, material: str='default', *, uv_scale: float=1.0, centered_y: bool=False) -> Mesh` — A w (X) by h (Y) by d (Z) box, sitting on y=0 unless `centered_y`.
- `frustum(w0: float, d0: float, w1: float, d1: float, h: float, material: str='default', *, uv_scale: float=1.0, shift: tuple[float, float]=(0.0, 0.0)) -> Mesh` — A tapered box: base w0 x d0 on y=0, top w1 x d1 at y=h, top offset by `shift`.
- `pyramid(w: float, d: float, h: float, material: str='default', **kw) -> Mesh` — A four-sided pyramid on y=0 with its apex at y=h.
- `plane(w: float, d: float, material: str='default', *, uv_scale: float=1.0) -> Mesh` — A flat w x d rectangle at y=0 facing up.
- `cylinder(radius: float, height: float, material: str='default', *, segments: int=12, radius_top: float | None=None, caps: bool=True, smooth: bool=False, uv_scale: float=1.0) -> Mesh` — A cylinder (or cone/frustum with `radius_top`) on y=0, axis along Y.
- `cone(radius: float, height: float, material: str='default', **kw) -> Mesh` — A cone on y=0 with its tip at y=height.
- `sphere(radius: float, material: str='default', *, segments: int=12, rings: int=8, smooth: bool=True, centered: bool=True) -> Mesh` — A UV sphere, centred on the origin (or resting on y=0 when not `centered`).
- `gable_roof(w: float, d: float, h: float, material: str='default', *, overhang: float=0.0, uv_scale: float=1.0) -> Mesh` — A gable roof on y=0: ridge along X at height h, slopes facing ±Z.
- `hip_roof(w: float, d: float, h: float, material: str='default', *, overhang: float=0.0, ridge: float | None=None, uv_scale: float=1.0) -> Mesh` — A hip roof: four slopes, a ridge along X of length `ridge` (default w - d).
- `curved_roof(w: float, d: float, h: float, material: str='default', *, overhang: float=0.2, lift: float=0.15, steps: int=4, uv_scale: float=1.0) -> Mesh` — A concave East Asian hip roof; `lift` raises the eave line (use `blocks.roof_curved` for corner upsweep).
- `extrude(polygon: Sequence[tuple[float, float]], height: float, material: str='default', *, uv_scale: float=1.0, caps: bool=True) -> Mesh` — Extrude a simple 2D polygon of (x, z) points, in either winding, from y=0 up to `height`.
- `triangulate(pts: Sequence[tuple[float, float]]) -> list[tuple[int, int, int]]` — Ear-clip a simple 2D polygon into triangles of its point indices.
- `lathe(profile: Sequence[tuple[float, float]], material: str='default', *, segments: int=16, smooth: bool=True, uv_scale: float=1.0) -> Mesh` — Spin a profile of (radius, y) points, bottom to top, around the Y axis.
- `beam(p0: Sequence[float], p1: Sequence[float], thickness: float, material: str='default', *, depth: float | None=None, uv_scale: float=1.0) -> Mesh` — A square-section bar from p0 to p1 — struts, lattice members, poles, rails.
- `array_around(m: Mesh, count: int, radius: float=0.0, *, face_out: bool=True) -> Mesh` — `count` copies spaced evenly around the Y axis at `radius`.

## `preview`

Render a `.glb` to PNG with a small software rasteriser — look at what you built.

- `SUN` — constant
- `render(path: str | Path, out: str | Path | None=None, *, size: int=512, yaw: float=45.0, pitch: float=30.0, clip: str | None=None, time: float=0.0, hidden: Sequence[str]=(), frame: tuple[np.ndarray, np.ndarray] | None=None, floor: bool=True, background: tuple[int, int, int]=(238, 240, 244), supersample: int=2) -> Image.Image` — Render one view of a `.glb`; also write it to `out` if given.
- `turntable(path: str | Path, out: str | Path | None=None, *, yaws: Sequence[float]=(45, 135, 225, 315), pitch: float=30.0, size: int=320, clip: str | None=None, time: float=0.0, floor: bool=True, frame: tuple[np.ndarray, np.ndarray] | None=None, hidden: Sequence[str]=()) -> Image.Image` — The model from several corners in one sheet — check every side, not just the front.
- `clip_strip(path: str | Path, out: str | Path | None=None, clip: str='idle', *, frames: int=8, yaw: float=45.0, pitch: float=30.0, size: int=220, floor: bool=True, hidden: Sequence[str]=()) -> Image.Image` — Evenly spaced frames of one clip on a shared frame, so motion reads left to right.
- `stage_strip(path: str | Path, out: str | Path | None=None, *, prefix: str='stage_', yaw: float=45.0, pitch: float=30.0, size: int=240, hidden: Sequence[str]=('scaffold',)) -> Image.Image` — A landmark at each construction stage: stage nodes hidden from the top down (scaffold hidden too).

## `texture`

Seamless procedural textures for game assets, as Pillow images.

- `rgb(color: Color) -> np.ndarray` — A colour as float RGB in [0, 1], from '#rrggbb' or a 0-1 / 0-255 tuple.
- `to_image(arr: np.ndarray) -> Image.Image` — Float array (H, W, 3|4) in [0, 1], or (H, W) grey, to a Pillow image.
- `to_png(img: Image.Image) -> bytes` — Encode an image as PNG bytes.
- `noise(size: int=256, cells: int=8, *, octaves: int=4, seed: int=0, persistence: float=0.5) -> np.ndarray` — Tileable fractal value noise in [0, 1], shape (size, size).
- `colorize(value: np.ndarray, stops: Sequence[tuple[float, Color]]) -> np.ndarray` — Map a [0, 1] field through colour stops [(position, colour), ...] to RGB.
- `tint(base: Color, value: np.ndarray, amount: float=0.25) -> np.ndarray` — A flat colour modulated by a [0, 1] field: (1 - amount .. 1 + amount).
- `solid(color: Color, size: int=64, *, grain: float=0.06, seed: int=0) -> Image.Image` — A flat colour with a faint grain so large faces do not look like plastic.
- `concrete(size: int=256, color: Color='#9a9a96', *, seed: int=0) -> Image.Image` — Mottled concrete or plaster.
- `grass(size: int=256, color: Color='#5d8f3a', *, seed: int=0) -> Image.Image` — Lawn or meadow ground.
- `snow(size: int=256, *, seed: int=0) -> Image.Image` — Snow cover with a cool blue shadow tone.
- `dirt(size: int=256, color: Color='#7a5a3a', *, seed: int=0) -> Image.Image` — Bare earth with pebbles.
- `rock(size: int=256, color: Color='#7c7f84', *, seed: int=0) -> Image.Image` — Granite-like stone.
- `water(size: int=256, color: Color='#3d7fb8', *, seed: int=0) -> Image.Image` — Rippled water.
- `asphalt(size: int=256, *, seed: int=0) -> Image.Image` — Road surface.
- `wood(size: int=256, color: Color='#8a5a32', *, seed: int=0) -> Image.Image` — Wood grain running vertically (along V).
- `bark(size: int=256, color: Color='#5a3f2a', *, seed: int=0) -> Image.Image` — Tree bark: vertical furrows.
- `foliage(size: int=256, color: Color='#2f6b3a', *, seed: int=0) -> Image.Image` — Leaf or needle clusters.
- `metal(size: int=256, color: Color='#9aa3ad', *, panels: int=4, seed: int=0) -> Image.Image` — Brushed metal panels with seams.
- `fabric(size: int=128, color: Color='#3f6fb0', *, seed: int=0) -> Image.Image` — Woven cloth for clothing.
- `skin(size: int=64, color: Color='#e0b48c', *, seed: int=0) -> Image.Image` — Faintly mottled skin tone.
- `stripes(size: int=256, count: int=8, *, a: Color='#f4f1ea', b: Color='#3b3e46', vertical: bool=False, duty: float=0.5) -> Image.Image` — Even stripes — zebra crossings, hazard tape, awnings.
- `brick(size: int=256, *, rows: int=8, cols: int=4, color: Color='#9a4a32', mortar: Color='#c9c2b4', gap: float=0.08, seed: int=0) -> Image.Image` — Running-bond brick, `rows` courses and `cols` bricks per course per repeat.
- `tiles(size: int=256, *, count: int=8, color: Color='#d8d2c4', grout: Color='#8c867a', gap: float=0.06, vary: float=0.08, seed: int=0) -> Image.Image` — Square floor or wall tiles, `count` per side per repeat.
- `planks(size: int=256, *, count: int=6, color: Color='#9a6a3a', seed: int=0) -> Image.Image` — Floor boards or wooden siding: `count` planks across, staggered joints.
- `shingles(size: int=256, *, rows: int=8, cols: int=8, color: Color='#3d4a5a', seed: int=0) -> Image.Image` — Overlapping roof tiles (kawara-style rounded courses); rows run along U.
- `windows(size: int=256, *, cols: int=4, rows: int=4, wall: Color='#c3c8d0', glass: Color='#2c4660', frame: float=0.18, lit: float=0.35, light: Color='#ffd98a', seed: int=0) -> tuple[Image.Image, Image.Image]` — A facade of `cols` x `rows` windows per repeat: (colour, emissive) images.
- `lattice(size: int=256, *, cells: int=4, color: Color='#ec5b2a', bar: float=0.12, background: Color | None=None) -> Image.Image` — Diagonal truss bracing; transparent between bars unless `background` is given.
- `gradient(size: int=256, top: Color='#9cc8ff', bottom: Color='#f7e3c0') -> Image.Image` — A vertical gradient, top colour at V=0 — sky domes and backdrops.
- `atlas(images: Sequence[Image.Image], *, cell: int=128) -> tuple[Image.Image, list[tuple[float, float, float, float]]]` — Pack images into one square atlas: (image, [(u0, v0, u1, v1) per input]).