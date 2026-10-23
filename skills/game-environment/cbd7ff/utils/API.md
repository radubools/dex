## `backdrop`

The world around the map, for a free-orbiting camera: sky dome, ground apron, forest, treelines, hills, mountains, clouds.

- `MAP_W` — constant
- `HALF` — constant
- `SECTORS` — constant
- `RADII` — constant
- `HAZE` — constant
- `SKIES` — constant
- `SKY_BELOW` — constant
- `sector_of(angle: float) -> int` — The sector index of an azimuth in radians (0 = +X, increasing toward +Z).
- `near_sectors(yaw: float, *, cutoff: float=0.25) -> list[str]` — Names of the sectors between a `preview` camera at `yaw` and the centre (hide them to look inside).
- `apron_texture(season: str) -> Image.Image` — Ground beyond the map: the map's grass darkened toward forest floor (snow in winter).
- `sky_texture(time: str='day') -> tuple[Image.Image, Image.Image]` — The dome's (colour, emissive) images — the same image: elevation gradient top to bottom, stars at night.
- `treeline_texture(season: str, *, seed: int=0, haze: float=0.3, width: int=512, height: int=128) -> Image.Image` — RGBA strip of spruce silhouettes (tiles horizontally), hazed toward the horizon; winter adds snow.
- `hills_texture(season: str, *, haze: float=0.2) -> Image.Image` — Forested hillside seen from afar: dark canopy speckle (snow-dusted in winter).
- `mountain_texture(season: str, *, haze: float=0.38) -> Image.Image` — Altitude bands for the far ranges (V = 0 at the top peak): forest, rock, snow; tiles along U.
- `sky(time: str='day', *, map_w: float=MAP_W, segments: int=32, rings: int=10) -> gltf.Scene` — The sky dome (inward, unlit) for a time of day: day, dusk, night or overcast.
- `ground(season: str='summer', *, map_w: float=MAP_W, segments: int=64) -> gltf.Scene` — The ground apron: forest floor from the map's square edge out under every other ring.
- `forest(season: str='summer', *, map_w: float=MAP_W, count: int=290, seed: int=5) -> gltf.Scene` — A band of real low-poly spruces 2..14 tiles outside the map edge (and inside 2 x HALF).
- `treeline(season: str='summer', *, map_w: float=MAP_W, cards: int=48) -> gltf.Scene` — Two bands of inward-facing alpha cards with spruce silhouettes, at 2.15 and 2.55 x HALF.
- `hills(season: str='summer', *, map_w: float=MAP_W, segments: int=96, seed: int=11) -> gltf.Scene` — Low-poly forested hills ringing the map at 3 .. 4.5 x HALF.
- `mountains(season: str='summer', *, map_w: float=MAP_W, segments: int=128, seed: int=21) -> gltf.Scene` — Distant mountain ranges at 5.8 .. 7.7 x HALF, 18-42 high, snow-capped (much lower snow in winter).
- `clouds(season: str='summer', *, map_w: float=MAP_W, count: int=22, seed: int=31, period: float=1200.0) -> gltf.Scene` — Puffy low-poly clouds at 3.5 .. 6.5 x HALF, 1.2 .. 1.75 x HALF up; `drift` turns them once per `period` s.
- `layer(name: str, season: str='summer', **kw) -> gltf.Scene` — The scene of one ring layer (`LAYERS`) for a season; `backdrop-sky` takes a time of day instead.
- `panorama(season: str='summer', time: str='day', *, layers: Sequence[str] | None=None, map_w: float=MAP_W) -> gltf.Scene` — Every backdrop layer (and the sky) composed into one scene — for previews, not for shipping.
- `build_layer(name: str, out_dir: str | Path, *, size: int=384, map_w: float=MAP_W) -> dict` — Package one backdrop layer with summer/autumn/winter files (sky: day/dusk/night/overcast).
- `build_all(out_root: str | Path) -> dict[str, dict]` — Package the sky and every ring layer; returns {name: validate report}.

## `flora`

