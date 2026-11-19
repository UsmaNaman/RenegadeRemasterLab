"""Halo 2 (original Xbox, retail) level geometry (`sbsp`): clusters, instanced geometry, glTF.

Layout facts and sources: documentation/platforms/halo2_xbox.md ("Level geometry"). Section raws are
decoded by render_model.decode_section; the BSP area is reached the way ltmp.py reaches it.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, BinaryIO

import numpy as np
from numpy.typing import NDArray

from rrl.adapters.xbox_halo2.bitmaps import RawLocation
from rrl.adapters.xbox_halo2.cachefile import CacheError, StringIds, TagEntry, _read_at, block_at
from rrl.adapters.xbox_halo2.ltmp import NONE, BspEntry, LightmapGroup, lightmap_groups
from rrl.adapters.xbox_halo2.render_model import (
    HALO_TO_GLTF_ROTATION,
    RESOURCE_SIZE,
    WORLD_UNIT_METRES,
    RenderModel,
    Resource,
    Section,
    SectionMesh,
    ShaderInfo,
    add_materials,
)
from rrl.processors.gltf import Gltf

SBSP_SIZE = 0x148  # enough of the sbsp head for the blocks below
CLUSTERS, CLUSTER_SIZE = 0x9C, 0xB0
MATERIALS, MATERIAL_SIZE = 0xA4, 0x20
MATERIAL_SHAD_DATUM = 0x0C  # datum half of the shad tagref at +8
DEFINITIONS, DEFINITION_SIZE = 0x138, 0xC8
INSTANCES, INSTANCE_SIZE = 0x140, 0x58
RAW_HEADER_SIZE = 0x44
TOLERANCE = 1e-3
# Lighting flags (section +0x26) bit -> the vertex stream it adds
LIGHTING_STREAMS = {0: 3, 1: 4}


@dataclass(frozen=True)
class Instance:
    name: str
    definition: int
    scale: float
    rotation: tuple[float, ...]  # 3x3 row-major; columns = forward, left, up (local +X/+Y/+Z)
    position: tuple[float, float, float]
    lightmapping_policy: int  # 0 = own lightmap page, 1 = per vertex


@dataclass(frozen=True)
class StructureBsp:
    name: str  # sbsp tag name
    clusters: RenderModel  # stub models: decode_section reads sections and materials from them
    definitions: RenderModel
    instances: tuple[Instance, ...]
    materials: tuple[int, ...]  # shad datum per material


def read_structure_bsp(f: BinaryIO, bsp: BspEntry, name: str, sid: StringIds) -> StructureBsp:
    """Parse one BSP's sbsp. Raises CacheError on anything malformed."""
    tr = bsp.tr
    sbsp = _read_at(f, tr(bsp.addresses(f)[0], SBSP_SIZE), SBSP_SIZE, f"{name} sbsp")

    def blocks(parent: bytes, pos: int, element: int, what: str) -> list[bytes]:
        data = block_at(f, parent, pos, element, tr, f"{name} {what}")
        return [data[i : i + element] for i in range(0, len(data), element)]

    def sections(pos: int, element: int, what: str) -> tuple[Section, ...]:
        out = []
        for i, el in enumerate(blocks(sbsp, pos, element, what)):
            vc, tc, pc = struct.unpack_from("<3H", el)
            (lighting,) = struct.unpack_from("<H", el, 0x26)
            ptr, size, header_size, data_size = struct.unpack_from("<4I", el, 0x28)
            resources = tuple(
                Resource(*struct.unpack_from("<Bxxxhh2I", r))
                for r in blocks(el, 0x38, RESOURCE_SIZE, f"{what} {i} resources")
            )
            keys = {(r.kind, r.locator, r.sub) for r in resources}
            for bit, stream in LIGHTING_STREAMS.items():
                if vc and bool(lighting >> bit & 1) != ((2, 56, stream) in keys):
                    raise CacheError(
                        f"{name} {what} {i}: lighting flags {lighting:#x} disagree with vertex "
                        f"stream {stream}"
                    )
            raw = RawLocation(ptr >> 30, ptr & 0x3FFFFFFF, size)
            out.append(Section(0, vc, tc, pc, -1, raw, header_size, data_size, resources))
        return tuple(out)

    materials = tuple(
        struct.unpack_from("<I", m, MATERIAL_SHAD_DATUM)[0]
        for m in blocks(sbsp, MATERIALS, MATERIAL_SIZE, "materials")
    )
    instances = []
    for el in blocks(sbsp, INSTANCES, INSTANCE_SIZE, "instances"):
        scale, *frame = struct.unpack_from("<13f", el)
        (definition,) = struct.unpack_from("<h", el, 0x34)
        (name_sid,) = struct.unpack_from("<I", el, 0x50)
        (policy,) = struct.unpack_from("<h", el, 0x56)
        try:
            label = sid(name_sid)
        except CacheError:  # the instance still places; only its name is lost
            label = ""
        rows = np.array(frame[:9]).reshape(3, 3).T  # stored F, L, U become the columns
        instances.append(
            Instance(
                label, definition, scale, tuple(rows.ravel().tolist()), tuple(frame[9:]), policy
            )
        )

    def stub(what: str, secs: tuple[Section, ...]) -> RenderModel:
        return RenderModel(f"{name} {what}", (0.0, 1.0) * 7, (), secs, (), (), tuple(materials))

    return StructureBsp(
        name=name,
        clusters=stub("cluster", sections(CLUSTERS, CLUSTER_SIZE, "cluster")),
        definitions=stub(
            "instance def", sections(DEFINITIONS, DEFINITION_SIZE, "instance definition")
        ),
        instances=tuple(instances),
        materials=materials,
    )


