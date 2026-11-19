"""Halo 2 (original Xbox, retail): identify the disc and inspect its cache files (.map)."""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import struct
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, BinaryIO, ClassVar

from rrl.adapters.xbox_halo2.animation import (
    add_animations,
    clip_reader,
    exportable,
    read_animation_graph,
)
from rrl.adapters.xbox_halo2.bitmaps import Bitmap, BitmapTag, read_bitmap_tag, read_raw
from rrl.adapters.xbox_halo2.bump import find_bump_palette
from rrl.adapters.xbox_halo2.cachefile import (
    CacheError,
    CacheHeader,
    StringIds,
    TagEntry,
    _read_at,
    compute_signature,
    parse_header,
    read_tag_index,
)
from rrl.adapters.xbox_halo2.ltmp import Palettes, bsp_entries, read_lightmap_palettes
from rrl.adapters.xbox_halo2.render_model import (
    ModelPart,
    RenderModel,
    SectionMesh,
    ShaderInfo,
    _Meta,
    decode_section,
    models_to_glb,
    read_render_model,
    read_shader,
    relative_uri,
    sections_at,
    tag_by_datum,
)
from rrl.adapters.xbox_halo2.structure_bsp import RAW_HEADER_SIZE as BSP_RAW_HEADER_SIZE
from rrl.adapters.xbox_halo2.structure_bsp import (
    instance_problem,
    lightmap_group,
    read_structure_bsp,
)
from rrl.adapters.xbox_halo2.structure_bsp import to_glb as level_to_glb
from rrl.adapters.xbox_original import XboxOriginalAdapter
from rrl.adapters.xbox_original.xbe import Certificate
from rrl.api import Asset, Entry, Match, SelectionError, Skipped, Source
from rrl.containers.xdvdfs import XdvdfsImage
from rrl.emulators.xemu_textures import PACK_FORMATS, candidate_hashes
from rrl.processors.gltf import Gltf
from rrl.processors.textures import FORMATS, bytes_needed, decode, decode_volume, to_png

HALO2_TITLE_ID = 0x4D530064
HALO2_TITLE = "Halo 2"
VERIFY_LIMIT = 64 * 1024 * 1024  # full verification of all maps reads ~4.7 GB
MAX_DIM = 4096  # retail maps top out at 2048
MAX_MIPS = 12
LINEAR_PITCH = 64  # NV2A linear textures pad each row to 64 bytes
KINDS = frozenset({"textures", "models", "levels", "animations"})