Canadian-wilderness vegetation: spruces, pines, firs, birch, maple, bushes, grass, cattails.

- `GREENS` — constant
- `birch_bark(size: int=128) -> Image.Image` — White birch bark with dark horizontal lenticels.
- `blob(radius: float, material: str, *, seed: int=0, squash: float=1.0, segments: int=7, rings: int=5, rough: float=0.18) -> mesh.Mesh` — A lumpy low-poly ball — a leaf cluster, a bush lobe, a cloud puff, a rock.
- `tier(radius: float, height: float, material: str, *, segments: int=8, seed: int=0, droop: float=0.18) -> mesh.Mesh` — One conifer tier: a cone with a hollowed underside and a ragged rim, base at y=0.
- `conifer(name: str, season: str='summer', *, height: float=2.1, tiers: int=5, radius: float=0.5, trunk: float=0.3, color: str=GREENS['spruce'], seed: int=0, segments: int=8) -> gltf.Scene` — A spruce / fir: stacked tiers on a short trunk; winter puts a snow cap on every tier.
- `pine(name: str, season: str='summer', *, height: float=2.5, seed: int=0, color: str=GREENS['pine']) -> gltf.Scene` — A jack / lodgepole pine: tall bare trunk, a ragged crown of needle clumps near the top.
- `deciduous(name: str, season: str='summer', *, species: str='maple', height: float=1.8, seed: int=0) -> gltf.Scene` — Birch (slim white trunk, small airy crown) or maple (broad dome); winter is bare branches.
- `bush(name: str, season: str='summer', *, seed: int=0, size: float=1.0) -> gltf.Scene` — A low shrub of leafy lobes (blueberry red in autumn); winter piles snow on top.
- `grass_tuft(name: str, season: str='summer', *, seed: int=0, blades: int=11) -> gltf.Scene` — A clump of grass blades (double-sided); autumn turns gold, winter is straw poking out of snow.
- `cattails(name: str, season: str='summer', *, seed: int=0) -> gltf.Scene` — Lake-shore reeds with brown seed heads; dry and pale in autumn/winter.
- `plant(name: str, season: str='summer') -> gltf.Scene` — The scene of a catalogue plant in a season (falls back to summer when it has no own variant).
- `build_plant(name: str, out_dir: str | Path, *, size: int=320) -> dict` — Package one catalogue plant with its seasonal variants; returns `catalog.validate_package`.
- `build_all(out_root: str | Path) -> dict[str, dict]` — Package every plant in `PLANTS`; returns {name: validate report}.

## `props`

Scenery props: rocks, logs, lumber, crates, barrels, fences, lamps, cones, benches, signs, winter decor.

