"""Halo 2 level geometry (sbsp) -> glTF. Synthetic maps built in code only, never game data."""

from __future__ import annotations

import io
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fixtures.halo2_builder import make_map, meta_address, scnr_meta, sid, tag_datum
from fixtures.xbox_builder import build_partition, make_xbe, write_image

from rrl.adapters.xbox_halo2 import Halo2Adapter
from rrl.adapters.xbox_halo2.bitmaps import read_raw
from rrl.adapters.xbox_halo2.cachefile import CacheError, StringIds, parse_header, read_tag_index
from rrl.adapters.xbox_halo2.ltmp import BspEntry, bsp_entries
from rrl.adapters.xbox_halo2.render_model import _quat_matrix, decode_section
from rrl.adapters.xbox_halo2.structure_bsp import (
    Instance,
    instance_problem,
    lightmap_group,
    quat_from_matrix,
    read_structure_bsp,
)
from rrl.api import Asset, Skipped, Source
from rrl.processors.gltf import read_glb

BASE = 0x8E000000  # BSP load address; any value works
NONE = 0xFFFFFFFF
STRINGS = ["crate", "crate_turned"]
CLUSTER_POS = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
CLUSTER_STRIP = [0, 1, 2, 3]  # 2 triangles, stored as a strip (4 indices != 3 x 2)
DEF_POS = [(1, 0, 0), (0, 2, 0), (0, 0, 3)]
LM_UV = [(0, 0), (32767, 0), (16384, 32767)]
# (scale, forward, left, up, position, definition, name, policy); F/L/U = images of +X/+Y/+Z
TURN = ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))  # 90 degrees about +Z
INSTANCES = [
    (1.0, *np.eye(3).tolist(), (10.0, 0.0, 0.0), 0, sid(0, "crate"), 0),
    (2.0, *TURN, (0.0, 5.0, 0.0), 0, sid(1, "crate_turned"), 1),
    (1.0, *np.eye(3).tolist(), (0.0, 0.0, 0.0), 1, 0, 0),  # empty definition: warned, not placed
]


def _raw(resources: list[tuple[int, int, int, bytes]], index_count: int) -> tuple[bytes, bytes]:
    """hklb-framed blob with a 0x44 header. Returns (blob, packed resource entries)."""
    data, entries = b"", b""
    for kind, locator, sub, payload in resources:
        entries += struct.pack("<Bxxxhh2I", kind, locator, sub, len(payload), len(data))
        data += payload + b"\0" * (-len(payload) % 4)
    head = bytearray(0x44)
    head[0:4] = b"hklb"
    struct.pack_into("<I", head, 4, len(data))
    struct.pack_into("<I", head, 40, index_count)
    return bytes(head) + b"rsrc\0\0\0\0" + data + b"fklb", entries


def _streams(pos: list[Any], extra: list[tuple[int, int, bytes]]) -> list[Any]:
    """Resources of a section with stream 0 = f32 positions, 1 = f32 uv, 2 = +Z normals, + extra."""
    n = len(pos)
    desc = [(1, 12), (24, 8), (27, 12)] + [(t, s) for t, s, _ in extra]
    return [
        (0, 56, 0x20, b"".join(bytes([t, s]).ljust(0x20, b"\0") for t, s in desc)),
        (2, 56, 0, b"".join(struct.pack("<3f", *p) for p in pos)),
        (2, 56, 1, struct.pack("<2f", 0.5, 0.25) * n),
        (2, 56, 2, struct.pack("<3I", 511 << 22, 0, 0) * n),
    ] + [(2, 56, 3 + i, data) for i, (_, _, data) in enumerate(extra)]