class Halo2Adapter(XboxOriginalAdapter):
    id: ClassVar[str] = "xbox.halo2"
    name: ClassVar[str] = "Halo 2 (Xbox)"
    version: ClassVar[str] = "0.2.0"
    capabilities: ClassVar[frozenset[str]] = frozenset({"identify", "inspect", "extract"})

    def identify(self, source: Source) -> Match | None:
        with source.open() as f:
            probed = self._probe(f)
        if probed is None:
            return None
        image, cert = probed
        if not _is_halo2(cert):
            return None
        match = self._match(image, cert)
        return dataclasses.replace(match, platform=XboxOriginalAdapter.name, confidence=0.95)

    def inspect(self, source: Source, verify_all: bool = False) -> dict[str, Any]:
        with source.open() as f:
            probed = self._probe(f)
            if probed is None or not _is_halo2(probed[1]):
                raise ValueError(f"{source.path.name} is not a Halo 2 (Xbox) disc image")
            image, cert = probed
            maps = [
                _inspect_map(image, e.path, e.size, verify_all or e.size <= VERIFY_LIMIT)
                for e in _map_entries(image)
            ]
            match = self._match(image, cert)
        return {
            "platform": XboxOriginalAdapter.name,
            "game": HALO2_TITLE,
            "title_id": match.title_id,
            "region": match.region,
            **match.details,
            "map_count": len(maps),
            "maps": maps,
        }

    def extract(
        self, source: Source, selection: Sequence[str] | None = None
    ) -> Iterator[Asset | Skipped]:
        """Every bitmap as PNG and, on request, render models and level BSPs as glTF (.glb).

        `selection` is map names (e.g. ["ascension"]) plus optionally the kinds "textures",
        "models" and "levels"; no map names = all maps, no kind = textures only (the original
        behaviour).
        Textures stored in shared maps are exported again under every map that references them.
        """
        kinds = {s.lower() for s in selection or ()} & KINDS or {"textures"}
        selection = [s for s in selection or () if s.lower() not in KINDS] or None
        with source.open() as f:
            probed = self._probe(f)
            if probed is None or not _is_halo2(probed[1]):
                raise ValueError(f"{source.path.name} is not a Halo 2 (Xbox) disc image")
            image = probed[0]
            paths = {e.path.split("/")[-1].lower(): e.path for e in _map_entries(image)}
            names = sorted(n.removesuffix(".map") for n in paths)
            if selection is not None:
                wanted = [s.lower().removesuffix(".map") for s in selection]
                if missing := sorted(set(wanted) - set(names)):
                    raise SelectionError(f"no such map(s): {', '.join(missing)}")
                names = [n for n in names if n in wanted]
            opened: dict[str, BinaryIO] = {}

            @functools.cache
            def bump() -> bytes | str:  # looked up only once a map has P8 bump bitmaps
                try:
                    return find_bump_palette(image)
                except (ValueError, OSError) as exc:  # P8 bump stays skipped; the run goes on
                    return f"no P8 bump palette: {exc}"

            def opener(file: str) -> BinaryIO:
                if file not in opened:
                    if file not in paths:
                        raise CacheError(f"{file} not on disc")
                    opened[file] = image.open(paths[file])
                return opened[file]

            for name in names:
                yield from _extract_map(
                    name, opener(f"{name}.map"), opener, bump, kinds, source.sha256
                )


def _map_entries(image: XdvdfsImage) -> list[Entry]:
    return [
        e
        for e in image.entries()
        if not e.is_dir
        and e.path.count("/") == 1
        and e.path.lower().endswith(".map")
        and e.path.lower().startswith("maps/")
    ]


def _extract_map(
    name: str,
    f: BinaryIO,
    opener: Callable[[str], BinaryIO],
    bump: Callable[[], bytes | str],
    kinds: set[str] | frozenset[str] = frozenset({"textures"}),
    source_sha256: str = "",
) -> Iterator[Asset | Skipped]:
    try:
        header = parse_header(f)
        tags = read_tag_index(f, header)
    except CacheError as exc:
        yield Skipped(f"{name}.map", str(exc))
        return
    if "textures" in kinds:
        yield from _textures(name, f, header, tags, opener, bump)
    if kinds & {"models", "animations"}:  # animations ride on their model's .glb
        animated = "animations" in kinds
        yield from _models(name, f, header, tags, opener, source_sha256, animated)
    if "levels" in kinds:
        yield from _levels(name, f, header, tags, opener, source_sha256)


def _textures(
    name: str,
    f: BinaryIO,
    header: CacheHeader,
    tags: list[TagEntry],
    opener: Callable[[str], BinaryIO],
    bump: Callable[[], bytes | str],
) -> Iterator[Asset | Skipped]:
    lightmaps: Palettes | str
    bsp_errors: list[str] = []
    try:
        lightmaps = read_lightmap_palettes(f, header, tags, bsp_errors)
    except CacheError as exc:  # only this map's P8 lightmaps are lost
        lightmaps = f"no lightmap palettes: {exc}"
    for tag in tags:
        if tag.group != "bitm":
            continue
        base = _texture_path(name, tag)
        try:
            bt = read_bitmap_tag(f, header, tags, tag)
        except CacheError as exc:
            yield Skipped(base, str(exc))
            continue
        for b in bt.bitmaps:
            path = f"{base}_{b.index:02d}"
            palette = bump() if b.format == "p8_bump" else _lightmap(lightmaps, bt, b, bsp_errors)
            if isinstance(palette, str):
                yield Skipped(f"{path}.png", palette)
                continue
            try:
                yield from _convert(path, bt, b, f, opener, palette)
            except (ValueError, OSError) as exc:  # CacheError and TextureError are ValueErrors
                yield Skipped(f"{path}.png", str(exc))


