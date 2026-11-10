#!/usr/bin/env python3
"""
Make a mod's models from the games' own discs, as its manifest describes, so that a mod ships no
model taken from a game: custom_assets/<game>/models/models.jsonc.

    python scripts/modding/mod_models.py                 # every game with a manifest
    python scripts/modding/mod_models.py --game jak3     # one game's
    python scripts/modding/mod_models.py --check         # only list what is missing

At a mod's install from the OpenGOAL Launcher, the mod's extractor runs this tool (a one-file
executable, mod_models, that the release builds) before its decompile, whose level extraction
bakes the models into their levels (decompiler/extractor/main.cpp, run_mod_models): --root is then
the mod's data/ folder, --iso the games' extracted discs of the player's install, --extractor that
extractor, which rips the levels the models come from.

The manifest (comments allowed):

  {
    "games": ["jak2", "jak3"],                 // the discs it needs (the extractor checks that
                                               // the player has them)
    "rips": {"jak2/forresca": "FRA.DGO"},      // each rip folder used below, and its DGO
    "models": {
      "<name>": {
        "level": "ctypesa",                    // the level whose .fr3 gets the mesh (optional)
        "mesh": "jak2/forresca/crimson-guard-lod0.glb",    // <game>/<rip folder>/<rip file>
        "prims": [4, 5, 6],                    // the mesh's primitives kept (optional: all)
        "skeleton": "jak3/ctypesa/crimson-guard-lod0.glb", // skeleton and clips from another rip
                                               // (same joint names and bind poses; optional)
        "padding": 2,                          // copies of the first clip before the others
        "clips": ["idle", "walk"],             // the clips in this order (optional: the rip's)
        "clip_prefix": "",                     // the rip's clip names start with it
        "textures_from": "jak3/ctypesa/crimson-guard-lod0.glb", // images of the same name taken
        "texture_prefix": "cguardgame-",       // from this rip (those starting with the prefix)
        "recolor": {"hues": [-15, 15], "rotate": 217, "min_saturation": 0.3}
                                               // a color variant: the texels of a hue in hues
                                               // (degrees, lo below 0 wraps around red) and that
                                               // saturated turned by rotate degrees; "textures":
                                               // [names] limits it to those images (optional)
      }
    },
    "files": {"custom_assets/jak3/music/X.MUS": "jak2/MUS/DANGER4.MUS"}  // copies from a disc
  }

An image a recolor rule changes is renamed <image>-<model>: two variants of one rip baked into
the same level keep their own textures. Each model is written twice, same content: custom_assets/<game>/models/custom_levels/<name>.glb,
which build-actor reads (art group <name>-ag), and custom_assets/<game>/models/<level>/
<name>-lod0.glb, which the level extraction (task extract) bakes into <level>.fr3 for the
renderer. A missing rip is extracted first: the decompiler of out/build/Release/bin on
iso_data/<game> (rip_levels, only the DGOs needed), or the --extractor given. A file is only
rewritten when its content changes. Needs numpy.
"""
from __future__ import annotations

import argparse
import base64
import colorsys
import json
import os
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np

EXE = ".exe" if os.name == "nt" else ""
VERSION = "ntsc_v1"