def _section(element: int, vc: int, tc: int, lighting: int, ptr: int, blob: bytes,
             entries: bytes, at: int) -> bytes:  # fmt: skip
    """A cluster (0xB0) or definition (0xC8) element whose resource entries sit at `at`."""
    s = bytearray(element)
    struct.pack_into("<3H", s, 0, vc, tc, 1 if vc else 0)
    struct.pack_into("<hH", s, 0x14, 0 if element == 0xB0 else 1, 4 if lighting else 0)
    struct.pack_into("<H", s, 0x26, lighting)
    data_size = len(blob) - 0x44 - 12
    struct.pack_into("<4I", s, 0x28, ptr, len(blob), 0x44, data_size)
    struct.pack_into("<iI", s, 0x38, len(entries) // 0x10, at)
    return bytes(s)


def _blobs(def_lighting: int = 1) -> list[tuple[bytes, bytes]]:
    part = struct.pack("<4xhHH", 0, 0, 4).ljust(0x48, b"\0")
    cluster = _raw(
        [(0, 0, 0x48, part), (0, 32, 2, struct.pack("<4H", *CLUSTER_STRIP))]
        + _streams(CLUSTER_POS, []),
        4,
    )
    lm = b"".join(struct.pack("<2h", *uv) for uv in LM_UV)
    extra = [(31, 4, lm)] if def_lighting else []
    part = struct.pack("<4xhHH", 0, 0, 3).ljust(0x48, b"\0")
    definition = _raw(
        [(0, 0, 0x48, part), (0, 32, 2, struct.pack("<3H", 0, 1, 2))] + _streams(DEF_POS, extra), 3
    )
    return [cluster, definition]


def _area(tail_at: int, *, def_lighting: int = 1, cluster_header: int = 0x44) -> bytes:
    """BSP area at BASE: header, sbsp, ltmp, blocks. Raws live at file offset tail_at."""
    blobs = _blobs(def_lighting)
    buf = bytearray(0x10 + 0x148 + 0x88 + 0x68)
    sbsp, ltmp, group = 0x10, 0x158, 0x1E0

    def put(data: bytes) -> int:
        buf.extend(data)
        return BASE + len(buf) - len(data)

    def block(at: int, elements: list[bytes]) -> None:
        struct.pack_into("<iI", buf, at, len(elements), put(b"".join(elements)) if elements else 0)

    clusters, defs = [], []
    ptr = tail_at
    for i, (blob, entries) in enumerate(blobs):
        res_at = put(entries)
        if i == 0:
            clusters.append(_section(0xB0, 4, 2, 0, ptr, blob, entries, res_at))
        else:
            defs.append(_section(0xC8, 3, 1, def_lighting, ptr, blob, entries, res_at))
        ptr += len(blob)
    defs.append(bytes(0xC8))  # an empty definition (vc 0)
    if cluster_header != 0x44:
        c = bytearray(clusters[0])
        struct.pack_into("<I", c, 0x30, cluster_header)
        clusters[0] = bytes(c)
    block(sbsp + 0x9C, clusters)
    block(sbsp + 0xA4, [struct.pack("<8x4sI", b"dahs", NONE).ljust(0x20, b"\0")])
    block(sbsp + 0x138, defs)
    insts = []
    for scale, f, left, up, p, d, name, policy in INSTANCES:
        e = bytearray(0x58)
        struct.pack_into("<13f", e, 0, scale, *f, *left, *up, *p)
        struct.pack_into("<h", e, 0x34, d)
        struct.pack_into("<I", e, 0x50, name)
        struct.pack_into("<h", e, 0x56, policy)
        insts.append(bytes(e))
    block(sbsp + 0x140, insts)
    struct.pack_into("<iI", buf, ltmp + 0x80, 1, BASE + group)
    struct.pack_into("<4sI", buf, group + 0x18, b"mtib", tag_datum(1))
    block(group + 0x28, [struct.pack("<hBB", 3, 0, 0)])
    block(group + 0x48, [struct.pack("<hBB", 4, 1, 0), struct.pack("<hBB", -1, 255, 0)])
    struct.pack_into("<3I4s", buf, 0, len(buf), BASE + sbsp, BASE + ltmp, b"psbs")
    return bytes(buf)


def build(**area_kw: Any) -> bytes:
    """matg, lightmap bitm, scnr with a good BSP and one whose area lies past the file, sbsp."""

    def tags(end: int) -> list[tuple[str, str, int | bytes]]:
        area_len = len(_area(0, **area_kw))
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals\\globals", 0x40)]
        out.append(("bitm", "scenarios\\test\\lightmap", 0x10))
        scnr = bytearray(scnr_meta(meta_address(out, 2), [
            (end, area_len, BASE, tag_datum(4)),
            (0x7FFFFFF0, 0x10, BASE, NONE),  # malformed: area past the end of the file
        ]))  # fmt: skip
        struct.pack_into("<I", scnr, 0x218 + 0x14, tag_datum(3))
        out.append(("scnr", "scenarios\\test\\level", bytes(scnr)))
        out.append(("sbsp", "scenarios\\test\\level_bsp", 0))
        out.append(("ltmp", "scenarios\\test\\level_lm", 0))
        return out

    end = len(make_map(tags(0), string_ids=STRINGS))
    area = _area(end + len(_area(0, **area_kw)), **area_kw)
    tail = area + b"".join(blob for blob, _ in _blobs(area_kw.get("def_lighting", 1)))
    return make_map(tags(end), string_ids=STRINGS, tail=tail)


def _open(data: bytes) -> tuple[io.BytesIO, StringIds, list[BspEntry]]:
    f = io.BytesIO(data)
    h = parse_header(f)
    return f, StringIds(f, h), bsp_entries(f, h, read_tag_index(f, h))


def _no_shared(name: str) -> Any:
    pytest.fail(f"opened {name}")


def test_read_structure_bsp() -> None:
    f, sids, bsps = _open(build())
    assert [(b.index, b.sbsp, b.ltmp) for b in bsps] == [(0, tag_datum(3), tag_datum(4)),
                                                         (1, NONE, NONE)]  # fmt: skip
    level = read_structure_bsp(f, bsps[0], "lvl", sids)
    assert level.materials == (NONE,)
    assert [s.vertex_count for s in level.definitions.sections] == [3, 0]

    def decode(model: Any, i: int) -> Any:
        raw = read_raw(model.sections[i].raw, f, _no_shared)
        return decode_section(model, i, raw, header_size=0x44, level=True)

    cluster = decode(level.clusters, 0)
    assert cluster.positions.tolist() == [list(p) for p in CLUSTER_POS]
    assert [t.tolist() for _, t in cluster.parts] == [[0, 1, 2, 2, 1, 3]]  # unstripped
    assert cluster.lightmap_uv is None and cluster.texcoords is not None
    definition = decode(level.definitions, 0)
    assert [t.tolist() for _, t in definition.parts] == [[0, 1, 2]]  # list: 3 indices = 3 x 1
    assert definition.lightmap_uv is not None
    assert np.allclose(definition.lightmap_uv, np.array(LM_UV) / 32767)
    assert definition.normals is not None and definition.normals[0].tolist() == [0, 0, 1]

    turned = level.instances[1]
    assert (turned.name, turned.scale, turned.lightmapping_policy) == ("crate_turned", 2.0, 1)
    r = np.array(turned.rotation).reshape(3, 3)
    assert r @ np.array([1, 0, 0]) == pytest.approx(TURN[0])  # local +X -> forward
    assert level.instances[2].name == ""  # sid 0 is "crate" with the wrong length: name lost

    group = lightmap_group(f, bsps[0])
    assert group is not None and group.clusters == [(3, 0)] and group.instances[0] == (4, 1)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({}, None),
        ({"definition": 2}, "not in 2"),
        ({"definition": 1}, "is empty"),
        ({"scale": 0.0}, "scale <= 0"),
        ({"position": (float("nan"), 0.0, 0.0)}, "non-finite"),
        ({"rotation": (1.0, 0, 0, 0, 1.0, 0, 0, 0, -1.0)}, "not a rotation"),  # mirror, det -1
        ({"rotation": (2.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0)}, "not a rotation"),  # not orthonormal
    ],
)
def test_instance_problem(change: dict[str, Any], message: str | None) -> None:
    defs: Any = SimpleNamespace(sections=[SimpleNamespace(vertex_count=3)] * 1
                                + [SimpleNamespace(vertex_count=0)])  # fmt: skip
    fields = {"name": "x", "definition": 0, "scale": 1.0, "rotation": tuple(np.eye(3).flat),
              "position": (0.0, 0.0, 0.0), "lightmapping_policy": 0} | change  # fmt: skip
    problem = instance_problem(Instance(**fields), defs)
    assert problem is None if message is None else message in (problem or "")