def _texture_path(name: str, tag: TagEntry) -> str:
    """Export path stem of a bitm tag; bitmap n is written as f"{stem}_{n:02d}.png"."""
    return name + "/" + _clean(tag.name or f"bitm_{tag.datum:08x}")


def _page_uri(name: str, glb_path: str, bitm: TagEntry, index: int) -> str:
    """Where the PNG of bitmap `index` of `bitm` lands in an export, relative to the .glb."""
    return relative_uri(glb_path, f"{_texture_path(name, bitm)}_{index:02d}.png")


def _diffuse_uri(name: str, glb_path: str, bitm: TagEntry) -> str:
    """Where the PNG of bitmap 0 of `bitm` lands in an export, relative to the .glb."""
    # shortcut: assumes a 2D diffuse exported in the same folder (cube/3D diffuses or a models-only
    # export leave the link dangling; none dangle on 4 maps checked), upgrade if one turns up.
    return _page_uri(name, glb_path, bitm, 0)


HLMT_MODEL, HLMT_GRAPH = 0x0, 0x10  # hlmt tagrefs (group, datum): render model, animation graph [V]
WEAP_FIRST_PERSON = 0x2A8  # weap block (0x10 each): +0 fp weapon mode ref, +8 fp jmad ref [V]


def _animate(
    parts: Sequence[ModelPart],
    jmad: TagEntry,
    f: BinaryIO,
    opener: Callable[[str], BinaryIO],
    sid: StringIds,
    g: Gltf,
    joints: dict[str, int],
) -> list[str]:
    """Add the jmad's base and replacement clips to the scene's glTF; problems become warnings."""
    try:
        graph = read_animation_graph(f, jmad, sid)
    except CacheError as exc:  # the model still exports, unanimated
        return [f"animations: {exc}"]
    clip = clip_reader(graph, lambda loc: read_raw(loc, f, opener))
    clips, warnings = [], []
    for anim in graph.animations:
        if (why := exportable(anim)) is not None:
            if anim.group >= 0:
                warnings.append(f"{anim.name}: {why}; skipped")
            continue
        try:
            clips.append((anim, clip(anim)))
        except (ValueError, OSError) as exc:
            warnings.append(f"{anim.name}: {exc}; skipped")
    rest: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {}
    parents: dict[str, str | None] = {}
    for part in parts:
        nodes = part.model.nodes
        for n in nodes:
            rest[n.name] = (n.translation, n.rotation)
            parents[n.name] = nodes[n.parent].name if n.parent >= 0 else part.attach.get(n.name)
    return warnings + add_animations(g, graph, clips, joints, rest, parents)


def _refs(f: BinaryIO, tag: TagEntry, size: int, at: tuple[int, ...]) -> list[int]:
    """Datums of the tagrefs at `at` in a tag's meta."""
    if tag.offset is None:
        raise CacheError(f"{tag.group} {tag.name}: no meta")
    raw = _read_at(f, tag.offset, size, f"{tag.group} {tag.name}")
    return [struct.unpack_from("<I", raw, o + 4)[0] for o in at]


def _graphs_by_model(f: BinaryIO, tags: list[TagEntry]) -> dict[str, TagEntry]:
    """Model tag name -> its animation graph: the same-named jmad, else the hlmt's graph."""
    graphs = {t.name: t for t in tags if t.group == "jmad" and t.name}
    out: dict[str, TagEntry] = {}
    for t in tags:
        if t.group != "hlmt":
            continue
        try:
            model, graph = (
                tag_by_datum(tags, d) for d in _refs(f, t, 0x18, (HLMT_MODEL, HLMT_GRAPH))
            )
        except CacheError:
            continue
        if model and model.group == "mode" and model.name and graph and graph.group == "jmad":
            out.setdefault(model.name, graph)
    out.update({t.name: graphs[t.name] for t in tags if t.group == "mode" and t.name in graphs})
    return out