def quat_from_matrix(m: NDArray[np.float64]) -> tuple[float, float, float, float]:
    """Unit quaternion (x, y, z, w) of a proper rotation matrix (column-vector convention)."""
    t = float(np.trace(m))
    # Shepperd: divide by the largest of w, x, y, z so the result stays accurate near 180 degrees.
    if t > 0:
        s = 2 * np.sqrt(1 + t)
        q = ((m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, s / 4)
    else:
        i = int(np.argmax(np.diag(m)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2 * np.sqrt(1 + m[i, i] - m[j, j] - m[k, k])
        v = [0.0, 0.0, 0.0]
        v[i], v[j], v[k] = s / 4, (m[j, i] + m[i, j]) / s, (m[k, i] + m[i, k]) / s
        q = (v[0], v[1], v[2], (m[k, j] - m[j, k]) / s)
    n = float(np.linalg.norm(q))
    return (float(q[0]) / n, float(q[1]) / n, float(q[2]) / n, float(q[3]) / n)


def instance_problem(inst: Instance, definitions: RenderModel) -> str | None:
    """Why an instance cannot be placed, or None."""
    if not 0 <= inst.definition < len(definitions.sections):
        return f"definition {inst.definition} not in {len(definitions.sections)}"
    if definitions.sections[inst.definition].vertex_count == 0:
        return f"definition {inst.definition} is empty"
    r = np.array(inst.rotation).reshape(3, 3)
    if not (np.isfinite(r).all() and np.isfinite(inst.position).all() and inst.scale > 0):
        return "non-finite transform or scale <= 0"
    if not np.allclose(r.T @ r, np.eye(3), atol=TOLERANCE) or np.linalg.det(r) <= 0:
        return "frame is not a rotation"
    return None


def lightmap_group(f: BinaryIO, bsp: BspEntry) -> LightmapGroup | None:
    """The BSP's first lightmap group that names a bitm, or None. Raises CacheError."""
    if bsp.ltmp == NONE:
        return None
    return next((g for g in lightmap_groups(f, bsp) if g.bitm != NONE), None)


def to_glb(
    level: StructureBsp,
    clusters: dict[int, SectionMesh],
    definitions: dict[int, SectionMesh],
    shaders: Iterable[ShaderInfo | None],
    texture_uri: Callable[[TagEntry], str],
    lightmap: LightmapGroup | None,
    extras: dict[str, Any],
    lightmap_uri: Callable[[int], str] | None = None,
) -> tuple[bytes, list[str]]:
    """One .glb: a mesh per cluster, a mesh per used definition shared by its instance nodes.

    `clusters` / `definitions` hold the decoded non-empty sections. Returns the file and warnings.
    """
    warnings: list[str] = []
    g = Gltf(extras=extras)
    root = g.node("halo_to_gltf", rotation=HALO_TO_GLTF_ROTATION, scale=(WORLD_UNIT_METRES,) * 3)
    materials = add_materials(g, shaders, texture_uri)

    def lightmap_extras(infos: list[tuple[int, int]], i: int) -> dict[str, Any]:
        """Page links in node extras: core glTF has no lightmap slot and pages are per node."""
        if i >= len(infos) or infos[i][0] < 0:
            return {}
        out: dict[str, Any] = {"lightmap_bitmap": infos[i][0], "lightmap_palette": infos[i][1]}
        if lightmap_uri is not None:
            out["lightmap_uri"] = lightmap_uri(infos[i][0])
        return out

    def mesh(name: str, sm: SectionMesh) -> int | None:
        if not any(len(t) for _, t in sm.parts):
            warnings.append(f"{name}: no triangles after unstripping")
            return None
        return g.mesh(
            name,
            sm.positions,
            [(t, materials[mat]) for mat, t in sm.parts],
            normals=sm.normals,
            texcoords=sm.texcoords,
            texcoords1=sm.lightmap_uv,
        )

    for i, sm in clusters.items():
        if (m := mesh(f"cluster_{i}", sm)) is not None:
            info = lightmap_extras(lightmap.clusters, i) if lightmap else {}
            g.node(f"cluster_{i}", parent=root, mesh=m, extras=info or None)
    meshes: dict[int, int | None] = {}
    for j, inst in enumerate(level.instances):
        if problem := instance_problem(inst, level.definitions):
            warnings.append(f"instance {j} ({inst.name}): {problem}; not placed")
            continue
        d = inst.definition
        if d not in meshes:
            meshes[d] = mesh(f"instance_def_{d}", definitions[d])
        if meshes[d] is None:
            continue
        g.node(
            inst.name or f"instance_{j}",
            parent=root,
            mesh=meshes[d],
            translation=inst.position,
            rotation=quat_from_matrix(np.array(inst.rotation).reshape(3, 3)),
            scale=(inst.scale,) * 3,
            extras={
                "instance": j,
                "def": d,
                "lightmapping_policy": inst.lightmapping_policy,
                **(lightmap_extras(lightmap.instances, j) if lightmap else {}),
            },
        )
    return g.glb(), warnings
