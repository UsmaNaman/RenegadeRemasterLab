"""Halo 2 (original Xbox, retail) render models (`mode`): meta, section geometry, glTF export.

Layout facts and sources: documentation/platforms/halo2_xbox.md ("Render models"). Reimplemented
from those documented facts; Assembly and Reclaimer (GPL-3.0) were references, no code is copied.
"""

from __future__ import annotations

import io
import posixpath
import struct
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, BinaryIO

import numpy as np
from numpy.typing import NDArray

from rrl.adapters.xbox_halo2.bitmaps import RawLocation
from rrl.adapters.xbox_halo2.cachefile import (
    CacheError,
    CacheHeader,
    StringIds,
    TagEntry,
    _read_at,
    block_at,
)
from rrl.processors.gltf import Gltf

MODE_SIZE = 0x84
BOUNDS_SIZE = 0x38
REGION_SIZE = 0x10
PERMUTATION_SIZE = 0x10
SECTION_SIZE = 0x5C
NODE_SIZE = 0x60
MARKER_GROUP_SIZE = 0xC
MARKER_SIZE = 0x24
MATERIAL_SIZE = 0x20
RESOURCE_SIZE = 0x10
PART_SIZE = 0x48
DESCRIPTOR_SIZE = 0x20
RAW_HEADER_SIZE = 0x70
SHAD_RUNTIME_SIZE = 0x50
SHAD_POSTPROCESS_SIZE = 0x7C
SHAD_BITMAP_SIZE = 0xC
# Shaders with no runtime diffuse: which postprocess bitmap slot holds the base colour, by template
# name. [M] Read off the bitmap names on the owner's disc (e.g. one_alpha_env: env cube, specular,
# base, ...). Water pools (slot 0 is a bump map), plasma and camera templates stay untextured.
BASE_SLOT_BY_TEMPLATE = {
    "one_alpha_env": 2,
    "one_alpha_env_illum": 2,
    "one_alpha_env_fixed": 2,
    "one_alpha_env_active_camo": 2,
    "two_alpha_env_illum": 2,
    "sky_one_alpha_env": 2,
    "two_alpha_env_multichannel": 1,
    "waves": 1,
    "sky_one_add_two_plus_two": 1,
    "two_alpha_env_detail": 3,
    "two_add_env_illum": 3,
    "two_add_env_illum_3_channel": 3,
    "overlay": 0,
    "decal_simple": 0,
    "one_add_illum": 0,
    "one_add_illum_detail": 0,
    "two_alpha_clouds": 0,
    "two_add_clouds": 0,
    "two_add_detail_masked": 0,
    "two_alpha_detail_masked": 0,
    "sky_two_alpha_clouds": 0,
    "sky_two_add_clouds": 0,
}
NONE = 0xFFFFFFFF
LODS = 6
TOLERANCE = 1e-3

# Resources inside a section's raw blob, keyed (kind, locator, sub-locator)
PARTS = (0, 0, PART_SIZE)
INDICES = (0, 32, 2)
DESCRIPTORS = (0, 56, DESCRIPTOR_SIZE)
NODE_MAP = (0, 100, 1)
LIST_LOCATOR = 16  # any resource here means the index buffer is a triangle list, not strips

# Stream 0 type byte -> (stride, node byte offset, nodes per vertex, weight byte offset or None)
POSITION_TYPES: dict[int, tuple[int, int, int, int | None]] = {
    1: (12, 0, 0, None),  # 3 x f32, uncompressed
    2: (6, 0, 0, None),
    4: (8, 6, 1, None),
    6: (12, 8, 2, 10),
    8: (12, 6, 3, 9),
    10: (16, 8, 4, 12),
}
UV_TYPES = {24: 8, 25: 4}  # 2 x f32, 2 x i16 compressed
NORMAL_TYPE, NORMAL_STRIDE = 27, 12
LIGHTMAP_UV_TYPE, LIGHTMAP_UV_STRIDE = 31, 4  # level geometry stream 3: 2 x i16 / 32767

# Halo is right-handed Z-up in world units; glTF is Y-up in metres. 1 wu = 10 ft (Reclaimer).
HALO_TO_GLTF_ROTATION = (-0.70710678, 0.0, 0.0, 0.70710678)  # -90 degrees about X
WORLD_UNIT_METRES = 3.048


