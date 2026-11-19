"""Minimal glTF 2.0 binary (.glb) writer and reader, stdlib + numpy only.

Knows meshes, nodes, skins and materials, never console formats: callers hand over plain arrays.
Unit and axis conversions are the caller's job (put them on a root node). Spec:
https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

__all__ = ["Gltf", "GltfError", "read_glb"]

GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
REPEAT = 10497
COMPONENT_TYPES = {
    np.dtype(np.uint8): 5121,
    np.dtype(np.uint16): 5123,
    np.dtype(np.uint32): 5125,
    np.dtype(np.float32): 5126,
}
TYPES = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4", 16: "MAT4"}


ALPHA_MODES = ("OPAQUE", "MASK", "BLEND")
ANIMATION_PATHS = {"translation": 3, "rotation": 4, "scale": 3}


class GltfError(ValueError):
    """Inconsistent input to the writer, or a .glb that does not parse."""


@dataclass
class Gltf:
    """Collects a scene and serialises it as one .glb. Nodes without a parent are scene roots."""

    extras: dict[str, Any] = field(default_factory=dict)
    _doc: dict[str, list[Any]] = field(default_factory=dict, init=False)
    _bin: bytearray = field(default_factory=bytearray, init=False)
    _parents: dict[int, int] = field(default_factory=dict, init=False)

    def _add(self, key: str, item: dict[str, Any]) -> int:
        items = self._doc.setdefault(key, [])
        items.append(item)
        return len(items) - 1

    def _accessor(self, data: NDArray[Any], target: int | None, bounds: bool = True) -> int:
        """One accessor + bufferView. data is (count,) for scalars or (count, components)."""
        if data.dtype not in COMPONENT_TYPES:
            raise GltfError(f"unsupported component type {data.dtype}")
        flat = np.ascontiguousarray(data, dtype=data.dtype.newbyteorder("<"))
        width = 1 if flat.ndim == 1 else flat.shape[1]
        if flat.ndim > 2 or width not in TYPES or len(flat) == 0:
            raise GltfError(f"bad accessor shape {data.shape}")
        self._bin += b"\0" * (-len(self._bin) % 4)  # every view starts 4-byte aligned
        view: dict[str, Any] = {"buffer": 0, "byteOffset": len(self._bin)}
        self._bin += flat.tobytes()
        view["byteLength"] = len(self._bin) - view["byteOffset"]
        if target is not None:
            view["target"] = target
        acc: dict[str, Any] = {
            "bufferView": self._add("bufferViews", view),
            "componentType": COMPONENT_TYPES[flat.dtype],
            "count": len(flat),
            "type": TYPES[width],
        }
        if bounds:
            cols = flat.reshape(len(flat), width)
            acc["min"], acc["max"] = cols.min(axis=0).tolist(), cols.max(axis=0).tolist()
        return self._add("accessors", acc)

    def node(
        self,
        name: str,
        *,
        parent: int | None = None,
        translation: tuple[float, ...] | None = None,
        rotation: tuple[float, ...] | None = None,  # quaternion x, y, z, w
        scale: tuple[float, ...] | None = None,
        mesh: int | None = None,
        skin: int | None = None,
        extras: dict[str, Any] | None = None,
    ) -> int:
        item: dict[str, Any] = {"name": name}
        for key, value in (
            ("translation", translation),
            ("rotation", rotation),
            ("scale", scale),
            ("mesh", mesh),
            ("skin", skin),
            ("extras", extras),
        ):
            if value is not None:
                item[key] = list(value) if isinstance(value, tuple) else value
        index = self._add("nodes", item)
        if parent is not None:
            if not 0 <= parent < index:  # parents first: the tree cannot have cycles
                raise GltfError(f"node {name!r}: parent {parent} not added yet")
            self._doc["nodes"][parent].setdefault("children", []).append(index)
            self._parents[index] = parent
        return index

    def material(
        self,
        name: str,
        texture_uri: str | None = None,
        extras: dict[str, Any] | None = None,
        alpha_mode: str = "OPAQUE",
    ) -> int:
        if alpha_mode not in ALPHA_MODES:
            raise GltfError(f"material {name!r}: alpha mode {alpha_mode!r} not in {ALPHA_MODES}")
        pbr: dict[str, Any] = {"metallicFactor": 0.0, "roughnessFactor": 1.0}
        if texture_uri is not None:
            if "samplers" not in self._doc:
                self._add("samplers", {"wrapS": REPEAT, "wrapT": REPEAT})
            image = self._add("images", {"uri": texture_uri})
            pbr["baseColorTexture"] = {"index": self._add("textures", {"source": image})}
            self._doc["textures"][-1]["sampler"] = 0
        item: dict[str, Any] = {"name": name, "pbrMetallicRoughness": pbr}
        if alpha_mode != "OPAQUE":  # OPAQUE is the glTF default; MASK uses the default cutoff 0.5
            item["alphaMode"] = alpha_mode
        if extras:
            item["extras"] = extras
        return self._add("materials", item)

    def mesh(
        self,
        name: str,
        positions: NDArray[np.float32],
        primitives: list[tuple[NDArray[Any], int | None]],  # (triangle-list indices, material)
        *,
        normals: NDArray[np.float32] | None = None,
        texcoords: NDArray[np.float32] | None = None,
        texcoords1: NDArray[np.float32] | None = None,
        joints: NDArray[Any] | None = None,
        weights: NDArray[np.float32] | None = None,
    ) -> int:
        if texcoords1 is not None and texcoords is None:  # glTF needs TEXCOORD_n sets contiguous
            raise GltfError(f"mesh {name!r}: TEXCOORD_1 without TEXCOORD_0")
        count = len(positions)
        attrs = {"POSITION": self._accessor(positions, ARRAY_BUFFER)}
        for key, arr in (
            ("NORMAL", normals),
            ("TEXCOORD_0", texcoords),
            ("TEXCOORD_1", texcoords1),
            ("JOINTS_0", joints),
            ("WEIGHTS_0", weights),
        ):
            if arr is not None:
                if len(arr) != count:
                    raise GltfError(f"{name}: {key} has {len(arr)} entries, POSITION {count}")
                attrs[key] = self._accessor(arr, ARRAY_BUFFER)
        prims = []
        for indices, material in primitives:
            if len(indices) % 3 or (len(indices) and int(indices.max()) >= count):
                raise GltfError(f"{name}: indices are not a triangle list over {count} vertices")
            if len(indices) == 0:
                continue
            prim: dict[str, Any] = {
                "attributes": attrs,
                "indices": self._accessor(indices, ELEMENT_ARRAY_BUFFER),
                "mode": 4,
            }
            if material is not None:
                prim["material"] = material
            prims.append(prim)
        if not prims:
            raise GltfError(f"{name}: no triangles")
        return self._add("meshes", {"name": name, "primitives": prims})

    def skin(
        self, joints: list[int], inverse_bind: NDArray[np.float32], skeleton: int | None = None
    ) -> int:
        """inverse_bind: (len(joints), 16) column-major matrices."""
        if inverse_bind.shape != (len(joints), 16):
            raise GltfError(f"inverse bind matrices shape {inverse_bind.shape}")
        item: dict[str, Any] = {
            "joints": joints,
            "inverseBindMatrices": self._accessor(inverse_bind, None, bounds=False),
        }
        if skeleton is not None:
            item["skeleton"] = skeleton
        return self._add("skins", item)

    def animation(
        self,
        name: str,
        channels: list[tuple[int, str, NDArray[np.float32], NDArray[np.float32]]],
        extras: dict[str, Any] | None = None,
    ) -> int:
        """channels: (node, path, times in seconds, values). LINEAR samplers; equal key times share
        one input accessor."""
        samplers: list[dict[str, Any]] = []
        targets: list[dict[str, Any]] = []
        inputs: dict[bytes, int] = {}
        nodes = len(self._doc.get("nodes", []))
        for node, path, times, values in channels:
            width = ANIMATION_PATHS.get(path)
            if width is None or not 0 <= node < nodes:
                raise GltfError(f"animation {name!r}: bad target {node}/{path}")
            if times.ndim != 1 or values.shape != (len(times), width):
                raise GltfError(f"animation {name!r}: {path} keys shaped {values.shape}")
            if (np.diff(times) <= 0).any() or not np.isfinite(values).all():
                raise GltfError(
                    f"animation {name!r}: key times not increasing or values not finite"
                )
            key = times.astype(np.float32).tobytes()
            if key not in inputs:
                inputs[key] = self._accessor(times.astype(np.float32), None)  # input needs min/max
            output = self._accessor(values.astype(np.float32), None, bounds=False)
            targets.append({"sampler": len(samplers), "target": {"node": node, "path": path}})
            samplers.append({"input": inputs[key], "output": output, "interpolation": "LINEAR"})
        item: dict[str, Any] = {"name": name, "channels": targets, "samplers": samplers}
        if extras:
            item["extras"] = extras
        return self._add("animations", item)

    def glb(self) -> bytes:
        roots = [i for i in range(len(self._doc.get("nodes", []))) if i not in self._parents]
        doc: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": "RRL"},
            "scene": 0,
            "scenes": [{"nodes": roots}],
            **self._doc,
        }
        if self.extras:
            doc["asset"]["extras"] = self.extras
        binary = bytes(self._bin) + b"\0" * (-len(self._bin) % 4)
        if binary:
            doc["buffers"] = [{"byteLength": len(binary)}]
        try:
            text = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode()
        except ValueError as exc:  # NaN/inf is not valid JSON, so not valid glTF
            raise GltfError(f"non-finite value in glTF JSON: {exc}") from exc
        text += b" " * (-len(text) % 4)
        chunks = struct.pack("<II", len(text), JSON_CHUNK) + text
        if binary:
            chunks += struct.pack("<II", len(binary), BIN_CHUNK) + binary
        return GLB_MAGIC + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def read_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    """(JSON document, BIN chunk) of a .glb; checks the header, lengths and 4-byte alignment."""
    if len(data) < 20 or data[:4] != GLB_MAGIC:
        raise GltfError("not a .glb")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2 or length != len(data) or length % 4:
        raise GltfError(f"glb version {version}, length {length} of {len(data)} bytes")
    chunks: list[tuple[int, bytes]] = []
    pos = 12
    while pos < length:
        if pos + 8 > length:
            raise GltfError("truncated chunk header")
        size, kind = struct.unpack_from("<II", data, pos)
        if size % 4 or pos + 8 + size > length:
            raise GltfError(f"chunk at {pos} has bad length {size}")
        chunks.append((kind, data[pos + 8 : pos + 8 + size]))
        pos += 8 + size
    if not chunks or chunks[0][0] != JSON_CHUNK:
        raise GltfError("first chunk is not JSON")
    binary = chunks[1][1] if len(chunks) > 1 and chunks[1][0] == BIN_CHUNK else b""
    return json.loads(chunks[0][1]), binary
