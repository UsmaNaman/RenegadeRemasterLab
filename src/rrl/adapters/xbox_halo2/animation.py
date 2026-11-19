"""Halo 2 (original Xbox, retail) animation graphs (`jmad`) and their codecs -> glTF animations.

Layout facts and sources: documentation/platforms/halo2_xbox.md ("Animations"). Facts come from
Assembly's Halo2Xbox jmad plugin (GPL-3.0), coffeecutie's dimeter headers (MIT) and checks on the
owner's disc; the code is written from those facts, nothing is copied.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, BinaryIO

import numpy as np
from numpy.typing import NDArray

from rrl.adapters.xbox_halo2.bitmaps import RawLocation
from rrl.adapters.xbox_halo2.cachefile import CacheError, StringIds, TagEntry
from rrl.adapters.xbox_halo2.render_model import _Meta
from rrl.processors.gltf import Gltf

JMAD_SIZE = 0xBC
NODE_SIZE = 0x20
ANIMATION_SIZE = 0x6C
GROUP_SIZE = 0x14
NODES, ANIMATIONS, GROUPS = 0x0C, 0x2C, 0xAC
BASE, OVERLAY, REPLACEMENT = 0, 1, 2
MOVEMENT_BYTES = {0: 0, 1: 8, 2: 12, 3: 16}  # per frame: dx,dy / +dyaw / dx,dy,dz,dyaw (f32)
FULL_FRAME = {1: 8, 2: 16, 3: 8, 8: 16}  # codec -> rotation element size (i16x4 or f32x4)
KEYFRAME = {4: 1, 5: 2, 6: 1, 7: 2}  # codec -> key time size (u8 or u16)
BLEND_SCREEN = 8
FPS = 30.0  # no rate field in the tag; x01 cinematic foley lengths give 29.6-29.75 (halo2_xbox.md)
Q16 = 32767.0


@dataclass(frozen=True)
class Animation:
    name: str
    kind: int  # BASE, OVERLAY or REPLACEMENT
    frame_info: int  # 0 none, 1 dx dy, 2 dx dy dyaw, 3 dx dy dz dyaw
    node_count: int
    frames: int
    loop_frame: int
    group: int  # resource group index, -1 = no data
    offset: int  # inside the group
    static_flags: int  # byte sizes, in blob order after the two codec streams
    animated_flags: int
    movement: int
    extra: int
    static: int  # static codec stream bytes
    animated: int  # animated codec stream bytes

    @property
    def total(self) -> int:
        return (
            self.static
            + self.animated
            + self.static_flags
            + self.animated_flags
            + self.movement
            + self.extra
        )


@dataclass(frozen=True)
class AnimationGraph:
    name: str
    nodes: tuple[tuple[str, int], ...]  # (name, parent index)
    animations: tuple[Animation, ...]
    groups: tuple[RawLocation, ...]


@dataclass
class Track:
    frames: NDArray[np.float64]  # key frame numbers, strictly increasing
    values: NDArray[np.float64]  # (keys, 4) quaternion x,y,z,w / (keys, 3) / (keys, 1)


@dataclass
class Clip:
    codecs: tuple[int, int]  # (static, animated)
    rotation: dict[int, Track] = field(default_factory=dict)  # by jmad node index
    translation: dict[int, Track] = field(default_factory=dict)
    scale: dict[int, Track] = field(default_factory=dict)
    movement: NDArray[np.float32] | None = None  # (frames, 2/3/4)


def read_animation_graph(f: BinaryIO, tag: TagEntry, sid: StringIds) -> AnimationGraph:
    m = _Meta(f, tag, JMAD_SIZE)
    nodes = tuple(
        (sid(struct.unpack_from("<I", n)[0]), struct.unpack_from("<h", n, 8)[0])
        for n in m.block(m.raw, NODES, NODE_SIZE, "skeleton nodes")
    )
    for i, (_, parent) in enumerate(nodes):
        if not -1 <= parent < len(nodes) or parent == i:
            raise CacheError(f"{m.name}: node {i} parent {parent} out of range")
    groups = []
    for g in m.block(m.raw, GROUPS, GROUP_SIZE, "resource groups"):
        size, ptr = struct.unpack_from("<iI", g, 4)
        if size < 0:
            raise CacheError(f"{m.name}: resource group size {size}")
        groups.append(RawLocation(ptr >> 30, ptr & 0x3FFFFFFF, size))
    animations = []
    for a in m.block(m.raw, ANIMATIONS, ANIMATION_SIZE, "animations"):
        kind, frame_info, _, node_count, frames = struct.unpack_from("<BBbBh", a, 0x10)
        group, _, offset = struct.unpack_from("<hhi", a, 0x24)
        loop_frame = struct.unpack_from("<h", a, 0x2E)[0]
        sflag, aflag, movement, extra, static, _, animated = struct.unpack_from("<BBhhhii", a, 0x3C)
        animations.append(
            Animation(
                sid(struct.unpack_from("<I", a)[0]),
                kind,
                frame_info,
                node_count,
                frames,
                loop_frame,
                group,
                offset,
                sflag,
                aflag,
                movement,
                extra,
                static,
                animated,
            )
        )
    return AnimationGraph(m.name, nodes, tuple(animations), tuple(groups))


def decode_clip(graph: AnimationGraph, anim: Animation, group: bytes) -> Clip:
    """Decode one animation from its resource group's bytes. Raises CacheError on bad data."""
    what = f"{graph.name} {anim.name}"
    nodes = len(graph.nodes)
    if anim.node_count != nodes:
        raise CacheError(f"{what}: {anim.node_count} nodes, the graph has {nodes}")
    if anim.frames < 1:
        raise CacheError(f"{what}: {anim.frames} frames")
    sizes = (anim.static, anim.animated, anim.static_flags, anim.animated_flags, anim.movement)
    if min(*sizes, anim.extra) < 0:
        raise CacheError(f"{what}: negative data size")
    if anim.offset < 0 or anim.offset + anim.total > len(group):
        raise CacheError(f"{what}: data {anim.offset:#x}+{anim.total:#x} past its group")
    if anim.frame_info not in MOVEMENT_BYTES:
        raise CacheError(f"{what}: frame info type {anim.frame_info}")
    if anim.movement != anim.frames * MOVEMENT_BYTES[anim.frame_info]:
        raise CacheError(f"{what}: movement is {anim.movement} bytes for {anim.frames} frames")
    words = math.ceil(nodes / 32)
    if anim.static_flags not in (0, 12 * words) or anim.animated_flags != 12 * words:
        raise CacheError(f"{what}: node flags of {anim.static_flags}/{anim.animated_flags} bytes")
    blob = group[anim.offset : anim.offset + anim.total]
    at = 0

    def take(n: int) -> bytes:
        nonlocal at
        at += n
        return blob[at - n : at]

    static_stream, animated_stream = take(anim.static), take(anim.animated)
    static_bits = _flags(take(anim.static_flags), words, nodes) if anim.static_flags else None
    animated_bits = _flags(take(anim.animated_flags), words, nodes)
    movement = take(anim.movement)
    clip = Clip(
        (static_stream[0] if static_stream else 0, animated_stream[0] if animated_stream else 0)
    )
    streams = [(animated_stream, animated_bits, anim.frames)]
    if static_bits is not None:
        for c in range(3):
            if static_bits[c] & animated_bits[c]:
                raise CacheError(f"{what}: node flagged both static and animated")
        streams.insert(0, (static_stream, static_bits, 1))
    for stream, bits, frames in streams:
        tracks = _codec(stream, frames, f"{what} codec {stream[:1].hex()}")
        for c, (out, flagged) in enumerate(
            zip((clip.rotation, clip.translation, clip.scale), bits, strict=True)
        ):
            owners = [i for i in range(nodes) if flagged >> i & 1]
            if len(owners) != len(tracks[c]):
                raise CacheError(f"{what}: {len(tracks[c])} tracks for {len(owners)} flagged nodes")
            out.update(zip(owners, tracks[c], strict=True))
    if anim.frame_info:
        width = MOVEMENT_BYTES[anim.frame_info] // 4
        clip.movement = np.frombuffer(movement, "<f4").reshape(anim.frames, width).copy()
    for track in [*clip.rotation.values(), *clip.translation.values(), *clip.scale.values()]:
        if not np.isfinite(track.values).all():
            raise CacheError(f"{what}: non-finite key values")
    for track in clip.scale.values():
        if (track.values < 0).any():  # 0 is legal: the game hides nodes that way (e.g. bugger)
            raise CacheError(f"{what}: negative scale")
    return clip