@dataclass(frozen=True)
class Resource:
    kind: int  # 0 tag block, 2 vertex buffer
    locator: int
    sub: int  # element size, or the vertex stream index
    size: int
    offset: int


@dataclass(frozen=True)
class Section:
    cls: int  # 0 world, 1 rigid, 2 rigid boned, 3 skinned
    vertex_count: int
    triangle_count: int
    part_count: int
    rigid_node: int
    raw: RawLocation
    header_size: int
    data_size: int
    resources: tuple[Resource, ...]


@dataclass(frozen=True)
class Permutation:
    name: str
    lods: tuple[int, ...]  # section index per LOD, lowest detail first; -1 = none


@dataclass(frozen=True)
class Region:
    name: str
    permutations: tuple[Permutation, ...]


@dataclass(frozen=True)
class Node:
    name: str
    parent: int  # -1 = root
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]  # x, y, z, w as stored
    inverse_scale: float
    inverse_bind: tuple[float, ...]  # forward, left, up, position: columns of inverse(world)


@dataclass(frozen=True)
class Marker:
    group: str
    region: int
    permutation: int  # 255 = all
    node: int
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: float


@dataclass(frozen=True)
class RenderModel:
    name: str
    bounds: tuple[float, ...]  # (min, max) for x, y, z, u, v, u2, v2
    regions: tuple[Region, ...]
    sections: tuple[Section, ...]
    nodes: tuple[Node, ...]
    markers: tuple[Marker, ...]
    materials: tuple[int, ...]  # shad datum per material


@dataclass
class SectionMesh:
    positions: NDArray[np.float32]
    normals: NDArray[np.float32] | None
    texcoords: NDArray[np.float32] | None
    joints: NDArray[np.uint16] | None  # model node per influence; None = not skinned
    weights: NDArray[np.float32] | None
    parts: list[tuple[int, NDArray[np.uint16]]]  # (material, triangle-list indices)
    warnings: list[str] = field(default_factory=list)
    lightmap_uv: NDArray[np.float32] | None = None  # level geometry only (stream 3)


@dataclass(frozen=True)
class ShaderInfo:
    name: str
    template: str | None
    diffuse: TagEntry | None  # base colour bitm: runtime properties [0], else a template's slot
    bitmaps: tuple[str | None, ...]  # postprocess [0] bitmaps; slot roles depend on the template
    diffuse_source: str | None = None  # "runtime" or "postprocess[<slot>]"


class _Meta:
    """One tag's meta. Blocks must lie inside it, or with `area` anywhere in the meta area."""

    def __init__(
        self, f: BinaryIO, tag: TagEntry, minimum: int, area: tuple[int, int, int] | None = None
    ) -> None:
        self.name = tag.name or f"{tag.group}_{tag.datum:08x}"
        if tag.offset is None or tag.size < minimum:
            raise CacheError(f"{self.name}: {tag.group} meta missing or too small ({tag.size:#x})")
        self.raw = _read_at(f, tag.offset, tag.size, f"{self.name} meta")
        # (file, base address, file offset, size): mode keeps its blocks inside its own meta,
        # shad points into blocks shared with other tags.
        self._f, address, start, size = (
            (io.BytesIO(self.raw), tag.address, 0, tag.size) if area is None else (f, *area)
        )
        self._base, self._start, self._end = address, start, start + size

    def _tr(self, address: int, size: int) -> int:
        offset = address - self._base + self._start
        if not self._start <= offset <= offset + size <= self._end:
            raise CacheError(f"{self.name}: block {address:#x} (+{size:#x}) outside tag meta")
        return offset

    def block(self, parent: bytes, pos: int, element: int, what: str) -> list[bytes]:
        data = block_at(self._f, parent, pos, element, self._tr, f"{self.name} {what}")
        return [data[i : i + element] for i in range(0, len(data), element)]


def tag_by_datum(tags: list[TagEntry], datum: int) -> TagEntry | None:
    """The tag a datum names (tag references are resolved by datum only), or None."""
    i = datum & 0xFFFF
    return tags[i] if datum != NONE and i < len(tags) and tags[i].datum == datum else None


