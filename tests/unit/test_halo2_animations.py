"""Halo 2 animation graphs (jmad) -> glTF animations. Synthetic data built in code only."""

from __future__ import annotations

import io
import math
import struct
from typing import Any

import numpy as np
import pytest
from fixtures.halo2_builder import make_map, meta_address, sid

from rrl.adapters.xbox_halo2.animation import (
    BASE,
    OVERLAY,
    REPLACEMENT,
    Animation,
    AnimationGraph,
    Clip,
    add_animations,
    decode_clip,
    exportable,
    read_animation_graph,
)
from rrl.adapters.xbox_halo2.cachefile import CacheError, StringIds, parse_header, read_tag_index
from rrl.processors.gltf import Gltf, GltfError, read_glb

NODES = [("pelvis", -1), ("spine", 0), ("head", 1)]
GRAPH = AnimationGraph("test\\graph", tuple(NODES), (), ())
TURN = (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5))  # 90 degrees about z
IDENTITY = (0.0, 0.0, 0.0, 1.0)


def q16(q: tuple[float, ...]) -> bytes:
    return struct.pack("<4h", *(round(c * 32767) for c in q))


def flags(rot: int, trans: int, scale: int, nodes: int = len(NODES)) -> bytes:
    words = math.ceil(nodes / 32)
    return b"".join(
        struct.pack(f"<{words}I", *([m] + [0] * (words - 1))) for m in (rot, trans, scale)
    )


def full_frame(codec: int, rots: list[list[Any]], trans: list[list[Any]], n: int) -> bytes:
    """Codec 1/3 stream: per-track rows of n quaternions / translations, node-major."""
    rot = b"".join(q16(q) for track in rots for q in track)
    tr = b"".join(struct.pack("<3f", *t) for track in trans for t in track)
    trans_off = 0x20 + len(rot)
    head = struct.pack(
        "<4BffIIIII", codec, len(rots), len(trans), 0, 0.0, 1.0, trans_off,
        trans_off + len(tr), 8 * n if codec != 1 else 0, 12 * n if codec != 1 else 0, 0,
    )  # fmt: skip
    return head + rot + tr


def keyframe(
    codec: int, rots: list[tuple[list[int], list[Any]]], trans: list[tuple[list[int], list[Any]]]
) -> bytes:
    """Codec 4-7 stream. Codecs 6/7 lay each table out in reverse track order."""
    tsize = 1 if codec in (4, 6) else 2
    order = list(range(len(rots))), list(range(len(trans)))
    if codec in (6, 7):
        order = order[0][::-1], order[1][::-1]
    th = 0x30 + 4 * len(rots)
    sh = th + 4 * len(trans)
    times_at = sh  # no scale tracks
    tables = []  # (words, times, values) per component
    for tracks, idx, pack in (
        (rots, order[0], q16),
        (trans, order[1], lambda t: struct.pack("<3f", *t)),
    ):
        first, firsts = 0, {}
        for i in idx:
            firsts[i] = first
            first += len(tracks[i][0])
        times = b"".join(
            struct.pack(f"<{len(tracks[i][0])}{'B' if tsize == 1 else 'H'}", *tracks[i][0])
            for i in idx
        )
        values = b"".join(pack(v) for i in idx for v in tracks[i][1])
        words = [len(t[0]) | firsts[i] << 12 for i, t in enumerate(tracks)]
        tables.append((words, times, values))
    rot_t = times_at
    tr_t = rot_t + len(tables[0][1])
    sc_t = tr_t + len(tables[1][1])
    rot_v = sc_t
    tr_v = rot_v + len(tables[0][2])
    sc_v = tr_v + len(tables[1][2])
    head = struct.pack(
        "<4Bff9I", codec, len(rots), len(trans), 0, 0.0, 1.0, th, sh, rot_t, tr_t, sc_t, rot_v,
        tr_v, sc_v, 0,
    )  # fmt: skip
    words = struct.pack(f"<{len(rots)}I", *tables[0][0]) + struct.pack(
        f"<{len(trans)}I", *tables[1][0]
    )
    return head + words + tables[0][1] + tables[1][1] + tables[0][2] + tables[1][2]


def animation(
    static: bytes, animated: bytes, sflags: bytes, aflags: bytes, frames: int, **kw: Any
) -> tuple[Animation, bytes]:
    movement = kw.pop("movement", b"")
    offset = 16
    blob = static + animated + sflags + aflags + movement
    fields: dict[str, Any] = {
        "name": "walk", "kind": BASE, "frame_info": 0, "node_count": len(NODES), "frames": frames,
        "loop_frame": 0, "group": 0, "offset": offset, "static_flags": len(sflags),
        "animated_flags": len(aflags), "movement": len(movement), "extra": 0,
        "static": len(static), "animated": len(animated),
    } | kw  # fmt: skip
    return Animation(**fields), b"\0" * offset + blob + b"\0" * 8


