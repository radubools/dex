# Game environment — ground, plants, props and the world around the map

Scenery for Many Builders (Tokyo rebuilt in the Canadian wilderness, June to
Christmas, snow arriving over the season), as real 3D models on the
`game-mesh` core. The camera orbits and zooms freely, so everything must look
right from every side, and the backdrop must surround the map in 360 degrees.

```python
from utils import terrain, flora, props, backdrop, catalog
```

Read `utils/API.md` for every signature. game-mesh's conventions apply
unchanged: Y up, 1 unit = 1 tile, origin at the footprint centre at y = 0,
unique node names, **3000 triangles per environment model**, previews looked at.

## Families and packages

| module     | builds                                                         | package names                 |
|------------|----------------------------------------------------------------|-------------------------------|
| `terrain`  | 1x1 tiles (`grass water dirt trail forest`), N x M chunks      | `terrain-<type>`, chunk name  |
| `flora`    | spruce, spruce-tall, spruce-small, fir, pine, birch, maple, bush, grass-tuft, cattails | plant name |
| `props`    | rock, boulder(-large), rock-cluster, log, stump, lumber-pile, crate, barrel, fence(-post), street-lamp, traffic-cone, bench, sign-post, snowman, christmas-tree | prop name |
| `backdrop` | sky dome + ring layers: ground, forest, treeline, hills, clouds, mountains | `backdrop-<layer>` |

Every family has `build_<thing>(name, out_dir)` and `build_all(out_root)`;
each writes `<out>/<name>/` with the GLBs, `previews/` (iso render, turntable,
a strip per clip) and `manifest.json` (`kind="environment"`, `category`,
`footprint` or `tileable: true`, `season` list, `seasons` = season → file,
`variants` = key → file), and returns `catalog.validate_package` — `ok` must
be true. Then look at the PNGs. The validated package is the deliverable:
games pull it from dex (game-mesh, "How a package reaches a game").

## Seasons

Variant files share node and material names: `<name>.glb` (summer) and
`<name>-autumn.glb` / `<name>-winter.glb`. The game picks
`manifest.seasons[season]`; autumn falls back to the summer file when an asset
has no autumn look. Suggested mapping onto `config.js` SNOW: summer Jun–Aug,
autumn Sep until snow starts (`startDaysLeft` 80), winter once
`game.snowLevel() >= 0.5`. `snowman` and `christmas-tree` are winter-only.

- **Terrain**: seasons have identical geometry and UVs (`snow_blend: true`);
  only textures change, so the game may crossfade summer→winter textures by
  `snowLevel()` instead of swapping. Winter water is ice and has no `bob` clip.
- **Models**: winter adds snow (`terrain.snowify` slabs on up-facing faces in a
  `snow` node for props; snow caps per tier for conifers; bare branches for
  deciduous trees).
- **Sky**: variants are times of day — `day`, `dusk`, `night`, `overcast`
  (`seasons.winter` = overcast). Manifest `fog` gives each one's horizon colour.

## Tiling rules (terrain)

- Map x → +X, map y → +Z; a tile is 1x1 split into `SUBDIV` (4) quads a side.
- **Every tile-edge vertex is at y = 0.** Relief (`RELIEF`) moves only interior
  vertices, so any tiles, any types, meet exactly.
- Lakes, inside a chunk: a vertex touching only water sinks with distance to
  shore (`WATER_DEPTH`, `SHORE`), computed from the whole map grid, so chunk
  seams match; banks stay at 0. Surface `water` node at `WATER_LEVEL` (-0.06),
  `bob` clip ±0.012. A lone water tile floods to `TILE_WATER_LEVEL` (+0.02) so
  lone tiles join without a ridge.
- UVs are map coordinates / `UV_TILES` (4): continuous across tiles and chunks.
  A lone tile takes its map position (`tile(type, at=(x, z))`) for phase.
- Ground normals are smooth from the heightfield including neighbours outside
  the chunk, so lighting has no seams either.
- Chunks: `terrain.chunk(grid, season, origin=(x, z), size=(8, 8))` → one GLB,
  merged per material (`ground`, `water`, optional `skirt` earth sides on map
  edges — leave it off when the backdrop ground is used). 8 x 8 ≈ 2100 tris.
  `terrain.world_grid(seed)` ports world.js map generation (tiles + trees →
  forest floor); `terrain.build_map(out_root, seed)` packages the whole map.

## Adding things

- **A tile type**: add it to `TILE_TYPES`, `RELIEF`, `_GROUND` (colour),
  `SNOW_COVER`, and a branch in `ground_texture`; map the game code in
  `GAME_TILES` if world.js has one. Run the seam tests.
- **A plant**: a builder `fn(season) -> gltf.Scene` from `conifer`, `pine`,
  `deciduous`, `bush`, `grass_tuft` or pieces (`blob`, `tier`); keep `trunk` +
  `crown` (pivot at trunk top) with a `sway` clip; register in `flora.PLANTS`
  with its own-file seasons.
- **A prop**: build meshes, return `props._finish(name, season, parts)` (winter
  snow for free; emissive materials in `GLOWING` stay bare), add a `light`
  node with `extras.light` for anything that glows; register in `props.PROPS`
  with seasons and footprint.
- **A backdrop layer**: a function `fn(season, map_w=...) -> Scene` whose meshes
  go into `sector_<k>` nodes (`_add_sectors`, `sector_of`), faces pointing
  inward, radius as a multiple of `HALF` in `RADII`; register in
  `backdrop.LAYERS`. Preview with `hidden=backdrop.near_sectors` (the
  package builder does) so the ring is seen from inside.

## The backdrop (360° around the map)

All layers are centred on the **map centre**: place each GLB at map
(MAP_W/2, MAP_H/2). Radii, in multiples of `HALF` = MAP_W/2 = 26 tiles: ground
apron map edge → 8.0; forest band 2–14 tiles outside the map edge (within
2.0); treeline alpha cards 2.15 and 2.55 (parallax); hills 3.0–4.5 (6–15
high); clouds 3.5–6.5 at 1.2–1.75 up with a 1200 s `drift` clip; mountains
5.8–7.7 (18–42 high, snow caps); sky dome 9.5. Camera far plane ≥ 260; fog in
the sky's horizon colour. The sky material is black with an emissive
gradient (`extras.unlit`): render it unlit and unaffected by time-of-day
emissive scaling. Each ring is 8 `sector_<k>` nodes for culling.

## Composing a test scene

`terrain.compose([(scene, (x, y, z), yaw), ...])` merges pieces into one scene
(clips dropped) — for dressed previews, never for shipping. Render terrain
with `preview.render(..., floor=False)`; `terrain.turntable_sheet` is the
turntable with a `floor` switch and per-yaw hidden nodes.
`backdrop.panorama(season, time)` composes every layer.