def _flags(raw: bytes, words: int, nodes: int) -> tuple[int, int, int]:
    """Three bit arrays (rotation, translation, scale) of u32 words, LSB first -> node bitmasks."""
    out = []
    for c in range(3):
        value = 0
        for w, word in enumerate(struct.unpack_from(f"<{words}I", raw, 12 * words // 3 * c)):
            value |= word << (32 * w)
        if value >> nodes:
            raise CacheError(f"node flags name node {value.bit_length() - 1} of {nodes}")
        out.append(value)
    return out[0], out[1], out[2]


def _codec(stream: bytes, frames: int, what: str) -> tuple[list[Track], list[Track], list[Track]]:
    """One codec stream -> (rotation, translation, scale) tracks in flag order."""
    codec = stream[0] if stream else 0
    if codec == 0:  # no tracks
        return [], [], []
    if len(stream) < 0x20:
        raise CacheError(f"{what}: {len(stream)}-byte stream")
    counts = stream[1:4]
    if codec in FULL_FRAME:
        return _full_frame(stream, codec, counts, frames, what)
    if codec in KEYFRAME:
        return _keyframe(stream, codec, counts, frames, what)
    raise CacheError(f"{what}: unknown codec {codec}")


def _full_frame(
    s: bytes, codec: int, counts: bytes, frames: int, what: str
) -> tuple[list[Track], list[Track], list[Track]]:
    n = 1 if codec == 1 else frames  # codec 1 is the static stream: one value per track
    esz = FULL_FRAME[codec]
    trans_off, scale_off = struct.unpack_from("<II", s, 0xC)
    nr, nt, ns = counts
    if trans_off != 0x20 + nr * n * esz or scale_off != trans_off + nt * n * 12:
        raise CacheError(f"{what}: header offsets disagree with the track counts")
    if scale_off + ns * n * 4 > len(s):
        raise CacheError(f"{what}: tracks run past the stream")
    times = np.arange(n, dtype=np.float64)
    q = (
        np.frombuffer(s, "<i2", nr * n * 4, 0x20).astype(np.float64) / Q16
        if esz == 8
        else np.frombuffer(s, "<f4", nr * n * 4, 0x20).astype(np.float64)
    ).reshape(nr, n, 4)
    t = np.frombuffer(s, "<f4", nt * n * 3, trans_off).astype(np.float64).reshape(nt, n, 3)
    sc = np.frombuffer(s, "<f4", ns * n, scale_off).astype(np.float64).reshape(ns, n, 1)
    return (
        [Track(times, _unit(q[i], what)) for i in range(nr)],
        [Track(times, t[i]) for i in range(nt)],
        [Track(times, sc[i]) for i in range(ns)],
    )


def _keyframe(
    s: bytes, codec: int, counts: bytes, frames: int, what: str
) -> tuple[list[Track], list[Track], list[Track]]:
    if len(s) < 0x30:
        raise CacheError(f"{what}: {len(s)}-byte keyframe stream")
    nr, nt, ns = counts
    th, sh, *offsets = struct.unpack_from("<9I", s, 0xC)
    times_at, values_at, zero = offsets[:3], offsets[3:6], offsets[6]
    if th != 0x30 + 4 * nr or sh != th + 4 * nt or times_at[0] != sh + 4 * ns or zero:
        raise CacheError(f"{what}: keyframe header disagrees with the track counts")
    tsize = KEYFRAME[codec]
    out: list[list[Track]] = []
    for c, (count, words_at, width, stride) in enumerate(
        zip((nr, nt, ns), (0x30, th, sh), (4, 3, 1), (8, 12, 4), strict=True)
    ):
        tracks = []
        for w in struct.unpack_from(f"<{count}I", s, words_at):
            keys, first = w & 0xFFF, w >> 12
            t_at, v_at = times_at[c] + first * tsize, values_at[c] + first * stride
            if keys < 1 or max(t_at + keys * tsize, v_at + keys * stride) > len(s):
                raise CacheError(f"{what}: track of {keys} keys runs past the stream")
            k = np.frombuffer(s, "<u1" if tsize == 1 else "<u2", keys, t_at).astype(np.float64)
            if k[0] != 0 or k[-1] != frames - 1 or (np.diff(k) <= 0).any():
                raise CacheError(f"{what}: key times {k[0]:.0f}..{k[-1]:.0f} for {frames} frames")
            if c == 0:
                v = np.frombuffer(s, "<i2", keys * 4, v_at).astype(np.float64) / Q16
                v = _unit(v.reshape(keys, 4), what)
            else:
                v = np.frombuffer(s, "<f4", keys * width, v_at).astype(np.float64)
            tracks.append(Track(k, v.reshape(keys, width)))
        out.append(tracks)
    return out[0], out[1], out[2]


def _unit(q: NDArray[np.float64], what: str) -> NDArray[np.float64]:
    """Normalised quaternions; dequantised values must already be near unit length."""
    norm = np.linalg.norm(q, axis=1)
    if not ((norm > 0.98) & (norm < 1.02)).all():
        raise CacheError(f"{what}: quaternion of length {float(norm.min()):.3f}")
    return q / norm[:, None]


def exportable(anim: Animation) -> str | None:
    """Why an animation is not exported as a clip, or None."""
    if anim.group < 0:
        return "no data"
    if anim.kind == OVERLAY:
        return "overlay (deltas; glTF has no additive layers)"
    if anim.kind not in (BASE, REPLACEMENT):
        return f"animation type {anim.kind}"
    return None


def add_animations(
    g: Gltf,
    graph: AnimationGraph,
    clips: list[tuple[Animation, Clip]],
    joints: dict[str, int],
    rest: Mapping[str, tuple[tuple[float, ...], tuple[float, ...]]],
    parents: Mapping[str, str | None],
) -> list[str]:
    """Add one glTF animation per clip, mapping jmad nodes onto `joints` by name.

    `rest` and `parents` describe the target skeleton (local translation, rotation; parent name).
    Base clips also key unflagged mapped nodes at rest so each clip is self-contained.
    """
    warnings: list[str] = []
    mapped: dict[int, int] = {}
    for i, (name, parent) in enumerate(graph.nodes):
        if name not in joints:
            warnings.append(f"jmad node {name} not in the model; its tracks are dropped")
        elif parents.get(name) != (graph.nodes[parent][0] if parent >= 0 else None):
            warnings.append(f"jmad node {name} has another parent in the model; dropped")
        else:
            mapped[i] = joints[name]
    for anim, clip in clips:
        if clip.codecs[1] == BLEND_SCREEN:
            warnings.append(f"{anim.name}: blend screen (pose grid, not a timeline); skipped")
            continue
        end = max(anim.frames - 1, 1)
        channels: list[tuple[int, str, NDArray[np.float32], NDArray[np.float32]]] = []
        for i, node in mapped.items():
            name = graph.nodes[i][0]
            for path, tracks, fallback in (
                ("rotation", clip.rotation, rest[name][1]),
                ("translation", clip.translation, rest[name][0]),
                ("scale", clip.scale, None),
            ):
                track = tracks.get(i)
                if track is None and (anim.kind != BASE or fallback is None):
                    continue
                if track is None:
                    frames, values = np.array([0.0, end]), np.array([fallback, fallback])
                elif len(track.frames) == 1:  # static stream: hold the value for the clip
                    frames, values = np.array([0.0, end]), np.repeat(track.values, 2, axis=0)
                else:
                    frames, values = track.frames, track.values
                if path == "rotation":
                    values = _continuous(values)
                elif path == "scale":
                    values = np.repeat(values.reshape(-1, 1), 3, axis=1)
                channels.append(
                    (node, path, (frames / FPS).astype(np.float32), values.astype(np.float32))
                )
        if not channels:
            warnings.append(f"{anim.name}: no tracks on the model's nodes; skipped")
            continue
        extras: dict[str, Any] = {
            "type": ("base", "overlay", "replacement")[anim.kind],
            "frames": anim.frames,
            "loop_frame": anim.loop_frame,
            "codecs": list(clip.codecs),
            "fps": FPS,
        }
        if clip.movement is not None:
            # Root motion stays out of the pose (the game moves the object, not the pelvis).
            extras["root_motion"] = {
                "frame_info": anim.frame_info,
                "deltas": np.round(clip.movement, 6).tolist(),
            }
        g.animation(anim.name, channels, extras)
    return warnings


def _continuous(q: NDArray[np.float64]) -> NDArray[np.float64]:
    """Flip quaternion signs so neighbours share a hemisphere (q and -q are the same rotation)."""
    out = q.copy()
    for k in range(1, len(out)):
        if float(np.dot(out[k], out[k - 1])) < 0:
            out[k] = -out[k]
    return out


def clip_reader(
    graph: AnimationGraph, read: Callable[[RawLocation], bytes]
) -> Callable[[Animation], Clip]:
    """decode_clip with each resource group read once."""
    cache: dict[int, bytes] = {}

    def clip(anim: Animation) -> Clip:
        if not 0 <= anim.group < len(graph.groups):
            raise CacheError(f"{graph.name} {anim.name}: resource group {anim.group} missing")
        if anim.group not in cache:
            cache[anim.group] = read(graph.groups[anim.group])
        return decode_clip(graph, anim, cache[anim.group])

    return clip