def _first_person(
    f: BinaryIO, header: CacheHeader, tags: list[TagEntry]
) -> list[tuple[TagEntry, TagEntry, TagEntry]]:
    """(arms mode, weapon fp mode, fp jmad) per weapon first-person entry, one per jmad.

    The arms are `objects\\characters\\<char>\\fp\\fp`, beside the graph's folder [V on disc]."""
    modes = {t.name: t for t in tags if t.group == "mode"}
    area = (tags[0].address, header.index_offset + header.index_size, header.meta_data_size)
    out: dict[int, tuple[TagEntry, TagEntry, TagEntry]] = {}
    for weap in (t for t in tags if t.group == "weap"):
        try:
            m = _Meta(f, weap, WEAP_FIRST_PERSON + 8, area)
            entries = m.block(m.raw, WEAP_FIRST_PERSON, 0x10, "first person")
        except CacheError:
            continue
        for e in entries:
            model = tag_by_datum(tags, struct.unpack_from("<I", e, 4)[0])
            graph = tag_by_datum(tags, struct.unpack_from("<I", e, 12)[0])
            if not (model and model.group == "mode" and graph and graph.group == "jmad"):
                continue
            parts = (graph.name or "").split("\\")
            arms = modes.get("\\".join([*parts[:3], "fp", "fp"])) if len(parts) > 3 else None
            if arms is not None and parts[:2] == ["objects", "characters"]:
                out.setdefault(graph.datum, (arms, model, graph))
    return list(out.values())