def base_walk(**kw: Any) -> tuple[Animation, bytes]:
    static = full_frame(1, [[IDENTITY]], [[(0.0, 0.0, 1.0)]], 1)  # pelvis rot + trans, held
    animated = full_frame(3, [[IDENTITY, TURN, IDENTITY, TURN]], [], 4)  # spine rot, 4 frames
    return animation(static, animated, flags(0b001, 0b001, 0), flags(0b010, 0, 0), 4, **kw)


def test_full_frame_base() -> None:
    anim, group = base_walk()
    clip = decode_clip(GRAPH, anim, group)
    assert clip.codecs == (1, 3)
    assert sorted(clip.rotation) == [0, 1] and sorted(clip.translation) == [0]
    assert clip.translation[0].values.tolist() == [[0.0, 0.0, 1.0]]
    spine = clip.rotation[1]
    assert spine.frames.tolist() == [0, 1, 2, 3]
    assert np.allclose(spine.values[1], TURN, atol=1e-4)


@pytest.mark.parametrize("codec", [4, 5, 6, 7])
def test_keyframe_codecs(codec: int) -> None:
    rots = [([0, 3], [IDENTITY, TURN]), ([0, 1, 3], [IDENTITY, TURN, IDENTITY])]  # pelvis, spine
    trans = [([0, 3], [(0, 0, 0), (1, 2, 3)])]  # head
    animated = keyframe(codec, rots, trans)
    anim, group = animation(b"", animated, b"", flags(0b011, 0b100, 0), 4, kind=REPLACEMENT)
    clip = decode_clip(GRAPH, anim, group)
    assert clip.rotation[1].frames.tolist() == [0, 1, 3]
    assert np.allclose(clip.rotation[0].values[1], TURN, atol=1e-4)
    assert clip.translation[2].values.tolist() == [[0, 0, 0], [1, 2, 3]]


def test_movement() -> None:
    deltas = np.array([[0.1, 0, 0.5], [0.2, 0, -0.5], [0, 0, 0], [0, 0, 0]], np.float32)
    anim, group = base_walk(frame_info=2, movement=deltas.tobytes())
    clip = decode_clip(GRAPH, anim, group)
    assert clip.movement is not None and clip.movement.tolist() == deltas.tolist()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"offset": 4096}, "past its group"),
        ({"node_count": 2}, "the graph has 3"),
        ({"movement": 4}, "movement is 4 bytes"),
        ({"animated_flags": 6}, "node flags"),
    ],
)
def test_malformed_animation(change: dict[str, Any], message: str) -> None:
    anim, group = base_walk()
    with pytest.raises(CacheError, match=message):
        decode_clip(GRAPH, Animation(**{**anim.__dict__, **change}), group)


def test_malformed_streams() -> None:
    static = full_frame(1, [[IDENTITY]], [[(0.0, 0.0, 1.0)]], 1)
    animated = full_frame(3, [[IDENTITY] * 4], [], 4)
    cases = [
        (flags(0b001, 0b001, 0), flags(0b110, 0, 0), "1 tracks for 2 flagged"),
        (flags(0b001, 0b001, 0), flags(0b001, 0, 0), "both static and animated"),
    ]
    for sflags, aflags, message in cases:
        anim, group = animation(static, animated, sflags, aflags, 4)
        with pytest.raises(CacheError, match=message):
            decode_clip(GRAPH, anim, group)
    bad_q = full_frame(3, [[(0.5, 0, 0, 0.5)] * 4], [], 4)  # length 0.71
    anim, group = animation(static, bad_q, flags(1, 1, 0), flags(0b010, 0, 0), 4)
    with pytest.raises(CacheError, match="quaternion of length"):
        decode_clip(GRAPH, anim, group)
    late = keyframe(4, [([0, 2], [IDENTITY, TURN])], [])  # last key 2, clip has 4 frames
    anim, group = animation(b"", late, b"", flags(0b010, 0, 0), 4, kind=REPLACEMENT)
    with pytest.raises(CacheError, match="key times 0..2 for 4 frames"):
        decode_clip(GRAPH, anim, group)
    shifted = bytearray(animated)
    struct.pack_into("<I", shifted, 0xC, 0x24)
    anim, group = animation(static, bytes(shifted), flags(1, 1, 0), flags(0b010, 0, 0), 4)
    with pytest.raises(CacheError, match="header offsets"):
        decode_clip(GRAPH, anim, group)


def test_exportable() -> None:
    anim, _ = base_walk()
    assert exportable(anim) is None
    assert exportable(Animation(**{**anim.__dict__, "kind": OVERLAY})) is not None
    assert exportable(Animation(**{**anim.__dict__, "group": -1})) == "no data"


def _skeleton(names: list[tuple[str, str | None]]) -> tuple[Gltf, dict[str, int]]:
    g = Gltf()
    joints: dict[str, int] = {}
    for name, parent in names:
        joints[name] = g.node(name, parent=joints[parent] if parent else None)
    return g, joints