COMPONENT = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Glb:
    """A parsed .glb: its JSON and binary chunk, with typed accessor reads."""

    def __init__(self, path: Path):
        data = path.read_bytes()
        json_len = struct.unpack_from("<I", data, 12)[0]
        self.json = json.loads(data[20:20 + json_len])
        off = 20 + json_len
        bin_len = struct.unpack_from("<I", data, off)[0]
        self.bin = data[off + 8:off + 8 + bin_len]
        self.path = path

    def accessor(self, index: int) -> np.ndarray:
        acc = self.json["accessors"][index]
        view = self.json["bufferViews"][acc["bufferView"]]
        dtype = COMPONENT[acc["componentType"]]
        width = WIDTH[acc["type"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride", 0)
        item = np.dtype(dtype).itemsize * width
        if stride and stride != item:
            raw = np.frombuffer(self.bin, dtype=np.uint8, count=stride * acc["count"], offset=start)
            raw = raw.reshape(acc["count"], stride)[:, :item].copy()
            return raw.view(dtype).reshape(acc["count"], width)
        return np.frombuffer(self.bin, dtype=dtype, count=acc["count"] * width,
                             offset=start).reshape(acc["count"], width).copy()

    def view_bytes(self, index: int) -> bytes:
        view = self.json["bufferViews"][index]
        start = view.get("byteOffset", 0)
        return self.bin[start:start + view["byteLength"]]

    def joint_names(self) -> list[str]:
        skin = self.json["skins"][0]
        return [self.json["nodes"][i]["name"] for i in skin["joints"]]


class Writer:
    """Collects glTF objects and one binary buffer, then writes a .glb."""

    def __init__(self):
        self.json = {"asset": {"version": "2.0", "generator": "OpenGOAL mod_models.py"},
                     "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [],
                     "materials": [], "textures": [], "images": [], "samplers": [],
                     "accessors": [], "bufferViews": [], "skins": [], "animations": []}
        self.bin = bytearray()

    def view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        view = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.bin.extend(data)
        self.json["bufferViews"].append(view)
        return len(self.json["bufferViews"]) - 1

    def accessor(self, array: np.ndarray, kind: str, component: int, target: int | None = None,
                 minmax: bool = False) -> int:
        array = np.ascontiguousarray(array, dtype=COMPONENT[component])
        acc = {"bufferView": self.view(array.tobytes(), target), "componentType": component,
               "count": int(array.shape[0]), "type": kind}
        if minmax:
            acc["min"] = [float(x) for x in array.reshape(array.shape[0], -1).min(axis=0)]
            acc["max"] = [float(x) for x in array.reshape(array.shape[0], -1).max(axis=0)]
        self.json["accessors"].append(acc)
        return len(self.json["accessors"]) - 1

    def copy_accessor(self, src: Glb, index: int, target: int | None = None) -> int:
        acc = src.json["accessors"][index]
        return self.accessor(src.accessor(index), acc["type"], acc["componentType"], target,
                             "min" in acc)

    def save_bytes(self) -> bytes:
        for key in [k for k, v in self.json.items() if v == []]:
            del self.json[key]
        self.json["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.json, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        self.bin.extend(b"\0" * (-len(self.bin) % 4))
        out = bytearray(struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(self.bin)))
        out += struct.pack("<II", len(js), 0x4E4F534A) + js
        out += struct.pack("<II", len(self.bin), 0x004E4942) + self.bin
        return bytes(out)


def image_bytes(src: Glb, image: dict) -> tuple[bytes, str]:
    """Pixels of an image of `src`, embedded (bufferView) or as a data URI."""
    if "bufferView" in image:
        return src.view_bytes(image["bufferView"]), image.get("mimeType", "image/png")
    uri = image["uri"]
    head, payload = uri.split(",", 1)
    return base64.b64decode(payload), head[5:].split(";")[0]


def png_decode(data: bytes) -> tuple[int, int, bytearray]:
    """(width, height, pixels) of an 8 bit RGB or RGBA PNG (the rips' textures): pixels is a
    bytearray of RGBA, row after row."""
    width, height, depth, color = struct.unpack(">IIBB", data[16:26])
    channels = {2: 3, 6: 4}.get(color)
    if depth != 8 or not channels:
        raise ValueError(f"PNG of bit depth {depth}, color type {color}: only 8 bit RGB(A)")
    pos, idat = 8, b""
    while pos < len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + size]
        pos += 12 + size
    raw = zlib.decompress(idat)
    stride = width * channels
    prev = bytearray(stride)
    out = bytearray()
    for y in range(height):
        kind = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if kind == 1:
                row[i] = (row[i] + a) & 255
            elif kind == 2:
                row[i] = (row[i] + b) & 255
            elif kind == 3:
                row[i] = (row[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = row
        if channels == 3:
            for x in range(width):
                out += row[3 * x:3 * x + 3] + b"\xff"
        else:
            out += row
    return width, height, out


def png_encode(width: int, height: int, pixels: bytearray) -> bytes:
    """An RGBA PNG (no filter) of pixels (RGBA, row after row)."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(
            ">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    stride = width * 4
    raw = b"".join(b"\0" + bytes(pixels[y * stride:(y + 1) * stride]) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def recolor_pixels(pixels: bytearray, hues: list[float], rotate: float, min_saturation: float) -> int:
    """Turn by rotate degrees the hue of the texels whose hue is in hues ([lo, hi] degrees, lo may
    be negative: a window around red) and whose saturation is at least min_saturation, in place
    (RGBA pixels). Returns the number of texels changed. The mod Haven City: New Dawn's rule
    (scripts/level_port/common/glb.py there)."""
    lo, hi = hues
    changed = 0
    for i in range(0, len(pixels), 4):
        h, s, v = colorsys.rgb_to_hsv(pixels[i] / 255, pixels[i + 1] / 255, pixels[i + 2] / 255)
        deg = h * 360.0
        if s >= min_saturation and (lo <= deg <= hi or lo <= deg - 360.0 <= hi):
            r, g, b = colorsys.hsv_to_rgb(((deg + rotate) % 360.0) / 360.0, s, v)
            pixels[i:i + 3] = bytes(int(round(c * 255)) for c in (r, g, b))
            changed += 1
    return changed


def copy_skeleton(out: Writer, src: Glb) -> tuple[int, dict[int, int]]:
    """Copy every node of `src` (meshes detached), its skin and its scene roots.
    Returns the index of the node that carried the skinned mesh and the node index map."""
    node_map = {i: i for i in range(len(src.json["nodes"]))}
    mesh_node = None
    for i, node in enumerate(src.json["nodes"]):
        node = dict(node)
        if "mesh" in node:
            mesh_node = i
            node.pop("mesh")
            node.pop("skin", None)
        out.json["nodes"].append(node)
    skin = dict(src.json["skins"][0])
    skin["inverseBindMatrices"] = out.copy_accessor(src, skin["inverseBindMatrices"])
    out.json["skins"].append(skin)
    out.json["scenes"][0]["nodes"] = list(src.json["scenes"][src.json.get("scene", 0)]["nodes"])
    if mesh_node is None:
        sys.exit(f"{src.path}: no skinned mesh node")
    return mesh_node, node_map


def copy_mesh(out: Writer, src: Glb, mesh_node: int, keep: list[int] | None,
              joint_remap: np.ndarray | None, textures_from: dict[str, tuple[bytes, str]] | None,
              recolor: dict | None = None, model: str = ""):
    """Copy the skinned mesh of `src` onto node `mesh_node` of `out`.

    keep          primitive indices to keep (None: all)
    joint_remap   JOINTS_0 value map from `src`'s skin to `out`'s (None: unchanged)
    textures_from image name -> pixels replacing `src`'s image of that name
    recolor      the model's recolor rule (None: none); an image it changes is renamed
                 <image>-<model>"""
    mesh = src.json["meshes"][0]
    prims = mesh["primitives"] if keep is None else [mesh["primitives"][i] for i in keep]
    attr_map: dict[tuple[str, int], int] = {}
    image_map: dict[int, int] = {}
    texture_map: dict[int, int] = {}
    material_map: dict[int, int] = {}
    sampler_map: dict[int, int] = {}

    def image(index: int) -> int:
        if index not in image_map:
            img = src.json["images"][index]
            data, mime = image_bytes(src, img)
            if textures_from and img.get("name") in textures_from:
                data, mime = textures_from[img["name"]]
            name = img.get("name", f"image{index}")
            if (recolor and mime == "image/png"
                    and ("textures" not in recolor or name in recolor["textures"])):
                width, height, pixels = png_decode(data)
                if recolor_pixels(pixels, recolor["hues"], recolor["rotate"],
                                  recolor.get("min_saturation", 0.0)):
                    data, name = png_encode(width, height, pixels), f"{name}-{model}"
            out.json["images"].append({"name": name, "mimeType": mime,
                                       "bufferView": out.view(data)})
            image_map[index] = len(out.json["images"]) - 1
        return image_map[index]

    def texture(index: int) -> int:
        if index not in texture_map:
            tex = dict(src.json["textures"][index])
            if "source" in tex:
                tex["source"] = image(tex["source"])
            if "sampler" in tex:
                s = tex["sampler"]
                if s not in sampler_map:
                    out.json["samplers"].append(dict(src.json["samplers"][s]))
                    sampler_map[s] = len(out.json["samplers"]) - 1
                tex["sampler"] = sampler_map[s]
            out.json["textures"].append(tex)
            texture_map[index] = len(out.json["textures"]) - 1
        return texture_map[index]

    def remap_textures(obj):
        # in a material, every dict holding "index" is a texture reference
        if isinstance(obj, dict):
            return {k: texture(v) if k == "index" and isinstance(v, int) else remap_textures(v)
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [remap_textures(x) for x in obj]
        return obj

    def material(index: int) -> int:
        if index not in material_map:
            out.json["materials"].append(remap_textures(src.json["materials"][index]))
            material_map[index] = len(out.json["materials"]) - 1
        return material_map[index]

    # The rips' vertex attributes cover every merc model of the level (the Jak 2 drillmtn hover
    # guard uses 2447 of 107332 vertices; the others are bound to other skeletons): keep the
    # vertices this mesh's indices reach, renumbered in order.
    if len({json.dumps(p["attributes"], sort_keys=True) for p in prims}) != 1:
        sys.exit(f"{src.path}: primitives do not share their vertex attributes")
    used = np.unique(np.concatenate([src.accessor(p["indices"]).ravel() for p in prims]))
    renumber = np.full(int(used.max()) + 1, -1, dtype=np.int64)
    renumber[used] = np.arange(len(used))

    new_prims = []
    for prim in prims:
        attrs = {}
        for name, acc in prim["attributes"].items():
            key = (name, acc)
            if key not in attr_map:
                info = src.json["accessors"][acc]
                data = src.accessor(acc)[used]
                if name == "JOINTS_0" and joint_remap is not None:
                    data = joint_remap[data.astype(np.int64)]
                if name == "JOINTS_0" and data.max() >= len(out.json["skins"][0]["joints"]):
                    sys.exit(f"{src.path}: a kept vertex is bound to a joint outside the skin")
                attr_map[key] = out.accessor(data, info["type"], info["componentType"], 34962,
                                             name == "POSITION")
            attrs[name] = attr_map[key]
        indices = renumber[src.accessor(prim["indices"]).ravel()]
        new = {"attributes": attrs,
               "indices": out.accessor(indices.reshape(-1, 1), "SCALAR", 5125, 34963),
               "mode": prim.get("mode", 4)}
        if "material" in prim:
            new["material"] = material(prim["material"])
        new_prims.append(new)
    out.json["meshes"].append({"name": mesh.get("name", "mesh"), "primitives": new_prims})
    node = out.json["nodes"][mesh_node]
    node["mesh"] = len(out.json["meshes"]) - 1
    node["skin"] = 0


def copy_animations(out: Writer, src: Glb, node_map: dict[int, int], padding: int,
                    names: list[str] | None = None, prefix: str = ""):
    """Copy every clip of `src`, after `padding` copies of its first clip; with `names`, in that
    order (prefix + name), failing when one is missing."""
    anims = src.json.get("animations", [])
    if names:
        by_name = {a.get("name"): a for a in anims}
        missing = [n for n in names if prefix + n not in by_name]
        if missing or len(names) != len(anims):
            sys.exit(f"{src.path}: clips {missing} missing, or clips left out of the list")
        anims = [by_name[prefix + n] for n in names]
    order = [anims[0]] * padding + anims
    acc_map: dict[int, int] = {}
    for i, anim in enumerate(order):
        samplers = []
        for s in anim["samplers"]:
            for key in ("input", "output"):
                if s[key] not in acc_map:
                    acc_map[s[key]] = out.copy_accessor(src, s[key])
            samplers.append({"input": acc_map[s["input"]], "output": acc_map[s["output"]],
                             "interpolation": s.get("interpolation", "LINEAR")})
        channels = [{"sampler": c["sampler"],
                     "target": {"node": node_map[c["target"]["node"]], "path": c["target"]["path"]}}
                    for c in anim["channels"]]
        name = anim.get("name", f"clip{i}") if i >= padding else f"padding-{i}"
        out.json["animations"].append({"name": name, "samplers": samplers, "channels": channels})


def read_jsonc(path: Path) -> dict:
    """A JSON file with // comments (outside strings)."""
    text = path.read_text("utf8")
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*', lambda m: m.group(1) or "", text)
    return json.loads(text)


class Context:
    """Where the tool reads and writes: the root (the repository, or a mod release's data/), the
    games' discs, and the extractor to rip with."""

    def __init__(self, root: Path, isos: dict, extractor):
        self.root = root
        self.isos = isos
        self.extractor = extractor

    def iso(self, game: str) -> Path:
        return Path(self.isos[game]) if game in self.isos else self.root / "iso_data" / game

    def rip_path(self, ref: str) -> Path:
        game, folder, file = ref.split("/", 2)
        return self.root / "decompiler_out" / game / "levels" / folder / file

    def extract(self, game: str, dgos: list):
        """Rip these DGOs of game: the extractor on its disc (at an install), else the decompiler."""
        overrides = json.dumps({"decompile_code": False, "levels_extract": True, "allowed_objects": [],
                                "rip_levels": True, "levels_to_extract": dgos})
        if self.extractor:
            cmd = [self.extractor, str(self.iso(game)), "--folder", "--decompile", "--game", game,
                   "--proj-path", str(self.root), "--decomp-config-override", overrides]
        else:
            decompiler = self.root / "out" / "build" / "Release" / "bin" / f"decompiler{EXE}"
            if not decompiler.exists():
                sys.exit(f"no {decompiler}: build it with task build-release-decomp")
            cmd = [str(decompiler), f"./decompiler/config/{game}/{game}_config.jsonc", "./iso_data",
                   "./decompiler_out", "--version", VERSION, "--config-override", overrides]
        print("  running: " + " ".join(cmd), flush=True)
        # OG_MOD_MODELS: the extractor skips its own run of this tool in this extraction
        subprocess.run(cmd, check=True, cwd=self.root, env={**os.environ, "OG_MOD_MODELS": "1"})


def needed_rips(manifest: dict) -> set:
    refs = set()
    for entry in manifest.get("models", {}).values():
        for key in ("mesh", "skeleton", "textures_from"):
            if key in entry:
                refs.add(entry[key])
    return refs


def make_rips(ctx: Context, manifest: dict):
    """Extract the rips that are missing, a game at a time, only their DGOs."""
    missing = sorted(r for r in needed_rips(manifest) if not ctx.rip_path(r).exists())
    by_game = {}
    for ref in missing:
        game, folder, _ = ref.split("/", 2)
        dgo = manifest.get("rips", {}).get(f"{game}/{folder}")
        if not dgo:
            sys.exit(f"{ref}: its folder {game}/{folder} has no DGO in the manifest's rips")
        by_game.setdefault(game, set()).add(dgo)
    for game, dgos in sorted(by_game.items()):
        print(f"  {game}: ripping {', '.join(sorted(dgos))}", flush=True)
        ctx.extract(game, sorted(dgos))
    still = [r for r in missing if not ctx.rip_path(r).exists()]
    if still:
        sys.exit("the extraction did not write: " + ", ".join(still))


def build_model(ctx: Context, name: str, entry: dict) -> "Writer":
    """One model of the manifest (see the docstring at the top)."""
    cache = {}

    def rip(ref: str) -> Glb:
        if ref not in cache:
            cache[ref] = Glb(ctx.rip_path(ref))
        return cache[ref]

    mesh = rip(entry["mesh"])
    textures = None
    if "textures_from" in entry:
        src = rip(entry["textures_from"])
        prefix = entry.get("texture_prefix", "")
        textures = {img.get("name"): image_bytes(src, img) for img in src.json["images"]
                    if img.get("name", "").startswith(prefix)}
    out = Writer()
    anim_src, remap = mesh, None
    if "skeleton" in entry:
        anim_src = rip(entry["skeleton"])
        names_m, names_s = mesh.joint_names(), anim_src.joint_names()
        missing = [n for n in names_m if n not in names_s]
        if missing:
            sys.exit(f"{name}: joints of the mesh missing from the skeleton: {missing}")
        remap = np.array([names_s.index(n) for n in names_m], dtype=np.int64)
        ibm_m = mesh.accessor(mesh.json["skins"][0]["inverseBindMatrices"])
        ibm_s = anim_src.accessor(anim_src.json["skins"][0]["inverseBindMatrices"])
        worst = float(np.abs(ibm_m - ibm_s[remap]).max())
        if worst > 1e-3:
            sys.exit(f"{name}: the bind poses of the mesh and the skeleton differ (max {worst})")
    mesh_node, node_map = copy_skeleton(out, anim_src)
    copy_mesh(out, mesh, mesh_node, entry.get("prims"), remap, textures, entry.get("recolor"), name)
    copy_animations(out, anim_src, node_map, entry.get("padding", 0), entry.get("clips"),
                    entry.get("clip_prefix", ""))
    out.json["meshes"][0]["name"] = f"{name}-lod0"
    return out


def write_if_changed(path: Path, data: bytes) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def model_paths(game: str, name: str, entry: dict) -> list:
    """Where a model is written: for build-actor, and for its level's extraction."""
    base = Path("custom_assets") / game / "models"
    paths = [base / "custom_levels" / f"{name}.glb"]
    if entry.get("level"):
        paths.append(base / entry["level"] / f"{name}-lod0.glb")
    return paths


def run_game(ctx: Context, game: str, check: bool) -> int:
    manifest = read_jsonc(ctx.root / "custom_assets" / game / "models" / "models.jsonc")
    if check:
        wanted = [p for n, e in manifest.get("models", {}).items() for p in model_paths(game, n, e)]
        wanted += [Path(p) for p in manifest.get("files", {})]
        bad = [str(p) for p in wanted if not (ctx.root / p).exists()]
        print(f"  {'missing: ' + ', '.join(bad) if bad else 'everything is there'}")
        return 1 if bad else 0
    make_rips(ctx, manifest)
    for name, entry in manifest.get("models", {}).items():
        model = build_model(ctx, name, entry)
        data = model.save_bytes()
        changed = False
        for path in model_paths(game, name, entry):
            changed |= write_if_changed(ctx.root / path, data)
        print(f"  {name}: {len(model.json['meshes'][0]['primitives'])} primitives, "
              f"{len(model.json.get('animations', []))} clips" + ("" if changed else " (unchanged)"))
    for dest, ref in manifest.get("files", {}).items():
        src_game, rel = ref.split("/", 1)
        src = ctx.iso(src_game) / rel
        if not src.exists():
            sys.exit(f"{dest}: no {src} (the disc of {src_game})")
        changed = write_if_changed(ctx.root / dest, src.read_bytes())
        print(f"  {dest}: from {ref}" + ("" if changed else " (unchanged)"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--game", help="one game's manifest (default: every game's)")
    parser.add_argument("--check", action="store_true", help="only list the missing outputs")
    parser.add_argument("--root", help="the repository root (default), or a mod release's data/")
    parser.add_argument("--iso", action="append", default=[], metavar="GAME=PATH",
                        help="a game's extracted disc (default: iso_data/<game>), repeatable")
    parser.add_argument("--extractor", help="rip with this OpenGOAL extractor (a mod release's) "
                                            "instead of the decompiler of out/build/Release/bin")
    args = parser.parse_args()
    # the one-file executable has no repository around it: --root is then required
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    isos = {}
    for item in args.iso:
        game, sep, path = item.partition("=")
        if not sep:
            parser.error(f"--iso {item}: expected GAME=PATH")
        isos[game] = str(Path(path).resolve())
    ctx = Context(root, isos, str(Path(args.extractor).resolve()) if args.extractor else None)
    games = [args.game] if args.game else sorted(
        p.parent.parent.name for p in (root / "custom_assets").glob("*/models/models.jsonc"))
    if not games:
        print("no custom_assets/<game>/models/models.jsonc")
        return 0
    code = 0
    for game in games:
        print(f"{game}:", flush=True)
        code |= run_game(ctx, game, args.check)
    return code


if __name__ == "__main__":
    sys.exit(main())