def read_render_model(f: BinaryIO, tag: TagEntry, sid: StringIds) -> RenderModel:
    m = _Meta(f, tag, MODE_SIZE)
    raw = m.raw
    bounds_block = m.block(raw, 0x14, BOUNDS_SIZE, "compression bounds")
    if not bounds_block:
        raise CacheError(f"{m.name}: no compression bounds")
    if not np.isfinite(struct.unpack_from("<14f", bounds_block[0])).all():
        raise CacheError(f"{m.name}: non-finite compression bounds")
    regions = tuple(
        Region(
            sid(struct.unpack_from("<I", r)[0]),
            tuple(
                Permutation(sid(struct.unpack_from("<I", p)[0]), struct.unpack_from("<6h", p, 4))
                for p in m.block(r, 8, PERMUTATION_SIZE, "permutations")
            ),
        )
        for r in m.block(raw, 0x1C, REGION_SIZE, "regions")
    )
    sections = tuple(_section(m, s) for s in m.block(raw, 0x24, SECTION_SIZE, "sections"))
    nodes = tuple(
        Node(
            name=sid(struct.unpack_from("<I", n)[0]),
            parent=struct.unpack_from("<h", n, 4)[0],
            translation=struct.unpack_from("<3f", n, 0xC),
            rotation=struct.unpack_from("<4f", n, 0x18),
            inverse_scale=struct.unpack_from("<f", n, 0x28)[0],
            inverse_bind=struct.unpack_from("<12f", n, 0x2C),
        )
        for n in m.block(raw, 0x48, NODE_SIZE, "nodes")
    )
    markers = tuple(
        Marker(
            group,
            region=mk[0],
            permutation=mk[1],
            node=mk[2],
            translation=struct.unpack_from("<3f", mk, 4),
            rotation=struct.unpack_from("<4f", mk, 0x10),
            scale=struct.unpack_from("<f", mk, 0x20)[0],
        )
        for g in m.block(raw, 0x58, MARKER_GROUP_SIZE, "marker groups")
        for group in [sid(struct.unpack_from("<I", g)[0])]
        for mk in m.block(g, 4, MARKER_SIZE, "markers")
    )
    materials = tuple(
        struct.unpack_from("<I", mt, 0x0C)[0]
        for mt in m.block(raw, 0x60, MATERIAL_SIZE, "materials")
    )
    model = RenderModel(
        name=sid(struct.unpack_from("<I", raw)[0]),
        bounds=struct.unpack_from("<14f", bounds_block[0]),
        regions=regions,
        sections=sections,
        nodes=nodes,
        markers=markers,
        materials=materials,
    )
    _validate(model, m.name)
    return model


def _section(m: _Meta, s: bytes) -> Section:
    cls, vc, tc, pc = struct.unpack_from("<hxxHHH", s)
    ptr, size, header_size, data_size = struct.unpack_from("<4I", s, 0x38)
    resources = tuple(
        Resource(*struct.unpack_from("<Bxxxhh2I", r))
        for r in m.block(s, 0x48, RESOURCE_SIZE, "section resources")
    )
    return Section(
        cls=cls,
        vertex_count=vc,
        triangle_count=tc,
        part_count=pc,
        rigid_node=struct.unpack_from("<h", s, 0x2C)[0],
        raw=RawLocation(ptr >> 30, ptr & 0x3FFFFFFF, size),
        header_size=header_size,
        data_size=data_size,
        resources=resources,
    )


def _validate(model: RenderModel, name: str) -> None:
    n = len(model.nodes)
    for region in model.regions:
        for p in region.permutations:
            if any(not -1 <= s < len(model.sections) for s in p.lods):
                raise CacheError(f"{name}: {region.name}:{p.name} names a missing section")
    for i, node in enumerate(model.nodes):
        if not -1 <= node.parent < n or node.parent == i:
            raise CacheError(f"{name}: node {i} has bad parent {node.parent}")
        if abs(float(np.linalg.norm(node.rotation)) - 1) > TOLERANCE:
            raise CacheError(f"{name}: node {node.name} quaternion is not unit length")
        if abs(node.inverse_scale - 1) > TOLERANCE:
            raise CacheError(f"{name}: node {node.name} inverse scale {node.inverse_scale}")


