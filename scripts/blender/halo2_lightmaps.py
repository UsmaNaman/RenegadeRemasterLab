"""Import an RRL Halo 2 level .glb into Blender with its lightmaps wired in (runs inside Blender).

    blender --python scripts/blender/halo2_lightmaps.py -- <level.glb> [--strength 1.0]

Core glTF has no lightmap slot, so RRL stores each cluster/instance page as the node extra
`lightmap_uri` (relative to the .glb) and its UVs as TEXCOORD_1. This script gives every lit object
its own material copies (slots linked to the object, so shared instance meshes stay shared) that
emit diffuse x lightmap x strength: unlit, as the game shades lightmapped geometry.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse

import bpy  # type: ignore[import-not-found]


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="halo2_lightmaps")
    p.add_argument("glb")
    p.add_argument("--strength", type=float, default=1.0, help="lightmap intensity scale [M]")
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return p.parse_args(argv)


def _diffuse(mat: bpy.types.Material) -> bpy.types.Node | None:
    """The image node feeding the Principled base colour, as the glTF importer builds it."""
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            link = next(iter(node.inputs["Base Color"].links), None)
            return link.from_node if link and link.from_node.type == "TEX_IMAGE" else None
    return None


def _lit(mat: bpy.types.Material, image: bpy.types.Image, uv: str, strength: float):  # noqa: ANN202
    lit = mat.copy()
    lit.name = f"{mat.name}|{image.name}"
    nodes, links = lit.node_tree.nodes, lit.node_tree.links
    diffuse = _diffuse(lit)
    lm = nodes.new("ShaderNodeTexImage")
    lm.image = image
    uvmap = nodes.new("ShaderNodeUVMap")
    uvmap.uv_map = uv
    links.new(uvmap.outputs["UV"], lm.inputs["Vector"])
    mix = nodes.new("ShaderNodeMix")
    mix.data_type, mix.blend_type = "RGBA", "MULTIPLY"
    mix.inputs[0].default_value = 1.0
    a, b, result = mix.inputs[6], mix.inputs[7], mix.outputs[2]  # the RGBA sockets of Mix
    if diffuse is not None:
        links.new(diffuse.outputs["Color"], a)
    links.new(lm.outputs["Color"], b)
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = strength
    links.new(result, emit.inputs["Color"])
    out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    # shortcut: emission drops the diffuse alpha (alpha-tested foliage loses its cutout); mix with
    # a Transparent BSDF if that matters.
    links.new(emit.outputs["Emission"], out.inputs["Surface"])
    return lit


def main() -> None:
    args = _args()
    folder = os.path.dirname(os.path.abspath(args.glb))
    bpy.ops.import_scene.gltf(filepath=args.glb)
    copies: dict[tuple[str, str], bpy.types.Material] = {}
    lit = missing = 0
    for obj in bpy.context.scene.objects:
        uri = obj.get("lightmap_uri")
        if obj.type != "MESH" or not uri or len(obj.data.uv_layers) < 2:
            continue
        path = os.path.join(folder, bpy.path.native_pathsep(urllib.parse.unquote(uri)))
        if not os.path.exists(path):
            missing += 1
            continue
        image = bpy.data.images.load(path, check_existing=True)
        uv = obj.data.uv_layers[1].name
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            key = (slot.material.name, image.name)
            if key not in copies:
                copies[key] = _lit(slot.material, image, uv, args.strength)
            slot.link = "OBJECT"  # per-object material; the mesh stays shared between instances
            slot.material = copies[key]
        lit += 1
    print(f"halo2_lightmaps: {lit} objects lit, {len(copies)} materials, {missing} pages missing")


main()
