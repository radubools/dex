"""Write and read binary glTF 2.0 (`.glb`): node hierarchies, PBR materials, textures, animations.

GLB is what three.js, Babylon, Godot and Unity all load natively, so it is the
one format every asset in this project ships as. A file holds the meshes, the
textures (embedded PNG), the node tree and every animation clip — one file per
asset, nothing to keep in step beside it.

Animation is **node animation**: each moving part is its own node (a forearm,
a turret barrel, a construction stage) and clips key its translation, rotation
or scale. No skinning, so nothing deforms — which suits low-poly blocky
characters and is what every engine plays without a rig importer.

    from utils import gltf, mesh, texture
    scene = gltf.Scene("crate")
    scene.material("wood", texture=texture.planks(), roughness=0.8)
    root = scene.node("crate", meshes=[mesh.box(1, 1, 1, "wood")])
    clip = scene.animation("spin")
    clip.rotate(root, [0, 1, 2], [mesh.quat_euler(y=a) for a in (0, 180, 360)])
    scene.write("crate.glb")
    print(gltf.validate("crate.glb"))
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

from . import texture as _tex
from .mesh import Mesh, quat_matrix

FLOAT, UINT16, UINT32 = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942


@dataclass
class Node:
    """One glTF node: a transform, optional meshes, children."""

    name: str
    index: int
    meshes: list[Mesh] = field(default_factory=list)
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    children: list["Node"] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


@dataclass
class Material:
    """A metallic-roughness PBR material, optionally textured and emissive."""

    name: str
    base_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    texture: Image.Image | None = None
    roughness: float = 0.85
    metallic: float = 0.0
    emissive: tuple[float, float, float] = (0.0, 0.0, 0.0)
    emissive_texture: Image.Image | None = None
    alpha: str = "OPAQUE"  # OPAQUE | MASK | BLEND
    double_sided: bool = False
    extras: dict = field(default_factory=dict)


class Clip:
    """One animation clip: channels keyed against node translation, rotation or scale."""

    def __init__(self, name: str, loop: bool = True):
        self.name = name
        self.loop = loop
        self.channels: list[tuple[Node, str, np.ndarray, np.ndarray, str]] = []

    def key(self, node: Node, path: str, times: Sequence[float], values: Sequence[Sequence[float]],
            interpolation: str = "LINEAR") -> "Clip":
        """Key `path` ('translation' | 'rotation' | 'scale') of `node` at `times` seconds."""
        if path not in ("translation", "rotation", "scale"):
            raise ValueError(f"unknown path {path!r}")
        t = np.asarray(times, dtype=np.float32)
        v = np.asarray(values, dtype=np.float32)
        width = 4 if path == "rotation" else 3
        if v.shape != (len(t), width):
            raise ValueError(f"{path} needs {len(t)} values of {width}, got {v.shape}")
        if np.any(np.diff(t) <= 0):
            raise ValueError("key times must strictly increase")
        if path == "rotation":
            v = v / np.linalg.norm(v, axis=1, keepdims=True)
            # Keep consecutive quaternions in one hemisphere so slerp takes the short way.
            for i in range(1, len(v)):
                if np.dot(v[i - 1], v[i]) < 0:
                    v[i] = -v[i]
        self.channels.append((node, path, t, v, interpolation))
        return self

    def translate(self, node: Node, times, values, interpolation: str = "LINEAR") -> "Clip":
        """Key node translation."""
        return self.key(node, "translation", times, values, interpolation)

    def rotate(self, node: Node, times, values, interpolation: str = "LINEAR") -> "Clip":
        """Key node rotation as (x, y, z, w) quaternions."""
        return self.key(node, "rotation", times, values, interpolation)

    def scale(self, node: Node, times, values, interpolation: str = "LINEAR") -> "Clip":
        """Key node scale."""
        return self.key(node, "scale", times, values, interpolation)

    @property
    def duration(self) -> float:
        """Length of the clip in seconds."""
        return max((float(t[-1]) for _, _, t, _, _ in self.channels), default=0.0)


class Scene:
    """Accumulates nodes, materials and clips, then writes one `.glb`."""

    def __init__(self, name: str = "asset", extras: dict | None = None):
        self.name = name
        self.extras = dict(extras or {})
        self.nodes: list[Node] = []
        self.roots: list[Node] = []
        self.materials: dict[str, Material] = {}
        self.clips: list[Clip] = []

    def material(self, name: str, *, color: _tex.Color = "#ffffff", texture: Image.Image | None = None,
                 roughness: float = 0.85, metallic: float = 0.0, emissive: _tex.Color = (0, 0, 0),
                 emissive_texture: Image.Image | None = None, opacity: float = 1.0,
                 alpha: str | None = None, double_sided: bool = False, **extras: Any) -> Material:
        """Define (or replace) a material referenced by meshes' `material` names."""
        c = _tex.rgb(color)
        if alpha is None:
            alpha = "BLEND" if opacity < 1 else ("MASK" if texture is not None and texture.mode == "RGBA" else "OPAQUE")
        m = Material(name, (float(c[0]), float(c[1]), float(c[2]), float(opacity)), texture, roughness, metallic,
                     tuple(float(x) for x in _tex.rgb(emissive)), emissive_texture, alpha, double_sided, extras)
        self.materials[name] = m
        return m

    def node(self, name: str, *, meshes: Sequence[Mesh] = (), parent: Node | None = None,
             translation: Sequence[float] = (0, 0, 0), rotation: Sequence[float] = (0, 0, 0, 1),
             scale: Sequence[float] = (1, 1, 1), **extras: Any) -> Node:
        """Add a node under `parent` (or as a scene root). Meshes are grouped per material."""
        if any(n.name == name for n in self.nodes):
            raise ValueError(f"node name {name!r} is already used; names are how the game finds parts")
        from .mesh import group
        n = Node(name, len(self.nodes), group(meshes) if meshes else [], tuple(map(float, translation)),
                 tuple(map(float, rotation)), tuple(map(float, scale)), extras=extras)
        self.nodes.append(n)
        (parent.children if parent else self.roots).append(n)
        return n

    def find(self, name: str) -> Node:
        """The node called `name`."""
        for n in self.nodes:
            if n.name == name:
                return n
        raise KeyError(name)

    def animation(self, name: str, *, loop: bool = True) -> Clip:
        """Start a new animation clip."""
        if any(c.name == name for c in self.clips):
            raise ValueError(f"clip {name!r} already exists")
        c = Clip(name, loop)
        self.clips.append(c)
        return c

    # ------------------------------------------------------------- writing

    def to_glb(self) -> bytes:
        """Serialise to GLB bytes."""
        w = _Writer()
        doc: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": "dex game-mesh"},
            "scene": 0,
            "scenes": [{"name": self.name, "nodes": [n.index for n in self.roots],
                        **({"extras": self.extras} if self.extras else {})}],
            "nodes": [], "meshes": [], "materials": [], "accessors": [], "bufferViews": [],
        }
        mat_index: dict[str, int] = {}
        images: list[dict] = []
        textures: list[dict] = []
        image_cache: dict[int, int] = {}

        def tex_index(img: Image.Image) -> int:
            key = id(img)
            if key not in image_cache:
                view = w.view(_tex.to_png(img))
                images.append({"bufferView": view, "mimeType": "image/png"})
                textures.append({"source": len(images) - 1, "sampler": 0})
                image_cache[key] = len(textures) - 1
            return image_cache[key]

        used = {m.material for n in self.nodes for m in n.meshes}
        missing = used - set(self.materials)
        for name in sorted(missing):  # an undeclared material gets a visible magenta
            self.material(name, color="#ff00ff")
        for name, m in self.materials.items():
            if name not in used:
                continue
            pbr: dict[str, Any] = {"baseColorFactor": list(m.base_color),
                                   "metallicFactor": m.metallic, "roughnessFactor": m.roughness}
            if m.texture is not None:
                pbr["baseColorTexture"] = {"index": tex_index(m.texture)}
            md: dict[str, Any] = {"name": name, "pbrMetallicRoughness": pbr}
            if any(m.emissive) or m.emissive_texture is not None:
                md["emissiveFactor"] = list(m.emissive) if any(m.emissive) else [1.0, 1.0, 1.0]
            if m.emissive_texture is not None:
                md["emissiveTexture"] = {"index": tex_index(m.emissive_texture)}
            if m.alpha != "OPAQUE":
                md["alphaMode"] = m.alpha
                if m.alpha == "MASK":
                    md["alphaCutoff"] = 0.5
            if m.double_sided:
                md["doubleSided"] = True
            if m.extras:
                md["extras"] = m.extras
            mat_index[name] = len(doc["materials"])
            doc["materials"].append(md)

        for n in self.nodes:
            nd: dict[str, Any] = {"name": n.name}
            if any(n.translation):
                nd["translation"] = list(n.translation)
            if tuple(n.rotation) != (0.0, 0.0, 0.0, 1.0):
                nd["rotation"] = list(n.rotation)
            if tuple(n.scale) != (1.0, 1.0, 1.0):
                nd["scale"] = list(n.scale)
            if n.children:
                nd["children"] = [c.index for c in n.children]
            if n.extras:
                nd["extras"] = n.extras
            if n.meshes:
                prims = []
                for m in n.meshes:
                    pos = m.positions.astype(np.float32)
                    attrs = {
                        "POSITION": w.accessor(doc, pos, "VEC3", ARRAY_BUFFER, bounds=True),
                        "NORMAL": w.accessor(doc, m.normals.astype(np.float32), "VEC3", ARRAY_BUFFER),
                        "TEXCOORD_0": w.accessor(doc, m.uvs.astype(np.float32), "VEC2", ARRAY_BUFFER),
                    }
                    idx = m.indices.reshape(-1)
                    idx = idx.astype(np.uint16) if len(pos) < 65536 else idx.astype(np.uint32)
                    prims.append({"attributes": attrs, "material": mat_index[m.material],
                                  "indices": w.accessor(doc, idx, "SCALAR", ELEMENT_ARRAY_BUFFER)})
                nd["mesh"] = len(doc["meshes"])
                doc["meshes"].append({"name": n.name, "primitives": prims})
            doc["nodes"].append(nd)

        if self.clips:
            doc["animations"] = []
            for c in self.clips:
                samplers, channels = [], []
                for node, path, t, v, interp in c.channels:
                    samplers.append({"input": w.accessor(doc, t, "SCALAR", None, bounds=True),
                                     "output": w.accessor(doc, v, "VEC4" if path == "rotation" else "VEC3", None),
                                     "interpolation": interp})
                    channels.append({"sampler": len(samplers) - 1, "target": {"node": node.index, "path": path}})
                doc["animations"].append({"name": c.name, "samplers": samplers, "channels": channels,
                                          "extras": {"loop": c.loop, "duration": round(c.duration, 4)}})

        if images:
            doc["images"] = images
            doc["textures"] = textures
            doc["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
        doc["bufferViews"] = w.views
        for k in ("meshes", "materials", "accessors", "bufferViews"):
            if not doc[k]:
                del doc[k]
        blob = bytes(w.blob)
        doc["buffers"] = [{"byteLength": len(blob)}]
        return _pack(doc, blob)

    def write(self, path: str | Path) -> Path:
        """Write the `.glb` and return its path."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.to_glb())
        return p


class _Writer:
    def __init__(self):
        self.blob = bytearray()
        self.views: list[dict] = []

    def view(self, data: bytes, target: int | None = None, doc: dict | None = None) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        bv: dict[str, Any] = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.blob += data
        self.views.append(bv)
        return len(self.views) - 1

    def accessor(self, doc: dict, arr: np.ndarray, kind: str, target: int | None, bounds: bool = False) -> int:
        ctype = {np.dtype(np.float32): FLOAT, np.dtype(np.uint16): UINT16, np.dtype(np.uint32): UINT32}[arr.dtype]
        view = self.view(np.ascontiguousarray(arr).tobytes(), target)
        doc["bufferViews"] = self.views
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[kind]
        acc: dict[str, Any] = {"bufferView": view, "componentType": ctype,
                               "count": int(arr.size // width), "type": kind}
        if bounds:
            a = arr.reshape(-1, width)
            acc["min"] = [float(x) for x in a.min(axis=0)]
            acc["max"] = [float(x) for x in a.max(axis=0)]
        doc["accessors"].append(acc)
        return len(doc["accessors"]) - 1


def _pack(doc: dict, blob: bytes) -> bytes:
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    blob = blob + b"\0" * (-len(blob) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (struct.pack("<III", GLB_MAGIC, 2, total) + struct.pack("<II", len(js), JSON_CHUNK) + js
            + struct.pack("<II", len(blob), BIN_CHUNK) + blob)


# ------------------------------------------------------------------ reading


@dataclass
class Gltf:
    """A parsed `.glb`: its JSON document and binary chunk."""

    doc: dict
    blob: bytes

    def accessor(self, index: int) -> np.ndarray:
        """An accessor's data as a numpy array, shaped (count, width)."""
        a = self.doc["accessors"][index]
        bv = self.doc["bufferViews"][a["bufferView"]]
        dtype = {FLOAT: np.float32, UINT16: np.uint16, UINT32: np.uint32, 5121: np.uint8}[a["componentType"]]
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        out = np.frombuffer(self.blob, dtype=dtype, count=a["count"] * width, offset=start)
        return out.reshape(a["count"], width)

    def image(self, texture_index: int) -> Image.Image:
        """The image behind a texture index."""
        import io
        src = self.doc["textures"][texture_index]["source"]
        bv = self.doc["bufferViews"][self.doc["images"][src]["bufferView"]]
        off = bv.get("byteOffset", 0)
        return Image.open(io.BytesIO(self.blob[off:off + bv["byteLength"]])).convert("RGBA")

    def clip_names(self) -> list[str]:
        """Names of the animation clips."""
        return [a.get("name", f"clip{i}") for i, a in enumerate(self.doc.get("animations", []))]

    def node_index(self, name: str) -> int:
        """Index of the node called `name`."""
        for i, n in enumerate(self.doc["nodes"]):
            if n.get("name") == name:
                return i
        raise KeyError(name)

    def pose(self, clip: str | None = None, time: float = 0.0,
             hidden: Sequence[str] = ()) -> list[tuple[int, np.ndarray]]:
        """World matrices [(node index, 4x4)] at `time` seconds into `clip` (rest pose if None).

        Nodes named in `hidden`, and their subtrees, are left out — which is
        how a construction stage that is not yet built is previewed.
        """
        nodes = self.doc["nodes"]
        local = {i: (list(n.get("translation", [0, 0, 0])), list(n.get("rotation", [0, 0, 0, 1])),
                     list(n.get("scale", [1, 1, 1]))) for i, n in enumerate(nodes)}
        if clip is not None:
            anims = {a.get("name"): a for a in self.doc.get("animations", [])}
            if clip not in anims:
                raise KeyError(f"no clip {clip!r}; have {list(anims)}")
            a = anims[clip]
            for ch in a["channels"]:
                s = a["samplers"][ch["sampler"]]
                t = self.accessor(s["input"])[:, 0]
                v = self.accessor(s["output"])
                local[ch["target"]["node"]][("translation", "rotation", "scale").index(ch["target"]["path"])][:] = \
                    _sample(t, v, time, s.get("interpolation", "LINEAR"), ch["target"]["path"] == "rotation")
        out: list[tuple[int, np.ndarray]] = []
        hide = set(hidden)

        def walk(i: int, parent: np.ndarray) -> None:
            if nodes[i].get("name") in hide:
                return
            tr, rot, sc = local[i]
            m = np.eye(4)
            m[:3, :3] = quat_matrix(rot) * np.asarray(sc)[None, :]
            m[:3, 3] = tr
            world = parent @ m
            out.append((i, world))
            for c in nodes[i].get("children", []):
                walk(c, world)

        for r in self.doc["scenes"][self.doc.get("scene", 0)]["nodes"]:
            walk(r, np.eye(4))
        return out

    def triangles(self, clip: str | None = None, time: float = 0.0, hidden: Sequence[str] = ()):
        """World-space triangles: yields (positions (n,3,3), uvs (n,3,2), normals (n,3,3), material index)."""
        for i, world in self.pose(clip, time, hidden):
            node = self.doc["nodes"][i]
            if "mesh" not in node:
                continue
            for prim in self.doc["meshes"][node["mesh"]]["primitives"]:
                p = self.accessor(prim["attributes"]["POSITION"]).astype(np.float64)
                n = self.accessor(prim["attributes"]["NORMAL"]).astype(np.float64)
                uv = self.accessor(prim["attributes"]["TEXCOORD_0"]).astype(np.float64) \
                    if "TEXCOORD_0" in prim["attributes"] else np.zeros((len(p), 2))
                idx = self.accessor(prim["indices"])[:, 0].astype(np.int64).reshape(-1, 3)
                # A node scaled to zero is the glTF way to hide a part: nothing to draw.
                if abs(np.linalg.det(world[:3, :3])) < 1e-12:
                    continue
                pw = (np.c_[p, np.ones(len(p))] @ world.T)[:, :3]
                nw = n @ np.linalg.inv(world[:3, :3])
                nw /= np.maximum(np.linalg.norm(nw, axis=1, keepdims=True), 1e-12)
                yield pw[idx], uv[idx], nw[idx], prim.get("material", -1)


def _sample(t: np.ndarray, v: np.ndarray, time: float, interp: str, quat: bool) -> np.ndarray:
    if time <= t[0]:
        return v[0].copy()
    if time >= t[-1]:
        return v[-1].copy()
    k = int(np.searchsorted(t, time) - 1)
    if interp == "STEP":
        return v[k].copy()
    f = (time - t[k]) / (t[k + 1] - t[k])
    if quat:
        a, b = v[k].astype(np.float64), v[k + 1].astype(np.float64)
        if np.dot(a, b) < 0:
            b = -b
        q = a * (1 - f) + b * f
        return q / np.linalg.norm(q)
    return v[k] * (1 - f) + v[k + 1] * f


def read(path: str | Path) -> Gltf:
    """Parse a `.glb` file."""
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2:
        raise ValueError(f"{path} is not a glTF 2.0 binary")
    if length != len(data):
        raise ValueError(f"{path}: header says {length} bytes, file has {len(data)}")
    jlen, jtype = struct.unpack_from("<II", data, 12)
    if jtype != JSON_CHUNK:
        raise ValueError("first chunk is not JSON")
    doc = json.loads(data[20:20 + jlen])
    blob = b""
    off = 20 + jlen
    if off < len(data):
        blen, btype = struct.unpack_from("<II", data, off)
        if btype != BIN_CHUNK:
            raise ValueError("second chunk is not BIN")
        blob = data[off + 8:off + 8 + blen]
    return Gltf(doc, blob)


def validate(path: str | Path, *, max_triangles: int | None = None) -> dict:
    """Check a `.glb` loads and is sane; return a report with `errors`, counts, bounds and clips.

    Checks what breaks in an engine rather than the whole spec: accessors in
    range, indices inside their vertex arrays, finite unit normals, POSITION
    min/max present and true, animation channels on real nodes with increasing
    times, every material and texture reference resolving. A report with an
    empty `errors` list is what "done" means for a model.
    """
    errors: list[str] = []
    g = read(path)
    d = g.doc
    nodes = d.get("nodes", [])
    tris = 0
    for mi, m in enumerate(d.get("meshes", [])):
        for pi, prim in enumerate(m["primitives"]):
            where = f"mesh {m.get('name', mi)} prim {pi}"
            try:
                p = g.accessor(prim["attributes"]["POSITION"])
                acc = d["accessors"][prim["attributes"]["POSITION"]]
                if "min" not in acc or "max" not in acc:
                    errors.append(f"{where}: POSITION has no min/max")
                elif not (np.allclose(acc["min"], p.min(0), atol=1e-4) and np.allclose(acc["max"], p.max(0), atol=1e-4)):
                    errors.append(f"{where}: POSITION min/max do not match the data")
                n = g.accessor(prim["attributes"]["NORMAL"])
                if not np.all(np.isfinite(n)) or np.any(np.abs(np.linalg.norm(n, axis=1) - 1) > 1e-2):
                    errors.append(f"{where}: normals are not finite unit vectors")
                if len(n) != len(p):
                    errors.append(f"{where}: NORMAL count differs from POSITION")
                idx = g.accessor(prim["indices"])[:, 0]
                if idx.size % 3:
                    errors.append(f"{where}: index count is not a multiple of 3")
                if idx.size and idx.max() >= len(p):
                    errors.append(f"{where}: index {int(idx.max())} out of range ({len(p)} vertices)")
                if not np.all(np.isfinite(p)):
                    errors.append(f"{where}: non-finite positions")
                tris += idx.size // 3
                mat = prim.get("material")
                if mat is None or mat >= len(d.get("materials", [])):
                    errors.append(f"{where}: material reference {mat} does not resolve")
            except (KeyError, ValueError, IndexError) as exc:
                errors.append(f"{where}: unreadable ({exc})")
    for mat in d.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        for key in ("baseColorTexture",):
            if key in pbr and pbr[key]["index"] >= len(d.get("textures", [])):
                errors.append(f"material {mat.get('name')}: {key} does not resolve")
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            if c >= len(nodes):
                errors.append(f"node {n.get('name', i)}: child {c} does not exist")
    names = [n.get("name") for n in nodes]
    dupes = sorted({x for x in names if names.count(x) > 1})
    if dupes:
        errors.append(f"duplicate node names {dupes}")
    clips = {}
    for a in d.get("animations", []):
        dur = 0.0
        for ch in a["channels"]:
            if ch["target"]["node"] >= len(nodes):
                errors.append(f"clip {a.get('name')}: channel targets missing node")
            s = a["samplers"][ch["sampler"]]
            t = g.accessor(s["input"])[:, 0]
            if len(t) > 1 and np.any(np.diff(t) <= 0):
                errors.append(f"clip {a.get('name')}: key times do not increase")
            if len(g.accessor(s["output"])) != len(t):
                errors.append(f"clip {a.get('name')}: output count differs from input")
            dur = max(dur, float(t[-1]))
        clips[a.get("name")] = round(dur, 3)
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for p, _, _, _ in g.triangles():
        if len(p):
            lo, hi = np.minimum(lo, p.reshape(-1, 3).min(0)), np.maximum(hi, p.reshape(-1, 3).max(0))
    if max_triangles is not None and tris > max_triangles:
        errors.append(f"{tris} triangles exceeds the budget of {max_triangles}")
    return {
        "path": str(path),
        "ok": not errors,
        "errors": errors,
        "bytes": Path(path).stat().st_size,
        "nodes": len(nodes),
        "node_names": names,
        "triangles": tris,
        "materials": [m.get("name") for m in d.get("materials", [])],
        "textures": len(d.get("textures", [])),
        "clips": clips,
        "bounds": {"min": [round(float(x), 4) for x in lo], "max": [round(float(x), 4) for x in hi]}
        if np.all(np.isfinite(lo)) else None,
        "extras": d["scenes"][d.get("scene", 0)].get("extras", {}),
    }