def _quat_matrix(q: tuple[float, ...]) -> NDArray[np.float64]:
    x, y, z, w = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def node_order(model: RenderModel) -> list[int]:
    """Node indices with every parent before its children. Raises CacheError on a cycle."""
    order: list[int] = []
    done = [False] * len(model.nodes)
    for start in range(len(model.nodes)):
        chain: list[int] = []
        i = start
        while i >= 0 and not done[i]:
            if len(chain) > len(model.nodes):
                raise CacheError(f"{model.name}: node parents form a cycle")
            chain.append(i)
            i = model.nodes[i].parent
        for j in reversed(chain):
            if not done[j]:
                done[j] = True
                order.append(j)
    return order


def inverse_bind_matrices(model: RenderModel) -> NDArray[np.float32]:
    """Stored inverse bind matrices, (nodes, 16) column-major, checked against the hierarchy."""
    world: dict[int, NDArray[np.float64]] = {}
    for i in node_order(model):
        node = model.nodes[i]
        local = np.eye(4)
        local[:3, :3] = _quat_matrix(node.rotation)
        local[:3, 3] = node.translation
        world[i] = local if node.parent < 0 else world[node.parent] @ local
    out = np.zeros((len(model.nodes), 16), np.float32)
    for i, node in enumerate(model.nodes):
        stored = np.eye(4)
        stored[:3, :4] = np.array(node.inverse_bind).reshape(4, 3).T
        computed = np.linalg.inv(world[i])
        scale = max(1.0, float(np.abs(computed[:3, 3]).max()))
        if float(np.abs(stored - computed).max()) > TOLERANCE * scale:
            raise CacheError(f"{model.name}: node {node.name} inverse bind != inverse(world)")
        out[i] = stored.T.reshape(16)  # column-major
    return out