- `log_end(size: int=128) -> Image.Image` — Cut end of a log: pale growth rings and a dark bark rim.
- `sign_face(text: str='TOKYO', size: tuple[int, int]=(256, 64)) -> Image.Image` — A painted wooden sign board with blocky lettering and an arrow.
- `GLOWING` — constant
- `rock(radius: float=0.3, *, seed: int=0, squash: float=0.62) -> mesh.Mesh` — A lumpy boulder of `radius`, flat-bottomed and slightly sunk into the ground.
- `log(length: float=0.9, radius: float=0.12, *, seed: int=0) -> list[mesh.Mesh]` — A felled log lying along X: bark sides and ringed cut ends.
- `boulder(name: str, season: str, radius: float=0.42, seed: int=0) -> gltf.Scene` — One boulder.
- `rock_cluster(name: str, season: str, seed: int=0) -> gltf.Scene` — Three rocks of different sizes huddled on one tile.
- `felled_log(name: str, season: str) -> gltf.Scene` — A fallen log.
- `stump(name: str, season: str) -> gltf.Scene` — A cut stump with roots.
- `lumber_pile(name: str, season: str) -> gltf.Scene` — Fresh-sawn boards stacked in three courses on sticks — building material for the sites.
- `crate(name: str, season: str, size: float=0.5) -> gltf.Scene` — A wooden shipping crate with a darker frame.
- `barrel(name: str, season: str) -> gltf.Scene` — A wooden barrel with iron hoops and a lid.
- `fence(name: str, season: str) -> gltf.Scene` — A split-rail fence segment along X (-0.5..0.5), post at x=-0.5; chains seamlessly.
- `fence_post(name: str, season: str) -> gltf.Scene` — The lone post that closes the open end of a fence run.
- `street_lamp(name: str, season: str) -> gltf.Scene` — A Tokyo-style park lamp: green iron pole, curved arm, glowing lantern (emissive + `light` node).
- `traffic_cone(name: str, season: str) -> gltf.Scene` — An orange traffic cone with a reflective band (construction-site dressing).
- `bench(name: str, season: str) -> gltf.Scene` — A park bench along X (1 tile), facing +Z: iron side frames, wooden seat and back slats.
- `sign_post(name: str, season: str, text: str='TOKYO') -> gltf.Scene` — A wooden trail sign on a post, its board pointing +X, lettered on the +Z face.
- `snowman(name: str, season: str='winter') -> gltf.Scene` — A snowman facing +Z: three snowballs, coal eyes and buttons, carrot nose, stick arms, scarf, top hat.
- `christmas_tree(name: str, season: str='winter') -> gltf.Scene` — The town Christmas tree (2x2): snowy spruce, two blinking light strings, glowing star, presents.
- `prop(name: str, season: str='summer') -> gltf.Scene` — The scene of a catalogue prop in a season (autumn uses the summer model).
- `build_prop(name: str, out_dir: str | Path, *, size: int=320) -> dict` — Package one catalogue prop with its seasonal variants; returns `catalog.validate_package`.
- `build_all(out_root: str | Path) -> dict[str, dict]` — Package every prop in `PROPS`; returns {name: validate report}.

## `terrain`

Ground for Many Builders: 1x1 terrain tiles, N x M chunks, seasons, and the package helpers.

