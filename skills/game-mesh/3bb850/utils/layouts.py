"""Lay written GLBs out side by side in one GLB, so layout previews are rendered from the files themselves."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Sequence

from . import gltf, mesh

_LISTS = ("bufferViews", "accessors", "meshes", "materials", "images", "textures", "samplers")


def compose(placements: Sequence[tuple[str | Path, Sequence[float], float]], out: str | Path) -> Path:
    """Write one GLB holding every (glb path, translation, yaw degrees) placement, animations dropped; returns its path.

    Each distinct file is copied in once (buffer, materials, textures, meshes); each placement adds a copy of
    its node tree under a wrapper node `place_NNN` with the placement's translation and turn about +Y.
    """
    doc: dict = {"asset": {"version": "2.0", "generator": "dex layouts.compose"}, "scene": 0,
                 "scenes": [{"name": "layout", "nodes": []}], "nodes": [], **{k: [] for k in _LISTS}}
    blob = bytearray()
    loaded: dict[str, tuple[gltf.Gltf, dict]] = {}
    for i, (path, t, yaw) in enumerate(placements):
        key = str(Path(path).resolve())
        if key not in loaded:
            g = gltf.read(path)
            while len(blob) % 4:
                blob.append(0)
            base = len(blob)
            blob += g.blob
            off = {k: len(doc[k]) for k in _LISTS}
            for bv in g.doc.get("bufferViews", []):
                doc["bufferViews"].append(dict(bv, buffer=0, byteOffset=bv.get("byteOffset", 0) + base))
            for a in g.doc.get("accessors", []):
                doc["accessors"].append(dict(a, bufferView=a["bufferView"] + off["bufferViews"]))
            for im in g.doc.get("images", []):
                doc["images"].append(dict(im, bufferView=im["bufferView"] + off["bufferViews"]))
            for s in g.doc.get("samplers", []):
                doc["samplers"].append(dict(s))
            for tx in g.doc.get("textures", []):
                tx = dict(tx, source=tx["source"] + off["images"])
                if "sampler" in tx:
                    tx["sampler"] += off["samplers"]
                doc["textures"].append(tx)
            for m in g.doc.get("materials", []):
                m = copy.deepcopy(m)
                pbr = m.get("pbrMetallicRoughness", {})
                if "baseColorTexture" in pbr:
                    pbr["baseColorTexture"]["index"] += off["textures"]
                if "emissiveTexture" in m:
                    m["emissiveTexture"]["index"] += off["textures"]
                doc["materials"].append(m)
            for me in g.doc.get("meshes", []):
                me = copy.deepcopy(me)
                for p in me["primitives"]:
                    p["attributes"] = {k: v + off["accessors"] for k, v in p["attributes"].items()}
                    p["indices"] += off["accessors"]
                    if "material" in p:
                        p["material"] += off["materials"]
                doc["meshes"].append(me)
            loaded[key] = (g, off)
        g, off = loaded[key]
        n0 = len(doc["nodes"])
        for n in g.doc["nodes"]:
            n = copy.deepcopy(n)
            if "mesh" in n:
                n["mesh"] += off["meshes"]
            if "children" in n:
                n["children"] = [c + n0 for c in n["children"]]
            doc["nodes"].append(n)
        roots = g.doc["scenes"][g.doc.get("scene", 0)]["nodes"]
        doc["nodes"].append({"name": f"place_{i:03d}", "translation": [float(v) for v in t],
                             "rotation": list(mesh.quat_euler(y=yaw)), "children": [r + n0 for r in roots]})
        doc["scenes"][0]["nodes"].append(len(doc["nodes"]) - 1)
    for k in _LISTS:
        if not doc[k]:
            del doc[k]
    doc["buffers"] = [{"byteLength": len(blob)}]
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gltf._pack(doc, bytes(blob)))
    return p