def decode_section(
    model: RenderModel,
    index: int,
    raw: bytes,
    *,
    header_size: int = RAW_HEADER_SIZE,
    level: bool = False,
) -> SectionMesh:
    """Geometry of one section from its raw blob (read via bitmaps.read_raw).

    `level`: a structure BSP cluster or instanced geometry (sbsp, header 0x44). Those are f32 only,
    a list iff the index count is 3 x triangles, and may carry lightmap UVs in stream 3.
    """
    sec = model.sections[index]
    what = f"{model.name} section {index}"
    vc, size = sec.vertex_count, len(raw)
    if raw[:4] != b"hklb" or raw[-4:] != b"fklb":
        raise CacheError(f"{what}: raw lacks hklb/fklb framing")
    if sec.header_size != header_size or size != sec.header_size + sec.data_size + 12:
        raise CacheError(f"{what}: raw is {size:#x} bytes, header and data say otherwise")
    if struct.unpack_from("<I", raw, 4)[0] != sec.data_size:
        raise CacheError(f"{what}: raw resource size disagrees with the section")
    base = size - sec.data_size - 4
    res: dict[tuple[int, int, int], bytes] = {}
    for r in sec.resources:
        start = base + r.offset
        if not base <= start <= start + r.size <= size - 4:
            raise CacheError(f"{what}: resource {r.locator}/{r.sub} outside the raw data")
        res[r.kind, r.locator, r.sub] = raw[start : start + r.size]

    def need(key: tuple[int, int, int]) -> bytes:
        if key not in res:
            raise CacheError(f"{what}: no resource {key}")
        return res[key]

    descriptors = need(DESCRIPTORS)

    def stream(i: int, strides: dict[int, int]) -> tuple[int, NDArray[np.uint8]] | None:
        if (2, 56, i) not in res:
            return None
        if len(descriptors) < DESCRIPTOR_SIZE * (i + 1):
            raise CacheError(f"{what}: no descriptor for vertex stream {i}")
        typ, stride = descriptors[DESCRIPTOR_SIZE * i : DESCRIPTOR_SIZE * i + 2]
        if strides.get(typ) != stride:
            raise CacheError(f"{what}: stream {i} type {typ} stride {stride} not supported")
        data = res[2, 56, i]
        if len(data) != vc * stride:
            raise CacheError(f"{what}: stream {i} is {len(data)} bytes, need {vc} x {stride}")
        return typ, np.frombuffer(data, np.uint8).reshape(vc, stride)

    bounds = np.array(model.bounds, np.float64).reshape(7, 2)

    def decompress(v: NDArray[np.uint8], width: int, lo_hi: NDArray[np.float64]) -> Any:
        s = v[:, : 2 * width].copy().view("<i2").astype(np.float64)
        return lo_hi[:, 0] + (s + 32768) / 65535 * (lo_hi[:, 1] - lo_hi[:, 0])

    s0 = stream(0, {1: 12} if level else {t: spec[0] for t, spec in POSITION_TYPES.items()})
    if s0 is None:
        raise CacheError(f"{what}: no position stream")
    ptype, v0 = s0
    _, node_at, per_vertex, weight_at = POSITION_TYPES[ptype]
    positions = v0[:, :12].copy().view("<f4") if ptype == 1 else decompress(v0, 3, bounds[:3])
    if not np.isfinite(positions).all():
        raise CacheError(f"{what}: non-finite vertex positions")
    mesh = SectionMesh(np.asarray(positions, np.float32), None, None, None, None, [])
    _skin(model, sec, res.get(NODE_MAP), v0, node_at, per_vertex, weight_at, mesh, what)

    if (s1 := stream(1, {24: 8} if level else UV_TYPES)) is not None:
        uv = s1[1][:, :8].copy().view("<f4") if s1[0] == 24 else decompress(s1[1], 2, bounds[3:5])
        mesh.texcoords = np.asarray(uv, np.float32)
    if (s2 := stream(2, {NORMAL_TYPE: NORMAL_STRIDE})) is not None:
        word = s2[1][:, :4].copy().view("<u4")[:, 0].astype(np.int64)
        n = np.stack(
            [_sext(word, 11) / 1023, _sext(word >> 11, 11) / 1023, _sext(word >> 22, 10) / 511],
            axis=1,
        )
        length = np.linalg.norm(n, axis=1)
        if len(length) and not ((length >= 0.95) & (length <= 1.05)).all():
            raise CacheError(f"{what}: stored normals are not unit length")
        mesh.normals = (n / length[:, None]).astype(np.float32)

    if level and (s3 := stream(3, {LIGHTMAP_UV_TYPE: LIGHTMAP_UV_STRIDE})) is not None:
        lm = (s3[1].copy().view("<i2") / 32767).astype(np.float32)
        if len(lm) and not ((lm >= 0) & (lm <= 1)).all():
            raise CacheError(f"{what}: lightmap UVs outside [0, 1]")
        mesh.lightmap_uv = lm

    (index_count,) = struct.unpack_from("<I", raw, 40)
    is_list = (
        index_count == 3 * sec.triangle_count
        if level
        else any(locator == LIST_LOCATOR for _, locator, _ in res)
    )
    mesh.parts = _triangles(model, sec, need, index_count, is_list, what)
    return mesh


def _sext(v: NDArray[np.int64], bits: int) -> NDArray[np.int64]:
    v = v & ((1 << bits) - 1)
    return np.where(v >= 1 << (bits - 1), v - (1 << bits), v)