def test_add_animations() -> None:
    model = [("pelvis", None), ("spine", "pelvis"), ("head", "spine")]
    g, joints = _skeleton(model)
    rest = {n: ((0.0, 0.0, 0.0), IDENTITY) for n, _ in model}
    parents = dict(model)
    base, group = base_walk()
    replacement = Animation(**{**base.__dict__, "name": "reload", "kind": REPLACEMENT})
    blend = Clip((0, 8))
    clips = [
        (base, decode_clip(GRAPH, base, group)),
        (replacement, decode_clip(GRAPH, base, group)),
    ]
    clips.append((Animation(**{**base.__dict__, "name": "aim"}), blend))
    warnings = add_animations(g, GRAPH, clips, joints, rest, parents)
    assert warnings == ["aim: blend screen (pose grid, not a timeline); skipped"]
    doc, _ = read_glb(g.glb())
    walk, reload = doc["animations"]
    targets = {(c["target"]["node"], c["target"]["path"]) for c in walk["channels"]}
    # base: every mapped node gets rotation + translation (rest when unflagged), no scale track
    assert targets == {(joints[n], p) for n, _ in model for p in ("rotation", "translation")}
    replaced = {(c["target"]["node"], c["target"]["path"]) for c in reload["channels"]}
    assert replaced == {(0, "rotation"), (0, "translation"), (1, "rotation")}  # flagged only
    spine = next(c for c in walk["channels"] if c["target"] == {"node": 1, "path": "rotation"})
    times = doc["accessors"][walk["samplers"][spine["sampler"]]["input"]]
    assert times["count"] == 4 and times["max"] == [pytest.approx(3 / 30)]
    assert walk["extras"]["type"] == "base" and reload["extras"]["type"] == "replacement"


def test_node_mapping_by_name() -> None:
    g, joints = _skeleton([("pelvis", None), ("head", "pelvis")])  # no spine; head re-parented
    rest = {"pelvis": ((0.0, 0.0, 0.0), IDENTITY), "head": ((0.0, 0.0, 0.0), IDENTITY)}
    base, group = base_walk()
    warnings = add_animations(
        g, GRAPH, [(base, decode_clip(GRAPH, base, group))], joints, rest,
        {"pelvis": None, "head": "pelvis"},
    )  # fmt: skip
    assert warnings == [
        "jmad node spine not in the model; its tracks are dropped",
        "jmad node head has another parent in the model; dropped",
    ]
    doc, _ = read_glb(g.glb())
    assert {c["target"]["node"] for c in doc["animations"][0]["channels"]} == {0}


def test_gltf_animation_checks() -> None:
    g, _ = _skeleton([("a", None)])
    one = np.array([0.0, 0.0], np.float32)
    with pytest.raises(GltfError, match="not increasing"):
        g.animation("x", [(0, "translation", one, np.zeros((2, 3), np.float32))])
    with pytest.raises(GltfError, match="bad target"):
        g.animation("x", [(5, "rotation", one + [0, 1], np.zeros((2, 4), np.float32))])


def test_read_animation_graph() -> None:
    strings = ["graph", "pelvis", "spine", "walk"]
    address = meta_address([("matg", "globals\\globals", 0x40)], 1)
    head = bytearray(0xBC)
    buf = bytearray()

    def block(pos: int, elements: list[bytes]) -> None:
        struct.pack_into("<iI", head, pos, len(elements), address + 0xBC + len(buf))
        buf.extend(b"".join(elements))

    nodes = [
        struct.pack("<Ihhh", sid(1, "pelvis"), -1, 1, -1),
        struct.pack("<Ihhh", sid(2, "spine"), -1, -1, 0),
    ]
    block(0x0C, [n.ljust(0x20, b"\0") for n in nodes])
    a = bytearray(0x6C)
    struct.pack_into("<I", a, 0, sid(3, "walk"))
    struct.pack_into("<BBbBh", a, 0x10, BASE, 0, -1, 2, 10)
    struct.pack_into("<hhi", a, 0x24, 0, 0, 0x40)
    struct.pack_into("<BBhhhii", a, 0x3C, 12, 12, 0, 0, 48, 0, 96)
    block(0x2C, [bytes(a)])
    block(0xAC, [struct.pack("<IiI", 0, 0x200, 2 << 30 | 0x1000).ljust(0x14, b"\0")])
    tags = [("matg", "globals\\globals", 0x40), ("jmad", "test\\graph", bytes(head + buf))]
    data = make_map(tags, string_ids=strings)
    f = io.BytesIO(data)
    h = parse_header(f)
    tags = read_tag_index(f, h)
    graph = read_animation_graph(f, tags[1], StringIds(f, h))
    assert graph.nodes == (("pelvis", -1), ("spine", 0))
    (walk,) = graph.animations
    assert (walk.name, walk.frames, walk.offset, walk.static, walk.animated) == (
        "walk",
        10,
        0x40,
        48,
        96,
    )
    assert (
        graph.groups[0].file == 2
        and graph.groups[0].offset == 0x1000
        and graph.groups[0].size == 0x200
    )