def _models(
    name: str,
    f: BinaryIO,
    header: CacheHeader,
    tags: list[TagEntry],
    opener: Callable[[str], BinaryIO],
    source_sha256: str,
    animated: bool = False,
) -> Iterator[Asset | Skipped]:
    sid = StringIds(f, header)
    shaders: dict[int, ShaderInfo | str | None] = {}
    graphs = _graphs_by_model(f, tags) if animated else {}

    def part(tag: TagEntry, path: str, warnings: list[str], attach: dict[str, str]) -> ModelPart:
        model = read_render_model(f, tag, sid)
        meshes = {
            s: decode_section(model, s, read_raw(model.sections[s].raw, f, opener))
            for s in sections_at(model)
        }
        infos = _shaders(model.materials, shaders, f, header, tags, warnings)
        uri = functools.partial(_diffuse_uri, name, path)
        return ModelPart(model, meshes, infos, uri, attach)

    def scene(
        path: str, kind: str, tags_used: list[TagEntry], jmad: TagEntry | None
    ) -> Asset | Skipped:
        warnings: list[str] = []
        try:
            parts = [part(tags_used[0], path, warnings, {})]
            if len(tags_used) > 1:  # fp weapon: its root hangs where the graph puts it
                graph = read_animation_graph(f, jmad, sid) if jmad else None
                node_parent = (
                    {n: graph.nodes[p][0] for n, p in graph.nodes if p >= 0} if graph else {}
                )
                weapon = part(tags_used[1], path, warnings, {})
                roots = [n.name for n in weapon.model.nodes if n.parent < 0]
                attach = {r: node_parent[r] for r in roots if r in node_parent}
                parts.append(dataclasses.replace(weapon, attach=attach))
            animate = None
            if jmad is not None:
                animate = functools.partial(_animate, parts, jmad, f, opener, sid)
            extras: dict[str, Any] = {
                "map": name,
                "tag": tags_used[0].name,
                "source_sha256": source_sha256,
            }
            if len(tags_used) > 1:
                extras["parts"] = [t.name for t in tags_used]
            data, glb_warnings = models_to_glb(parts, extras, animate=animate)
        except (ValueError, OSError) as exc:  # CacheError and GltfError are ValueErrors
            return Skipped(path, str(exc))
        meshes = [(p.model, m) for p in parts for m in p.meshes.items()]
        warnings += [w for _, (_, m) in meshes for w in m.warnings] + glb_warnings
        return Asset(
            logical_path=path,
            data=data,
            media_type="model/gltf-binary",
            metadata={
                "kind": kind,
                "tag_id": f"{tags_used[0].datum:08x}",
                "tag_name": tags_used[0].name,
                "lod": 6,
                "sections": sorted(s for p in parts[:1] for s in p.meshes),
                "vertices": sum(len(m.positions) for _, (_, m) in meshes),
                "triangles": sum(len(t) // 3 for _, (_, m) in meshes for _, t in m.parts),
                "nodes": sum(len(p.model.nodes) for p in parts),
                "raw_files": sorted(
                    {mo.sections[s].raw.file_name or "local" for mo, (s, _) in meshes}
                ),
                "animation_graph": jmad.name if jmad else None,
                "warnings": warnings,
            },
        )

    for tag in tags:
        if tag.group == "mode":
            path = f"{name}/{_clean(tag.name or f'mode_{tag.datum:08x}')}.glb"
            yield scene(path, "model", [tag], graphs.get(tag.name or ""))
    if animated:  # first-person arms + weapon, driven by one graph, at the graph's own path
        for arms, weapon, jmad in _first_person(f, header, tags):
            path = f"{name}/{_clean(jmad.name or f'jmad_{jmad.datum:08x}')}.glb"
            yield scene(path, "first_person", [arms, weapon], jmad)


def _shaders(
    datums: Sequence[int],
    cache: dict[int, ShaderInfo | str | None],
    f: BinaryIO,
    header: CacheHeader,
    tags: list[TagEntry],
    warnings: list[str],
) -> list[ShaderInfo | None]:
    """ShaderInfo per material datum; unreadable shaders become None plus a warning."""
    infos: list[ShaderInfo | None] = []
    for datum in datums:
        if datum not in cache:
            try:
                cache[datum] = read_shader(f, header, tags, datum)
            except CacheError as exc:  # the geometry still exports, untextured
                cache[datum] = str(exc)
        info = cache[datum]
        if isinstance(info, str):
            warnings.append(f"material {len(infos)}: {info}")
            info = None
        infos.append(info)
    return infos


def _levels(
    name: str,
    f: BinaryIO,
    header: CacheHeader,
    tags: list[TagEntry],
    opener: Callable[[str], BinaryIO],
    source_sha256: str,
) -> Iterator[Asset | Skipped]:
    """One .glb per structure BSP; a malformed BSP is skipped, the others still export."""
    try:
        bsps = bsp_entries(f, header, tags)
    except CacheError as exc:
        yield Skipped(f"{name}/{name}_bsp.glb", f"no structure BSPs: {exc}")
        return
    sid = StringIds(f, header)
    shaders: dict[int, ShaderInfo | str | None] = {}
    for i, bsp in enumerate(bsps):
        path = f"{name}/{name}_bsp{i}.glb"
        sbsp = tag_by_datum(tags, bsp.sbsp)
        tag_name = sbsp.name if sbsp and sbsp.name else f"sbsp_{bsp.sbsp:08x}"
        warnings: list[str] = []
        try:
            level = read_structure_bsp(f, bsp, tag_name, sid)

            def decode(model: RenderModel) -> dict[int, SectionMesh]:
                return {
                    s: decode_section(
                        model,
                        s,
                        read_raw(sec.raw, f, opener),
                        header_size=BSP_RAW_HEADER_SIZE,
                        level=True,
                    )
                    for s, sec in enumerate(model.sections)
                    if sec.vertex_count
                }

            clusters, definitions = decode(level.clusters), decode(level.definitions)
            infos = _shaders(level.materials, shaders, f, header, tags, warnings)
            try:
                lightmap = lightmap_group(f, bsp)
            except CacheError as exc:  # geometry is fine; only the lightmap links are lost
                lightmap = None
                warnings.append(f"no lightmap render info: {exc}")
            lm_tag = tag_by_datum(tags, lightmap.bitm) if lightmap else None
            data, glb_warnings = level_to_glb(
                level,
                clusters,
                definitions,
                infos,
                functools.partial(_diffuse_uri, name, path),
                lightmap,
                {
                    "map": name,
                    "bsp": i,
                    "tag": tag_name,
                    "source_sha256": source_sha256,
                    "lightmap_bitm": lm_tag.name if lm_tag else None,
                    "clusters": len(clusters),
                    "instances": len(level.instances),
                },
                functools.partial(_page_uri, name, path, lm_tag) if lm_tag else None,
            )
        except (ValueError, OSError) as exc:  # CacheError and GltfError are ValueErrors
            yield Skipped(path, str(exc))
            continue
        warnings += [w for m in (clusters, definitions) for s in m.values() for w in s.warnings]
        warnings += glb_warnings
        placed = [  # mirror to_glb: a definition with no triangles left yields no node
            x
            for x in level.instances
            if instance_problem(x, level.definitions) is None
            and any(len(t) for _, t in definitions[x.definition].parts)
        ]
        yield Asset(
            logical_path=path,
            data=data,
            media_type="model/gltf-binary",
            metadata={
                "kind": "level",
                "tag_id": f"{bsp.sbsp:08x}",
                "tag_name": tag_name,
                "bsp_index": i,
                "clusters": len(clusters),
                "definitions": len(definitions),
                "instances": len(placed),
                "vertices": sum(len(m.positions) for m in clusters.values()),
                "cluster_triangles": _tris(clusters.values()),
                "instance_triangles": sum(_tris([definitions[x.definition]]) for x in placed),
                "raw_files": sorted(
                    {
                        sec.raw.file_name or "local"
                        for model in (level.clusters, level.definitions)
                        for sec in model.sections
                        if sec.vertex_count
                    }
                ),
                "warnings": warnings,
            },
        )


def _tris(meshes: Iterable[SectionMesh]) -> int:
    return sum(len(t) // 3 for m in meshes for _, t in m.parts)


def _lightmap(
    lightmaps: Palettes | str, tag: BitmapTag, b: Bitmap, bsp_errors: list[str]
) -> bytes | str | None:
    """The palette a P8 bitmap needs (None for other formats), or why there is none."""
    if b.format != "p8":
        return None
    if isinstance(lightmaps, str):
        return lightmaps
    missing = "no lightmap render info names this bitmap"
    if bsp_errors:  # its BSP may be the one that failed to parse
        missing += f" (unreadable BSPs: {'; '.join(bsp_errors)})"
    return lightmaps.get(tag.datum, {}).get(b.index, missing)


def _clean(tag_name: str) -> str:
    # Tag names are game data: keep the export path relative and free of "." / ".." segments.
    parts = [p for p in tag_name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(parts) or "unnamed"


def _convert(
    path: str,
    tag: BitmapTag,
    b: Bitmap,
    f: BinaryIO,
    opener: Callable[[str], BinaryIO],
    palette: bytes | None = None,
) -> Iterator[Asset | Skipped]:
    if b.lod0 is None:
        yield Skipped(f"{path}.png", "no pixel data stored")
        return
    if b.type not in ("2d", "cube", "3d"):
        yield Skipped(f"{path}.png", f"{b.type} textures are not supported")
        return
    # Both index into a 256-entry B, G, R, A palette; only where the palette lives differs.
    fmt = "p8" if b.format == "p8_bump" else b.format
    if fmt not in FORMATS:
        yield Skipped(f"{path}.png", f"unsupported pixel format {b.format}")
        return
    if not (0 < b.width <= MAX_DIM and 0 < b.height <= MAX_DIM):
        raise CacheError(f"absurd dimensions {b.width}x{b.height}")
    if not 0 <= b.mips <= MAX_MIPS:
        raise CacheError(f"bad mip count {b.mips}")
    volume = b.type == "3d"
    if volume and b.depth == 0:  # u8 field, so 255 is the ceiling
        raise CacheError(f"absurd 3D depth {b.depth}")
    if volume and b.linear:  # xemu asserts swizzled for every 3D texture (gl/texture.c)
        raise CacheError("linear 3D texture: NV2A volumes are always swizzled")
    depth = b.depth if volume else 1
    dxt = fmt.startswith("dxt")
    pitched = b.linear and not dxt
    row = bytes_needed(fmt, b.width, 1)
    pitch = -(-row // LINEAR_PITCH) * LINEAR_PITCH if pitched else row
    surface = depth * (pitch * b.height if pitched else bytes_needed(fmt, b.width, b.height))
    faces = 6 if b.type == "cube" else 1
    # Each cube face stores its own full mip chain, padded, one after another.
    stride = b.lod0.size // faces
    if surface > stride:
        raise CacheError(f"LOD0 holds {b.lod0.size} bytes, need {surface * faces}")
    raw = read_raw(b.lod0, f, opener)
    pack: dict[str, Any] = {}
    if b.type == "2d" and not b.linear and b.format in PACK_FORMATS:
        try:
            if keys := candidate_hashes(raw, b.format, b.width, b.height, b.mips + 1):
                pack["xemu_hashes"] = [f"{k:016x}" for k in keys]
        except RuntimeError:  # xxhash extra not installed: extract still works, no pack keys
            pass
    images: list[tuple[str, Any, dict[str, int]]] = []
    if volume:
        slices = decode_volume(
            raw[:surface], fmt, b.width, b.height, depth, morton=b.swizzled, palette=palette
        )
        images = [
            (f"{path}_slice{n}.png", img, {"slice": n, "depth": depth})
            for n, img in enumerate(slices)
        ]
    for n in range(0 if volume else faces):
        data = raw[n * stride : n * stride + surface]
        if pitched and pitch != row:
            data = b"".join(data[y * pitch : y * pitch + row] for y in range(b.height))
        img = decode(data, fmt, b.width, b.height, morton=b.swizzled and not dxt, palette=palette)
        name = f"{path}_face{n}.png" if faces > 1 else f"{path}.png"
        images.append((name, img, {"face": n} if faces > 1 else {}))
    for name, img, where in images:
        yield Asset(
            logical_path=name,
            data=to_png(img),
            media_type="image/png",
            metadata={
                "format": b.format,
                "width": b.width,
                "height": b.height,
                "type": b.type,
                "mips": b.mips,
                "tag_id": f"{tag.datum:08x}",
                "tag_name": tag.name,
                "bitmap_index": b.index,
                "flags": b.flags,
                "swizzled": b.swizzled,
                "raw_file": b.lod0.file_name or "local",
                "raw_offset": b.lod0.offset,
                **where,
                **({"palette_sha256": hashlib.sha256(palette).hexdigest()} if palette else {}),
                **pack,
            },
        )


def _is_halo2(cert: Certificate) -> bool:
    return cert.title_id == HALO2_TITLE_ID and cert.title_name == HALO2_TITLE


def _inspect_map(image: XdvdfsImage, path: str, size: int, verify: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"file": path.split("/")[-1], "size": size}
    try:
        with image.open(path) as f:
            h = parse_header(f)
            tags = read_tag_index(f, h)
            computed = compute_signature(f) if verify else None
    except CacheError as exc:
        return {**out, "error": str(exc)}
    out.update(
        version=h.version,
        build=h.build,
        internal_name=h.internal_name,
        scenario=h.scenario_name,
        map_type=h.map_type_name,
        tag_count=len(tags),
        top_tag_classes=dict(Counter(t.group for t in tags).most_common(8)),
        signature=f"{h.signature:08x}",
        signature_status=(
            "unchecked" if computed is None else "ok" if computed == h.signature else "mismatch"
        ),
    )
    return out