def test_quat_from_matrix() -> None:
    rng = np.random.default_rng(1)
    qs = [rng.normal(size=4) for _ in range(50)] + [np.array(q, float) for q in
          [(0, 0, 0, 1), (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (1, 1, 0, 0)]]  # fmt: skip
    for q in qs:
        q = q / np.linalg.norm(q)
        m = _quat_matrix(tuple(q))
        assert np.allclose(_quat_matrix(quat_from_matrix(m)), m, atol=1e-9)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"def_lighting": 0}, None),  # a def without lightmap UVs is fine
        ({"cluster_header": 0x70}, "header and data say otherwise"),
    ],
)
def test_section_header(kwargs: dict[str, Any], message: str | None) -> None:
    f, sids, bsps = _open(build(**kwargs))
    level = read_structure_bsp(f, bsps[0], "lvl", sids)
    raw = read_raw(level.clusters.sections[0].raw, f, _no_shared)
    if message is None:
        decode_section(level.clusters, 0, raw, header_size=0x44, level=True)
        return
    with pytest.raises(CacheError, match=message):
        decode_section(level.clusters, 0, raw, header_size=0x44, level=True)


def _patched(offset: int, fmt: str, *values: Any) -> bytes:
    """build() with the area bytes at `offset` (relative to the area) overwritten."""
    data = bytearray(build())
    _, _, bsps = _open(bytes(data))
    struct.pack_into(fmt, data, bsps[0].offset + offset, *values)
    return bytes(data)