def _skin(
    model: RenderModel,
    sec: Section,
    node_map: bytes | None,
    v0: NDArray[np.uint8],
    node_at: int,
    per_vertex: int,
    weight_at: int | None,
    mesh: SectionMesh,
    what: str,
) -> None:
    vc, count = len(v0), len(model.nodes)
    if count == 0:
        return
    ids = np.zeros((vc, 4), np.int64)
    weights = np.zeros((vc, 4), np.float64)
    if per_vertex == 0:  # rigid: the whole section follows one node
        if not 0 <= sec.rigid_node < count:
            mesh.warnings.append(f"{what}: rigid node {sec.rigid_node} not in {count} nodes")
            return
        ids[:, 0], weights[:, 0] = sec.rigid_node, 1
    else:
        ids[:, :per_vertex] = v0[:, node_at : node_at + per_vertex]
        if weight_at is None:
            weights[:, 0] = 1
        else:
            weights[:, :per_vertex] = v0[:, weight_at : weight_at + per_vertex]
        total = weights.sum(axis=1)
        if (total == 0).any():
            raise CacheError(f"{what}: vertex with zero total weight")
        weights /= total[:, None]  # stored sums are 254, 255 or 256
        ids = np.where(weights > 0, ids, 0)
        if node_map is not None:
            table = np.frombuffer(node_map, np.uint8).astype(np.int64)
            if int(ids.max()) >= len(table):
                raise CacheError(f"{what}: vertex node {int(ids.max())} past the node map")
            ids = table[ids]
            if int(ids.max()) >= count:  # a node map pointing past the skeleton is bad data
                mesh.warnings.append(
                    f"{what}: vertex node {int(ids.max())} not in {count} nodes; exported unskinned"
                )
                return
        elif int(ids.max()) >= count:
            # fp_* sections index a hardware palette larger than the skeleton (+0x24); the extra
            # matrices are engine-driven helpers (fp_body knees, beam rifle charge lights). [M]
            # shortcut: bind each extra id to the node nearest its vertices in the bind pose.
            extra = sorted({int(k) for k in np.unique(ids[ids >= count])})
            inverse = np.tile(np.eye(4), (count, 1, 1))
            stored = np.array([n.inverse_bind for n in model.nodes], np.float64)
            inverse[:, :3, :4] = stored.reshape(-1, 4, 3).transpose(
                0, 2, 1
            )  # as inverse_bind_matrices
            world = np.linalg.inv(inverse)[:, :3, 3]
            for k in extra:
                centre = mesh.positions[(ids == k).any(axis=1)].mean(axis=0)
                ids[ids == k] = int(np.argmin(np.linalg.norm(world - centre, axis=1)))
            mesh.warnings.append(
                f"{what}: vertex nodes {extra} not in {count} nodes; bound to the nearest node"
            )
    if not (np.abs(weights.sum(axis=1) - 1) <= TOLERANCE).all():
        raise CacheError(f"{what}: weights do not sum to 1")
    mesh.joints, mesh.weights = ids.astype(np.uint16), weights.astype(np.float32)


def _triangles(
    model: RenderModel,
    sec: Section,
    need: Callable[[tuple[int, int, int]], bytes],
    index_count: int,
    is_list: bool,
    what: str,
) -> list[tuple[int, NDArray[np.uint16]]]:
    raw_indices = need(INDICES)
    if len(raw_indices) != 2 * index_count:
        raise CacheError(
            f"{what}: index buffer is {len(raw_indices)} bytes, need {index_count} x 2"
        )
    idx = np.frombuffer(raw_indices, "<u2")
    if index_count and int(idx.max()) >= sec.vertex_count:
        raise CacheError(f"{what}: index {int(idx.max())} >= {sec.vertex_count} vertices")
    parts_raw = need(PARTS)
    if len(parts_raw) != sec.part_count * PART_SIZE:
        raise CacheError(f"{what}: {len(parts_raw)} bytes of parts, expected {sec.part_count}")
    out: list[tuple[int, NDArray[np.uint16]]] = []
    expect = 0
    for i in range(sec.part_count):
        material, start, count = struct.unpack_from("<hHH", parts_raw, i * PART_SIZE + 4)
        if start != expect:
            raise CacheError(f"{what}: part {i} starts at {start}, expected {expect}")
        expect += count
        if not 0 <= material < len(model.materials):
            raise CacheError(f"{what}: part {i} material {material} not in the model")
        ix = idx[start : start + count]
        if is_list:
            if count % 3:
                raise CacheError(f"{what}: list part {i} has {count} indices")
            tris = ix
        else:
            tris = _unstrip(ix)
        out.append((material, tris.astype(np.uint16)))
    if expect != index_count:
        raise CacheError(f"{what}: parts cover {expect} of {index_count} indices")
    if sum(len(t) for _, t in out) != 3 * sec.triangle_count:
        raise CacheError(f"{what}: triangles do not match the stored count")
    return out


def _unstrip(ix: NDArray[np.uint16]) -> NDArray[np.uint16]:
    """Triangle strip -> list. Parity counts from the strip start, also across degenerates."""
    if len(ix) < 3:
        return np.zeros(0, np.uint16)
    a, b, c = ix[:-2], ix[1:-1], ix[2:]
    odd = np.arange(len(a)) % 2 == 1
    keep = (a != b) & (b != c) & (a != c)
    tris = np.stack([np.where(odd, b, a), np.where(odd, a, b), c], axis=1)
    return np.ascontiguousarray(tris[keep].reshape(-1))