- `MAP_W` — constant
- `MAP_H` — constant
- `TILE_TYPES` — constant
- `GAME_TILES` — constant
- `SEASONS` — constant
- `SUBDIV` — constant
- `RELIEF` — constant
- `WATER_LEVEL` — constant
- `TILE_WATER_LEVEL` — constant
- `WATER_DEPTH` — constant
- `SHORE` — constant
- `UV_TILES` — constant
- `CHUNK` — constant
- `SKIRT_DEPTH` — constant
- `SNOW_COVER` — constant
- `snow_cover(base: np.ndarray, coverage: float, *, seed: int=0) -> np.ndarray` — Lay patchy snow over an RGB array so about `coverage` of it is white.
- `ground_texture(kind: str, season: str='summer', size: int=256) -> Image.Image` — The seamless ground texture of a tile type (or 'lakebed' / 'earth') for a season.
- `declare_materials(scene: gltf.Scene, season: str, kinds: Sequence[str]=TILE_TYPES + ('lakebed', 'earth')) -> None` — Declare the ground materials (named after the tile type) on a scene for a season.
- `snow_texture(size: int=128) -> Image.Image` — The shared snow texture used on caps and drifts of models.
- `snow_material(scene: gltf.Scene, name: str='snow') -> None` — Declare the shared `snow` material on a scene.
- `snowify(meshes: Sequence[mesh.Mesh], *, depth: float=0.04, threshold: float=0.6, material: str='snow', skip: Sequence[str]=()) -> list[mesh.Mesh]` — Snow slabs on the up-facing triangles of meshes: tops lifted by `depth`, open edges walled.
- `jitter(m: mesh.Mesh, amount: float, *, seed: int=0, keep_floor: bool=True) -> mesh.Mesh` — Displace vertices randomly (coincident vertices move together, so no cracks open).
- `sink(m: mesh.Mesh, depth: float=0.0) -> mesh.Mesh` — Rest a mesh on the ground: lift its lowest point to y=0, lower it by `depth`, flatten what is below.
- `normalize_grid(grid: Any) -> list[list[str]]` — A tile grid as rows (map y / world +Z) of tile-type names; accepts names or world.js codes.
- `tiles_from_world(tiles: Sequence[int], width: int=MAP_W, height: int=MAP_H, trees: Sequence[Mapping[str, float]]=()) -> list[list[str]]` — Convert world.js's flat `tiles` array (+ `trees`, which make forest floor) into a name grid.
- `world_grid(seed: int, width: int=MAP_W, height: int=MAP_H) -> tuple[list[list[str]], list[dict]]` — Port of world.js `createWorld` tiles and trees for a seed: (name grid, trees [{x, y, s, v}]).
- `heightfield(grid: Sequence[Sequence[str]], origin: tuple[int, int]=(0, 0), size: tuple[int, int] | None=None, *, seed: int=0) -> np.ndarray` — Vertex heights (rows = z, cols = x) of a chunk of a name grid at SUBDIV per tile.
- `ground_normals(grid: Any, origin: tuple[int, int], size: tuple[int, int], *, seed: int=0) -> np.ndarray` — Smooth per-vertex normals of a chunk's heightfield, using neighbours outside it so chunks shade seamlessly.
- `chunk(grid: Any, season: str='summer', *, origin: tuple[int, int]=(0, 0), size: tuple[int, int] | None=None, seed: int=0, skirt: bool=False, name: str='chunk', water_level: float=WATER_LEVEL, smooth: bool=True) -> gltf.Scene` — One GLB scene of a size[0] x size[1] tile area of a map grid, merged per material.
- `tile(tile_type: str, season: str='summer', *, seed: int=0, at: tuple[int, int]=(0, 0)) -> gltf.Scene` — A lone 1x1 tile of one type at map position `at` (sets UV phase and relief); lone water floods its rim.
- `compose(parts: Sequence[tuple[gltf.Scene, Sequence[float]] | tuple[gltf.Scene, Sequence[float], float]], name: str='scene') -> gltf.Scene` — Merge several scenes into one, each root moved by (x, y, z) and turned by an optional yaw.
- `turntable_sheet(path: str | Path, out: str | Path, *, yaws: Sequence[float]=(45, 135, 225, 315), pitch: float=30.0, size: int=320, floor: bool=True, hidden: Callable[[float], Sequence[str]] | None=None, background: tuple[int, int, int]=(238, 240, 244)) -> Image.Image` — `preview.turntable` with a `floor` switch and per-yaw hidden nodes (for backdrop rings).
- `write_package(name: str, out_dir: str | Path, variants: Mapping[str, gltf.Scene], *, category: str, seasons: Mapping[str, str] | None=None, footprint: Sequence[int] | None=None, tileable: bool=False, floor: bool=True, pitch: float=30.0, size: int=384, hidden: Callable[[float], Sequence[str]] | None=None, clip_frames: int=6, title: str='', **fields: Any) -> dict` — Write an environment package: one GLB per variant, previews, manifest; return `validate_package`.
- `build_tile(tile_type: str, out_dir: str | Path, *, name: str | None=None, seed: int=0, size: int=320) -> dict` — Package `terrain-<type>`: a 1x1 tile in summer, autumn and winter (identical geometry).
- `sample_grid() -> list[list[str]]` — An 8x8 grid that shows every tile type and the seams between them (lake, trail, plot, forest).
- `build_chunk(name: str, out_dir: str | Path, grid: Any | None=None, *, origin: tuple[int, int]=(0, 0), size: tuple[int, int] | None=None, seed: int=0, skirt: bool=False, preview_size: int=384) -> dict` — Package a terrain chunk (default: `sample_grid()`) in all three seasons.
- `build_map(out_root: str | Path, seed: int, *, chunk_size: int=CHUNK, skirt: bool=False) -> dict[str, dict]` — Package the game map for a seed as chunks `terrain-map-<cx>-<cz>` (ragged last row/column).
- `build_all(out_root: str | Path) -> dict[str, dict]` — Every tile type package plus the sample chunk; returns {name: validate report}.