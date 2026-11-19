"""Halo 2 render models (mode) -> glTF. Synthetic maps built in code only, never game data."""

from __future__ import annotations

import dataclasses
import io
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fixtures.halo2_builder import bitm_meta, bitmap_entry, make_map, meta_address, sid, tag_datum
from fixtures.xbox_builder import build_partition, make_xbe, write_image

from rrl.adapters.xbox_halo2 import Halo2Adapter, _first_person, _graphs_by_model
from rrl.adapters.xbox_halo2.bitmaps import read_raw
from rrl.adapters.xbox_halo2.cachefile import (
    CacheError,
    StringIds,
    TagEntry,
    parse_header,
    read_tag_index,
)
from rrl.adapters.xbox_halo2.render_model import (
    ModelPart,
    RenderModel,
    SectionMesh,
    _unstrip,
    alpha_mode,
    decode_section,
    inverse_bind_matrices,
    models_to_glb,
    read_render_model,
    read_shader,
    sections_at,
    to_glb,
)
from rrl.api import Asset, Source
from rrl.processors.gltf import read_glb

STRINGS = ["testmodel", "body", "base", "arms", "pelvis", "spine", "head", "legs"]
S = {s: sid(i, s) for i, s in enumerate(STRINGS)}
NONE = 0xFFFFFFFF
BOUNDS = (-1.0, 1.0, -2.0, 2.0, 0.0, 4.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
ROOT_Q = (0.5, 0.5, 0.5, -0.5)
# Strip with a degenerate run: global parity keeps i=4 even, so (2, 3, 4) and not (3, 2, 4).
STRIP = [0, 1, 2, 2, 2, 3, 4]
TAIL_STRIP = [4, 3, 0]


def _q(q: tuple[float, ...]) -> Any:
    x, y, z, w = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def _normal(x: int, y: int, z: int) -> int:
    """Pack 11:11:10 signed (x, y in 1/1023, z in 1/511)."""
    return (x & 0x7FF) | (y & 0x7FF) << 11 | (z & 0x3FF) << 22


def _raw(resources: list[tuple[int, int, int, bytes]], index_count: int) -> tuple[bytes, list[Any]]:
    """hklb-framed blob: 0x70 header, 8 bytes, resource data, fklb. Returns (blob, entries)."""
    data = b""
    entries = []
    for kind, locator, sub, payload in resources:
        entries.append((kind, locator, sub, len(payload), len(data)))
        data += payload + b"\0" * (-len(payload) % 4)
    head = bytearray(0x70)
    head[0:4] = b"hklb"
    struct.pack_into("<I", head, 4, len(data))
    struct.pack_into("<I", head, 40, index_count)
    return bytes(head) + b"rsrc\0\0\0\0" + data + b"fklb", entries


def _descriptors(*streams: tuple[int, int]) -> bytes:
    return b"".join(bytes([t, s]).ljust(0x20, b"\0") for t, s in streams)


def _rigid_blob(strip: list[int] = STRIP, part1_start: int = 7) -> tuple[bytes, list[Any]]:
    pos = b"".join(struct.pack("<3h", *p) for p in [(-32768, -32768, -32768), (32767,) * 3]
                   + [(0, 0, 0)] * 3)  # fmt: skip
    uv = b"".join(struct.pack("<2h", 32767, -32768) for _ in range(5))
    normals = b"".join(struct.pack("<3I", _normal(0, 0, 511), 0, 0) for _ in range(5))
    idx = strip + TAIL_STRIP
    parts = bytearray(0x48 * 2)
    struct.pack_into("<hHH", parts, 4, 0, 0, len(strip))
    struct.pack_into("<hHH", parts, 0x48 + 4, 1, part1_start, len(TAIL_STRIP))
    return _raw(
        [
            (0, 0, 0x48, bytes(parts)),
            (0, 32, 2, struct.pack(f"<{len(idx)}H", *idx)),
            (0, 56, 0x20, _descriptors((2, 6), (25, 4), (27, 12))),
            (2, 56, 0, pos),
            (2, 56, 1, uv),
            (2, 56, 2, normals),
        ],
        len(idx),
    )


def _skinned_blob(node_map: bytes | None = bytes([1, 0])) -> tuple[bytes, list[Any]]:
    verts = [
        ((0, 1, 0, 0), (128, 127, 0, 0)),
        ((1, 0, 0, 0), (255, 0, 0, 0)),
        ((1,) * 4, (64,) * 4),
    ]
    if node_map is None:
        verts[2] = ((5, 0, 0, 0), (255, 0, 0, 0))  # fp_* style: node 5 of a 2-node skeleton
    pos = b"".join(struct.pack("<3hxx4B4B", 0, 0, 0, *n, *w) for n, w in verts)
    uv = b"".join(struct.pack("<2f", 0.25, 3.5) for _ in verts)
    normals = b"".join(
        struct.pack("<3I", _normal(*n), 0, 0) for n in [(1023, 0, 0), (0, -1023, 0), (0, 0, -511)]
    )
    parts = bytearray(0x48)
    struct.pack_into("<hHH", parts, 4, 1, 0, 3)
    resources = [
        (0, 0, 0x48, bytes(parts)),
        (0, 16, 4, b"\0" * 4),  # any locator-16 resource: the indices are a triangle list
        (0, 32, 2, struct.pack("<3H", 0, 1, 2)),
        (0, 56, 0x20, _descriptors((10, 16), (24, 8), (27, 12))),
        (2, 56, 0, pos),
        (2, 56, 1, uv),
        (2, 56, 2, normals),
    ]
    if node_map is not None:
        resources.append((0, 100, 1, node_map))
    return _raw(resources, 3)


class Heap:
    """Lays out tag meta: blocks are appended after the head, children before parents."""

    def __init__(self, address: int, head_size: int) -> None:
        self.address = address
        self.buf = bytearray(head_size)

    def block(self, parent: bytearray, pos: int, elements: list[bytes]) -> None:
        if elements:
            struct.pack_into("<iI", parent, pos, len(elements), self.address + len(self.buf))
            self.buf += b"".join(elements)


def _node(name: str, parent: int, t: tuple[float, ...], q: tuple[float, ...], ibm: Any) -> bytes:
    n = bytearray(0x60)
    struct.pack_into("<Ih", n, 0, S[name], parent)
    struct.pack_into("<3f4ff", n, 0xC, *t, *q, 1.0)
    # inverse(world): columns forward, left, up, then position
    struct.pack_into("<12f", n, 0x2C, *ibm[:3, :3].T.ravel(), *ibm[:3, 3])
    return bytes(n)


def _world() -> tuple[Any, Any]:
    root = np.eye(4)
    root[:3, :3], root[:3, 3] = _q(ROOT_Q), (0, 0, 1)
    child = np.eye(4)
    child[:3, 3] = (1, 0, 0)
    return root, root @ child


def mode_meta(
    address: int,
    sections: list[tuple[int, int, int, int, int, int, list[Any]]],
    shad: int,
    node_ibm: Any = None,
    regions_at: int | None = None,
    header_size: int = 0x70,
) -> bytes:
    """mode meta. sections: (class, vc, tris, parts, rigid node, raw pointer, raw entries)."""
    h = Heap(address, 0x84)
    head = h.buf
    struct.pack_into("<I", head, 0, S["testmodel"])
    h.block(head, 0x14, [struct.pack("<14f", *BOUNDS).ljust(0x38, b"\0")])
    perms = []
    for perm_name, lods in [("base", (-1, -1, -1, -1, 0, 0))]:
        p = bytearray(0x10)
        struct.pack_into("<I6h", p, 0, S[perm_name], *lods)
        perms.append(bytes(p))
    regions = []
    for region, plist in [("body", perms), ("arms", [struct.pack("<I6h", S["base"], *[1] * 6)]),
                          ("legs", perms)]:  # fmt: skip
        r = bytearray(0x10)
        struct.pack_into("<I", r, 0, S[region])
        h.block(r, 8, plist)
        regions.append(bytes(r))
    h.block(head, 0x1C, regions)
    secs = []
    for cls, vc, tris, nparts, rigid, ptr, entries in sections:
        s = bytearray(0x5C)
        struct.pack_into("<hxxHHH", s, 0, cls, vc, tris, nparts)
        struct.pack_into("<h", s, 0x2C, rigid)
        size = 0x70 + 8 + sum(e[3] + (-e[3] % 4) for e in entries) + 4
        struct.pack_into("<4I", s, 0x38, ptr, size, header_size, size - 0x70 - 12)
        h.block(s, 0x48, [struct.pack("<Bxxxhh2I", *e) for e in entries])
        secs.append(bytes(s))
    h.block(head, 0x24, secs)
    root, child = _world()
    ibm = node_ibm if node_ibm is not None else np.linalg.inv(child)
    h.block(head, 0x48, [
        _node("pelvis", -1, (0, 0, 1), ROOT_Q, np.linalg.inv(root)),
        _node("spine", 0, (1, 0, 0), (0, 0, 0, 1), ibm),
    ])  # fmt: skip
    marker = struct.pack("<BBBx3f4ff", 0, 255, 1, 0.1, 0.2, 0.3, 0, 0, 0, 1, 1.0)
    group = bytearray(0xC)
    struct.pack_into("<I", group, 0, S["head"])
    h.block(group, 4, [marker])
    h.block(head, 0x58, [bytes(group)])
    mats = [struct.pack("<8x4sI", b"dahs", d).ljust(0x20, b"\0") for d in (shad, NONE)]
    h.block(head, 0x60, mats)
    if regions_at is not None:  # malformed: region block pointing past the meta
        struct.pack_into("<I", head, 0x20, regions_at)
    return bytes(h.buf)


def shad_meta(
    address: int, bitm: int, runtime: int | None = None, template: int = NONE, slot: int = 0
) -> bytes:
    """`runtime`: runtime diffuse datum (default `bitm`). Postprocess bitmaps: `bitm` at `slot`."""
    h = Heap(address, 0x28)
    head = h.buf
    struct.pack_into("<4sI", head, 0, b"mets", template)
    diffuse = bitm if runtime is None else runtime
    h.block(head, 0x0C, [struct.pack("<4sI", b"mtib", diffuse).ljust(0x50, b"\0")])
    datums = [NONE] * max(2, slot + 1)
    datums[slot] = bitm
    post = bytearray(0x7C)
    h.block(post, 4, [struct.pack("<I", d).ljust(0xC, b"\0") for d in datums])
    h.block(head, 0x20, [bytes(post)])
    return bytes(h.buf)


Mutate = Callable[[list[Any]], None]


def build(
    rigid: tuple[bytes, list[Any]] | None = None,
    skinned: tuple[bytes, list[Any]] | None = None,
    **mode_kw: Any,
) -> bytes:
    blobs = [rigid or _rigid_blob(), skinned or _skinned_blob()]

    def tags(end: int) -> list[tuple[str, str, int | bytes]]:
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals\\globals", 0x40)]
        out.append(("bitm", "objects\\test\\diffuse", bitm_meta(meta_address(out, 1), [
            bitmap_entry(4, 4, 11, 0, 64),
        ])))  # fmt: skip
        out.append(
            ("shad", "objects\\test\\shaders\\metal", shad_meta(meta_address(out, 2), tag_datum(1)))
        )
        ptrs = [end, end + len(blobs[0][0])]
        sections = [
            (1, 5, 3, 2, 0, ptrs[0], blobs[0][1]),
            (3, 3, 1, 1, -1, ptrs[1], blobs[1][1]),
        ]
        meta = mode_meta(meta_address(out, 3), sections, tag_datum(2), **mode_kw)
        out.append(("mode", "objects\\test\\testmodel", meta))
        return out

    end = len(make_map(tags(0), string_ids=STRINGS))
    return make_map(tags(end), string_ids=STRINGS, tail=blobs[0][0] + blobs[1][0])


def _open(data: bytes) -> tuple[io.BytesIO, Any, list[TagEntry], TagEntry]:
    f = io.BytesIO(data)
    h = parse_header(f)
    tags = read_tag_index(f, h)
    return f, h, tags, next(t for t in tags if t.group == "mode")


def _decode(data: bytes) -> tuple[RenderModel, dict[int, SectionMesh]]:
    f, h, tags, mode = _open(data)
    model = read_render_model(f, mode, StringIds(f, h))
    no_shared: Callable[[str], Any] = lambda name: pytest.fail(name)  # noqa: E731
    return model, {
        s: decode_section(model, s, read_raw(model.sections[s].raw, f, no_shared))
        for s in sections_at(model)
    }


def test_unstrip_parity_not_reset_by_degenerates() -> None:
    # One degenerate between two real triangles: global parity makes (2, 3, 4) odd -> (3, 2, 4).
    tris = _unstrip(np.array([0, 1, 2, 2, 3, 4], np.uint16)).reshape(-1, 3).tolist()
    assert tris == [[0, 1, 2], [3, 2, 4]]


def test_string_ids() -> None:
    f, h, _, _ = _open(build())
    sids = StringIds(f, h)
    assert [sids(S[s]) for s in STRINGS] == STRINGS
    with pytest.raises(CacheError, match="bad offset or length"):
        sids(S["body"] + (1 << 24))  # wrong length byte
    with pytest.raises(CacheError, match="past the 8 ids"):
        sids(99)


def test_read_model() -> None:
    model, meshes = _decode(build())
    assert model.name == "testmodel"
    assert [(r.name, [p.name for p in r.permutations]) for r in model.regions] == [
        ("body", ["base"]),
        ("arms", ["base"]),
        ("legs", ["base"]),
    ]
    assert [n.name for n in model.nodes] == ["pelvis", "spine"]
    assert model.markers[0].group == "head" and model.markers[0].node == 1
    assert sections_at(model) == [0, 1]  # body and legs share section 0

    rigid = meshes[0]
    assert rigid.positions[0].tolist() == [-1, -2, 0] and rigid.positions[1].tolist() == [1, 2, 4]
    assert rigid.texcoords is not None and rigid.texcoords[0].tolist() == [1, 0]
    assert rigid.normals is not None and rigid.normals[0].tolist() == [0, 0, 1]
    assert [(m, t.tolist()) for m, t in rigid.parts] == [(0, [0, 1, 2, 2, 3, 4]), (1, [4, 3, 0])]
    assert rigid.joints is not None and rigid.joints.tolist() == [[0, 0, 0, 0]] * 5
    assert rigid.weights is not None and rigid.weights[:, 0].tolist() == [1] * 5

    skinned = meshes[1]
    assert [t.tolist() for _, t in skinned.parts] == [[0, 1, 2]]  # list, not unstripped
    assert skinned.texcoords is not None and skinned.texcoords[0].tolist() == [0.25, 3.5]
    assert skinned.normals is not None
    assert skinned.normals.tolist() == [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
    assert skinned.joints is not None and skinned.weights is not None
    # node map [1, 0]; zero-weight influences point at local 0 -> node 1
    assert skinned.joints.tolist() == [[1, 0, 1, 1], [0, 1, 1, 1], [0, 0, 0, 0]]
    assert np.allclose(skinned.weights.sum(axis=1), 1)
    assert np.allclose(skinned.weights[0, :2], [128 / 255, 127 / 255])
    assert skinned.warnings == []


def test_inverse_bind() -> None:
    model, _ = _decode(build())
    _, child = _world()
    ibm = inverse_bind_matrices(model)
    assert np.allclose(ibm[1].reshape(4, 4).T, np.linalg.inv(child), atol=1e-6)
    bad, _ = _decode(build(node_ibm=np.eye(4)))
    with pytest.raises(CacheError, match="inverse bind"):
        inverse_bind_matrices(bad)


def test_unmapped_nodes_warn() -> None:
    model, meshes = _decode(build(skinned=_skinned_blob(node_map=None)))
    joints = meshes[1].joints
    assert joints is not None and int(joints.max()) < len(model.nodes)  # rebound, still skinned
    assert meshes[1].warnings == [
        "testmodel section 1: vertex nodes [5] not in 2 nodes; bound to the nearest node"
    ]


def _corrupt_raw(offset: int, value: bytes) -> tuple[bytes, list[Any]]:
    blob, entries = _rigid_blob()
    return blob[:offset] + value + blob[offset + len(value) :], entries


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rigid": _corrupt_raw(0, b"XXXX")}, "hklb/fklb"),
        ({"rigid": _corrupt_raw(4, b"\0\0\0\0")}, "resource size"),
        ({"rigid": _rigid_blob(strip=[0, 1, 2, 2, 2, 3, 9])}, "index 9 >= 5"),
        ({"rigid": _rigid_blob(part1_start=8)}, "part 1 starts at 8"),
        ({"rigid": _rigid_blob(strip=[0, 1, 2, 3, 4, 0, 1])}, "do not match the stored count"),
        ({"regions_at": 0x80100000 + 0x10000}, "outside tag meta"),
    ],
)
def test_malformed(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(CacheError, match=message):
        _decode(build(**kwargs))


def test_size_mismatch() -> None:
    with pytest.raises(CacheError, match="header and data say otherwise"):
        _decode(build(header_size=0x74))


@pytest.mark.parametrize(
    ("template", "slot", "expect"),
    [
        ("shaders\\shader_templates\\one_alpha_env", 2, "postprocess[2]"),
        ("shaders\\shader_templates\\one_alpha_env", 0, None),  # slot 2 is empty: no guess
        ("shaders\\shader_templates\\water_pool", 0, None),  # slot 0 is a bump map: skipped
    ],
)
def test_shader_base_slot_fallback(template: str, slot: int, expect: str | None) -> None:
    def tags(end: int) -> list[tuple[str, str, int | bytes]]:
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals\\globals", 0x40)]
        out.append(("bitm", "objects\\test\\glass", bitm_meta(meta_address(out, 1), [
            bitmap_entry(4, 4, 11, 0, 64),
        ])))  # fmt: skip
        out.append(("stem", template, 0x40))
        shad = shad_meta(meta_address(out, 3), tag_datum(1), NONE, tag_datum(2), slot)
        out.append(("shad", "objects\\test\\shaders\\glass", shad))
        return out

    f = io.BytesIO(make_map(tags(0)))
    h = parse_header(f)
    index = read_tag_index(f, h)
    info = read_shader(f, h, index, tag_datum(3))
    assert info is not None and info.template == template
    assert info.diffuse_source == expect
    assert (info.diffuse is not None) == (expect is not None)


@pytest.mark.parametrize(
    ("template", "mode"),
    [
        ("shaders\\shader_templates\\opaque\\tex_bump", "OPAQUE"),
        ("shaders\\shader_templates\\opaque\\tex_bump_alpha_test", "MASK"),
        ("shaders\\shader_templates\\transparent\\one_alpha_env", "BLEND"),
        ("shaders\\shader_templates\\transparent\\two_add_detail_masked", "BLEND"),
        (None, "OPAQUE"),
    ],
)
def test_alpha_mode(template: str | None, mode: str) -> None:
    assert alpha_mode(template) == mode


def test_to_glb() -> None:
    data = build()
    f, h, tags, _ = _open(data)
    model, meshes = _decode(data)
    shaders = [read_shader(f, h, tags, d) for d in model.materials]
    assert shaders[1] is None
    assert shaders[0] is not None and shaders[0].bitmaps == ("objects\\test\\diffuse", None)
    glb, warnings = to_glb(model, meshes, shaders, lambda b: f"{b.name}.png", {"map": "m"})
    assert warnings == []
    doc, binary = read_glb(glb)
    nodes = doc["nodes"]
    root = nodes[0]
    assert root["name"] == "halo_to_gltf" and root["scale"] == [3.048] * 3
    names = [nodes[i]["name"] for i in root["children"]]
    assert names == ["pelvis", "body:base", "arms:base", "legs:base"]
    by_name = {n["name"]: n for n in nodes}
    assert by_name["body:base"]["mesh"] == by_name["legs:base"]["mesh"] == 0
    assert by_name["marker:head"]["translation"] == pytest.approx([0.1, 0.2, 0.3])
    assert doc["skins"][0]["joints"] == [1, 2]
    assert doc["asset"]["extras"] == {"map": "m", "lod": 6}
    assert len(doc["meshes"]) == 2
    prim = doc["meshes"][0]["primitives"][0]
    assert doc["accessors"][prim["attributes"]["POSITION"]]["min"] == [-1, -2, 0]
    assert doc["accessors"][prim["attributes"]["JOINTS_0"]]["componentType"] == 5121
    assert doc["images"] == [{"uri": "objects\\test\\diffuse.png"}]
    assert all(v["byteOffset"] % 4 == 0 for v in doc["bufferViews"])
    assert len(binary) % 4 == 0


def _disc(tmp_path: Path, data: bytes) -> Source:
    tree = {
        "default.xbe": make_xbe(title="Halo 2", title_id=0x4D530064),
        "maps": {"testmap.map": data},
    }
    iso = write_image(tmp_path / "disc.iso", build_partition(tree))
    return Source(iso, "ab" * 32, iso.stat().st_size)


def test_extract_selection(tmp_path: Path) -> None:
    source = _disc(tmp_path, build())
    adapter = Halo2Adapter()
    default = [i.logical_path for i in adapter.extract(source, ["testmap"])]
    assert default == ["testmap/objects/test/diffuse_00.png"]  # unchanged: textures only
    models = list(adapter.extract(source, ["models"]))
    assert [i.logical_path for i in models] == ["testmap/objects/test/testmodel.glb"]
    asset = models[0]
    assert isinstance(asset, Asset) and asset.media_type == "model/gltf-binary"
    assert asset.metadata["kind"] == "model" and asset.metadata["warnings"] == []
    doc, _ = read_glb(asset.data)
    assert doc["images"] == [{"uri": "diffuse_00.png"}]  # relative to the .glb
    assert doc["asset"]["extras"]["source_sha256"] == "ab" * 32
    both = [i.logical_path for i in adapter.extract(source, ["Models", "textures", "testmap"])]
    assert sorted(both) == sorted(default + ["testmap/objects/test/testmodel.glb"])


def test_models_to_glb_attach() -> None:
    data = build()
    f, h, tags, _ = _open(data)
    model, meshes = _decode(data)
    shaders = [read_shader(f, h, tags, d) for d in model.materials]
    renamed = dataclasses.replace(
        model, nodes=tuple(dataclasses.replace(n, name=f"w_{n.name}") for n in model.nodes)
    )
    arms = ModelPart(model, meshes, shaders, lambda b: f"{b.name}.png")
    weapon = ModelPart(renamed, meshes, shaders, lambda b: f"{b.name}.png", {"w_pelvis": "pelvis"})
    glb, _ = models_to_glb([arms, weapon], {"map": "m"})
    doc, _ = read_glb(glb)
    nodes = doc["nodes"]
    hand = next(i for i, n in enumerate(nodes) if n["name"] == "pelvis")
    w_root = next(i for i, n in enumerate(nodes) if n["name"] == "w_pelvis")
    assert w_root in nodes[hand]["children"]  # the weapon hangs from the named arms node
    assert len(doc["skins"]) == 2
    single, _ = to_glb(model, meshes, shaders, lambda b: f"{b.name}.png", {"map": "m"})
    alone, _ = models_to_glb([arms], {"map": "m"})
    assert single == alone  # the single-model path is unchanged
    lost = ModelPart(renamed, meshes, shaders, lambda b: "", {"w_pelvis": "nowhere"})
    with pytest.raises(CacheError, match="no node nowhere"):
        models_to_glb([arms, lost], {})


def _tagref(group: str, datum: int) -> bytes:
    return struct.pack("<4sI", group.encode()[::-1], datum)


def test_graph_pairing_and_first_person() -> None:
    hlmt = _tagref("mode", tag_datum(1)) + bytes(8) + _tagref("jmad", tag_datum(4))
    weap = bytearray(0x2B0 + 0x10)  # first-person block address is set once the meta is placed
    tags: list[tuple[str, str, int | bytes]] = [
        ("matg", "globals\\globals", 0x40),
        ("mode", "objects\\vehicles\\hog\\hog", 0x40),  # paired only through its hlmt
        ("mode", "objects\\characters\\chief\\fp\\fp", 0x40),  # arms
        ("mode", "objects\\weapons\\rifle\\br\\fp_br\\fp_br", 0x40),
        ("jmad", "objects\\vehicles\\hog\\hog_graph", 0x40),
        ("jmad", "objects\\characters\\chief\\fp\\weapons\\rifle\\fp_br\\fp_br", 0x40),
        ("hlmt", "objects\\vehicles\\hog\\hog", hlmt),
    ]
    address = meta_address([*tags, ("weap", "w", bytes(weap))], len(tags))
    struct.pack_into("<iI", weap, 0x2A8, 1, address + 0x2B0)
    weap[0x2B0:] = _tagref("mode", tag_datum(3)) + _tagref("jmad", tag_datum(5))
    tags.append(("weap", "objects\\weapons\\rifle\\br\\br", bytes(weap)))
    f = io.BytesIO(make_map(tags))
    h = parse_header(f)
    index = read_tag_index(f, h)
    graphs = _graphs_by_model(f, index)
    assert graphs["objects\\vehicles\\hog\\hog"].name == "objects\\vehicles\\hog\\hog_graph"
    (scene,) = _first_person(f, h, index)
    assert [t.name for t in scene] == [
        "objects\\characters\\chief\\fp\\fp",
        "objects\\weapons\\rifle\\br\\fp_br\\fp_br",
        "objects\\characters\\chief\\fp\\weapons\\rifle\\fp_br\\fp_br",
    ]