def read_shader(
    f: BinaryIO, header: CacheHeader, tags: list[TagEntry], datum: int
) -> ShaderInfo | None:
    """Template, diffuse bitmap and postprocess bitmaps of a shad, or None if the datum is none."""
    tag = tag_by_datum(tags, datum)
    if tag is None or tag.group != "shad":
        return None
    meta_start = header.index_offset + header.index_size
    m = _Meta(f, tag, 0x28, (tags[0].address, meta_start, header.meta_data_size))

    def name(d: int) -> str | None:
        t = tag_by_datum(tags, d)
        return None if t is None else t.name or f"{t.group}_{t.datum:08x}"

    runtime = m.block(m.raw, 0x0C, SHAD_RUNTIME_SIZE, "runtime properties")
    diffuse = tag_by_datum(tags, struct.unpack_from("<I", runtime[0], 4)[0]) if runtime else None
    post = m.block(m.raw, 0x20, SHAD_POSTPROCESS_SIZE, "postprocess")
    datums = (
        [
            struct.unpack_from("<I", b)[0]
            for b in m.block(post[0], 4, SHAD_BITMAP_SIZE, "postprocess bitmaps")
        ]
        if post
        else []
    )
    template = name(struct.unpack_from("<I", m.raw, 4)[0])
    source = None
    if diffuse is not None and diffuse.group == "bitm":
        source = "runtime"
    else:
        diffuse = None
        slot = BASE_SLOT_BY_TEMPLATE.get((template or "").replace("\\", "/").split("/")[-1])
        if slot is not None and slot < len(datums):
            fallback = tag_by_datum(tags, datums[slot])
            if fallback is not None and fallback.group == "bitm":
                diffuse, source = fallback, f"postprocess[{slot}]"
    return ShaderInfo(
        name=m.name,
        template=template,
        diffuse=diffuse,
        bitmaps=tuple(name(d) for d in datums),
        diffuse_source=source,
    )


@dataclass(frozen=True)
class ModelPart:
    """One model in a scene. `attach` hangs root nodes under a node of an earlier part, by name."""

    model: RenderModel
    meshes: dict[int, SectionMesh]
    shaders: Sequence[ShaderInfo | None]
    texture_uri: Callable[[TagEntry], str]
    attach: Mapping[str, str] = field(default_factory=dict)


def to_glb(
    model: RenderModel,
    meshes: dict[int, SectionMesh],
    shaders: Iterable[ShaderInfo | None],
    texture_uri: Callable[[TagEntry], str],
    extras: dict[str, Any],
    lod: int = LODS - 1,
    animate: Callable[[Gltf, dict[str, int]], list[str]] | None = None,
) -> tuple[bytes, list[str]]:
    """One .glb: skeleton, one mesh per section used at `lod`, one node per region:permutation.

    `animate` gets the builder and {node name: glTF node} once the skeleton exists.

    `meshes` holds the decoded sections used at `lod` (see sections_at).
    Returns the file and its warnings.
    """
    part = ModelPart(model, meshes, tuple(shaders), texture_uri)
    return models_to_glb([part], extras, lod, animate)


def models_to_glb(
    parts: Sequence[ModelPart],
    extras: dict[str, Any],
    lod: int = LODS - 1,
    animate: Callable[[Gltf, dict[str, int]], list[str]] | None = None,
) -> tuple[bytes, list[str]]:
    """Several skinned models in one .glb (e.g. first-person arms + weapon), one skin each."""
    warnings: list[str] = []
    g = Gltf(extras={**extras, "lod": lod + 1})
    root = g.node("halo_to_gltf", rotation=HALO_TO_GLTF_ROTATION, scale=(WORLD_UNIT_METRES,) * 3)
    names: dict[str, int] = {}
    joints: list[dict[int, int]] = []
    for part in parts:
        joint: dict[int, int] = {}
        for i in node_order(part.model):
            n = part.model.nodes[i]
            if n.parent >= 0:
                parent = joint[n.parent]
            elif (target := part.attach.get(n.name)) is not None:
                if target not in names:
                    raise CacheError(f"{part.model.name}: no node {target} to attach to")
                parent = names[target]
            else:
                parent = root
            joint[i] = g.node(n.name, parent=parent, translation=n.translation, rotation=n.rotation)
        names.update({part.model.nodes[i].name: j for i, j in joint.items()})
        joints.append(joint)
    if animate is not None:
        warnings += animate(g, names)
    for part, joint in zip(parts, joints, strict=True):
        warnings += _add_meshes(g, root, part, joint, lod)
    return g.glb(), warnings