@pytest.mark.parametrize(
    ("offset", "fmt", "values", "message"),
    [
        (0xC, "<4s", (b"XXXX",), "lacks"),
        (4, "<I", (BASE + 0x100000,), "outside BSP"),
        (0x10 + 0x9C, "<i", (0x7FFFFFFF,), "absurd"),
        (0x10 + 0x138 + 4, "<I", (BASE - 0x1000,), "outside BSP"),
    ],
)
def test_malformed(offset: int, fmt: str, values: tuple[Any, ...], message: str) -> None:
    f, sids, bsps = _open(_patched(offset, fmt, *values))
    with pytest.raises(CacheError, match=message):
        read_structure_bsp(f, bsps[0], "lvl", sids)


def test_lighting_flags_disagree() -> None:
    data = bytearray(build())
    f, sids, bsps = _open(bytes(data))
    _, address = struct.unpack_from("<iI", data, bsps[0].offset + 0x10 + 0x138)
    struct.pack_into("<H", data, bsps[0].tr(address, 0xC8) + 0x26, 0)  # def says no stream 3
    with pytest.raises(CacheError, match="lighting flags 0x0 disagree with vertex stream 3"):
        read_structure_bsp(io.BytesIO(bytes(data)), bsps[0], "lvl", sids)


def _disc(tmp_path: Path, data: bytes) -> Source:
    tree = {
        "default.xbe": make_xbe(title="Halo 2", title_id=0x4D530064),
        "maps": {"testmap.map": data},
    }
    iso = write_image(tmp_path / "disc.iso", build_partition(tree))
    return Source(iso, "cd" * 32, iso.stat().st_size)


def _world(node: dict[str, Any], p: Any) -> Any:
    """Apply a glTF node's TRS to a point."""
    return np.array(node["translation"]) + _quat_matrix(tuple(node["rotation"])) @ (
        np.array(node["scale"]) * p
    )


def test_extract_levels(tmp_path: Path) -> None:
    source = _disc(tmp_path, build())
    adapter = Halo2Adapter()
    assert not any(i.logical_path.endswith(".glb") for i in adapter.extract(source, ["testmap"]))
    items = list(adapter.extract(source, ["levels"]))
    assert [type(i) for i in items] == [Asset, Skipped]
    asset, skipped = items
    assert isinstance(asset, Asset) and isinstance(skipped, Skipped)
    assert (
        skipped.logical_path == "testmap/testmap_bsp1.glb" and "past end of file" in skipped.reason
    )
    assert asset.logical_path == "testmap/testmap_bsp0.glb"
    meta = asset.metadata
    assert (meta["kind"], meta["clusters"], meta["definitions"], meta["instances"]) == (
        "level", 1, 1, 2)  # fmt: skip
    assert meta["cluster_triangles"] == 2 and meta["instance_triangles"] == 2
    assert meta["warnings"] == ["instance 2 (): definition 1 is empty; not placed"]

    doc, _ = read_glb(asset.data)
    assert doc["asset"]["extras"]["tag"] == "scenarios\\test\\level_bsp"
    assert doc["asset"]["extras"]["lightmap_bitm"] == "scenarios\\test\\lightmap"
    nodes = doc["nodes"]
    root = nodes[0]
    assert root["name"] == "halo_to_gltf" and root["scale"] == [3.048] * 3
    assert [nodes[i]["name"] for i in root["children"]] == ["cluster_0", "crate", "crate_turned"]
    cluster, crate, turned = (nodes[i] for i in root["children"])
    assert cluster["extras"] == {
        "lightmap_bitmap": 3,
        "lightmap_palette": 0,
        "lightmap_uri": "scenarios/test/lightmap_03.png",  # same folder layout as the PNG export
    }
    assert "translation" not in cluster  # world space as stored
    assert crate["mesh"] == turned["mesh"] != cluster["mesh"]  # one mesh, two placements
    assert len(doc["meshes"]) == 2 and doc["materials"][0]["name"] == "material_0"
    assert turned["extras"] == {"instance": 1, "def": 0, "lightmapping_policy": 1}
    assert crate["extras"]["lightmap_uri"] == "scenarios/test/lightmap_04.png"
    # local (1, 0, 0) -> P + s * F = (0, 5, 0) + 2 * (0, 1, 0)
    assert _world(turned, np.array(DEF_POS[0])) == pytest.approx([0, 7, 0])
    assert _world(crate, np.array(DEF_POS[1])) == pytest.approx([10, 2, 0])
    prim = doc["meshes"][crate["mesh"]]["primitives"][0]
    assert "TEXCOORD_1" in prim["attributes"]
    assert "TEXCOORD_1" not in doc["meshes"][cluster["mesh"]]["primitives"][0]["attributes"]