def _add_meshes(g: Gltf, root: int, part: ModelPart, joint: dict[int, int], lod: int) -> list[str]:
    model, meshes = part.model, part.meshes
    warnings: list[str] = []
    skin = None
    if model.nodes:
        first_root = next(joint[i] for i, n in enumerate(model.nodes) if n.parent < 0)
        joints = [joint[i] for i in range(len(model.nodes))]
        skin = g.skin(joints, inverse_bind_matrices(model), skeleton=first_root)
    materials = add_materials(g, part.shaders, part.texture_uri)
    joint_type = np.uint8 if len(model.nodes) <= 256 else np.uint16
    mesh_ids: dict[int, int] = {}
    for region in model.regions:
        for p in region.permutations:
            section = p.lods[lod]
            if section < 0 or model.sections[section].vertex_count == 0:
                continue
            if section not in mesh_ids:
                sm = meshes[section]
                mesh_ids[section] = g.mesh(
                    f"section_{section}",
                    sm.positions,
                    [(t, materials[mat]) for mat, t in sm.parts],
                    normals=sm.normals,
                    texcoords=sm.texcoords,
                    joints=None if sm.joints is None else sm.joints.astype(joint_type),
                    weights=sm.weights,
                )
            g.node(
                f"{region.name}:{p.name}",
                parent=root,
                mesh=mesh_ids[section],
                skin=skin if meshes[section].joints is not None else None,
                extras={"section": section},
            )
    for mk in model.markers:
        if mk.node >= len(model.nodes):
            warnings.append(f"marker {mk.group}: node {mk.node} not in the model")
            continue
        g.node(
            f"marker:{mk.group}",
            parent=joint[mk.node],
            translation=mk.translation,
            rotation=mk.rotation,
            extras={"region": mk.region, "permutation": mk.permutation, "scale": mk.scale},
        )
    return warnings


# Template-name prefixes the game draws blended. [M] From the names; glTF has no additive mode, so
# additive templates (one_add_*, two_add_*) get BLEND as the nearest thing.
BLEND_PREFIXES = ("one_alpha", "two_alpha", "sky_", "plasma_alpha", "overlay", "decal", "one_add",
                  "two_add", "waves", "water")  # fmt: skip


def alpha_mode(template: str | None) -> str:
    """glTF alphaMode for a shader template: MASK for alpha-test, BLEND for blended, else OPAQUE."""
    base = (template or "").replace("\\", "/").split("/")[-1]
    if "alpha_test" in base:
        return "MASK"
    return "BLEND" if base.startswith(BLEND_PREFIXES) else "OPAQUE"


def add_materials(
    g: Gltf, shaders: Iterable[ShaderInfo | None], texture_uri: Callable[[TagEntry], str]
) -> list[int]:
    """One glTF material per shader, textured with its diffuse; `material_<i>` when unknown."""
    materials = []
    for i, shader in enumerate(shaders):
        if shader is None:
            materials.append(g.material(f"material_{i}"))
            continue
        materials.append(
            g.material(
                shader.name,
                texture_uri(shader.diffuse) if shader.diffuse else None,
                {
                    "shader": shader.name,
                    "template": shader.template,
                    "bitmaps": shader.bitmaps,
                    "diffuse_source": shader.diffuse_source,
                },
                alpha_mode(shader.template),
            )
        )
    return materials


def sections_at(model: RenderModel, lod: int = LODS - 1) -> list[int]:
    """Section indices with geometry that some permutation uses at `lod`, in first-use order."""
    used = (p.lods[lod] for r in model.regions for p in r.permutations)
    return list(dict.fromkeys(s for s in used if s >= 0 and model.sections[s].vertex_count))


def relative_uri(from_file: str, to_file: str) -> str:
    """URI of `to_file` relative to the folder holding `from_file` (both export paths)."""
    return urllib.parse.quote(posixpath.relpath(to_file, posixpath.dirname(from_file) or "."))
