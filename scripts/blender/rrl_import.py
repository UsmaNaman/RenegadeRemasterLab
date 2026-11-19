"""Import an RRL GLB into a new Blender 5.x scene. See README.md for invocation."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import bpy
from io_scene_gltf2.blender.imp.blender_gltf import BlenderGlTF
from io_scene_gltf2.io.imp.gltf2_io_gltf import glTFImporter
from mathutils import Quaternion, Vector


def read_glb(path: Path) -> glTFImporter:
    # Use Blender's bundled glTF reader and its normal operator defaults. Keeping the
    # import context lets us identify the source nodes even when Blender renames them.
    props = bpy.ops.import_scene.gltf.get_rna_type().properties
    settings = {p.identifier: p.default for p in props if hasattr(p, "default")}
    settings.update(import_user_extensions=[], import_pack_images=False)
    gltf = glTFImporter(str(path), settings)
    gltf.read()
    gltf.checks()
    return gltf


def texture_overrides(gltf: glTFImporter, folder: Path, directory: Path) -> dict[str, Path]:
    """Match the mirrored export tree or a path relative to the GLB directory."""
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"texture directory does not exist: {directory}")
    map_name = (gltf.data.asset.extras or {}).get("map")
    map_dir = next((p for p in (folder, *folder.parents) if p.name == map_name), None)
    uris = {image.uri for image in gltf.data.images or []}
    uris.update((node.extras or {}).get("lightmap_uri") for node in gltf.data.nodes)
    matches = {}
    for uri in filter(None, uris):
        relative = Path(unquote(uri))
        if relative.is_absolute():
            continue
        source = (folder / relative).resolve()
        candidates = [relative]
        if map_dir and source.is_relative_to(map_dir):
            candidates.insert(0, source.relative_to(map_dir.parent))
        for candidate in candidates:
            target = (directory / candidate).resolve()
            if target.is_relative_to(directory) and target != source and target.is_file():
                matches[uri] = target
                break
    return matches


def create_scene(
    gltf: glTFImporter, path: Path, overrides: dict[str, Path], warnings: list[str]
) -> None:
    for image in gltf.data.images or []:
        if image.uri in overrides:
            # Resolve before image creation so a replacement also works without the original.
            image.uri = quote(overrides[image.uri].as_posix(), safe="/:")
        elif image.uri and not (path.parent / unquote(image.uri)).is_file():
            warnings.append(f"missing texture {image.uri}")
    for mat in gltf.data.materials or []:
        bitmaps = (mat.extras or {}).get("bitmaps")
        if bitmaps and None in bitmaps:
            # Mixed string/null arrays are not representable as Blender IDProperty arrays.
            mat.extras["bitmaps"] = json.dumps(bitmaps)
    bpy.context.window.scene = bpy.data.scenes.new(path.stem)
    if gltf.data.animations:
        fps = gltf.data.animations[0].extras.get("fps", 30)
        bpy.context.scene.render.fps = round(fps)
        bpy.context.scene.render.fps_base = round(fps) / fps
    BlenderGlTF.create(gltf)


def lit_material(
    original: bpy.types.Material, image: bpy.types.Image, uv: str, strength: float
) -> bpy.types.Material:
    mat = original.copy()
    mat.name = f"{original.name}|{image.name}"
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    diffuse = bsdf.inputs["Base Color"]
    page = nodes.new("ShaderNodeTexImage")
    page.image = image
    uvmap = nodes.new("ShaderNodeUVMap")
    uvmap.uv_map = uv
    links.new(uvmap.outputs["UV"], page.inputs["Vector"])
    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs["Fac"].default_value = 1.0
    if diffuse.is_linked:
        links.new(diffuse.links[0].from_socket, mix.inputs["Color1"])
    else:
        mix.inputs["Color1"].default_value = diffuse.default_value
    links.new(page.outputs["Color"], mix.inputs["Color2"])
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = strength
    links.new(mix.outputs["Color"], emit.inputs["Color"])
    surface = emit.outputs[0]
    alpha = bsdf.inputs["Alpha"]
    if alpha.is_linked:
        # The importer already applied the glTF MASK cutoff, or connected raw BLEND alpha.
        transparent = nodes.new("ShaderNodeBsdfTransparent")
        shader = nodes.new("ShaderNodeMixShader")
        links.new(alpha.links[0].from_socket, shader.inputs[0])
        links.new(transparent.outputs[0], shader.inputs[1])
        links.new(surface, shader.inputs[2])
        surface = shader.outputs[0]
    out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
    links.new(surface, out.inputs["Surface"])
    if diffuse.is_linked:
        nodes.active = diffuse.links[0].from_node
    return mat


def lightmaps(
    folder: Path, strength: float, overrides: dict[str, Path], warnings: list[str]
) -> int:
    copies: dict[tuple[bpy.types.Material, bpy.types.Image, str], bpy.types.Material] = {}
    lit = 0
    for obj in bpy.context.scene.objects:
        uri = obj.get("lightmap_uri")
        if obj.type != "MESH":
            continue
        if not uri:
            if "lightmap_bitmap" in obj:
                warnings.append(f"{obj.name}: lightmap bitmap has no lightmap_uri")
            continue
        if len(obj.data.uv_layers) < 2:
            warnings.append(f"{obj.name}: lightmap needs TEXCOORD_1")
            continue
        path = overrides.get(uri, folder / unquote(uri))
        if not path.is_file():
            warnings.append(f"{obj.name}: missing lightmap {uri}")
            continue
        image = bpy.data.images.load(str(path), check_existing=True)
        uv = obj.data.uv_layers[1].name
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            key = mat, image, uv
            if key not in copies:
                copies[key] = lit_material(mat, image, uv, strength)
            slot.link = "OBJECT"  # instances retain the same mesh
            slot.material = copies[key]
        lit += 1
    return lit


def variants(gltf: glTFImporter, warnings: list[str]) -> dict[str, str]:
    """Group mesh instances in source order, including meshes moved under imported skins."""
    meshes = {
        v.mesh_node_idx: v.blender_object
        for v in gltf.vnodes.values()
        if v.mesh_node_idx is not None and v.blender_object is not None
    }
    members: dict[str, list[tuple[int | None, str, bpy.types.Object]]] = {}
    for index, node in enumerate(gltf.data.nodes):
        obj = meshes.get(index)
        if obj is None or obj.type != "MESH":
            continue
        region, sep, permutation = (node.name or "").partition(":")
        if sep and region and permutation:
            members.setdefault(region, []).append((node.skin, permutation, obj))

    first: dict[str, str] = {}
    for region, entries in members.items():
        # First-person exports may reuse a region name on the arms and the weapon.
        skins = {skin for skin, _, _ in entries if skin is not None}
        collections: dict[int | None, bpy.types.Collection] = {}
        for skin, permutation, obj in entries:
            key = skin if len(skins) > 1 else None
            if key not in collections:
                collection = bpy.data.collections.new(region)
                bpy.context.scene.collection.children.link(collection)
                collections[key] = collection
                first[collection.name] = permutation
                if skin is None and len(skins) > 1:
                    warnings.append(f"{region}: unskinned meshes have no part identifier")
            destination = collections[key]
            for collection in list(obj.users_collection):
                collection.objects.unlink(obj)
            destination.objects.link(obj)
            obj.hide_render = obj.hide_viewport = permutation != first[destination.name]
    return first


def bake_motion(
    gltf: glTFImporter,
    arm: bpy.types.Object,
    bag: bpy.types.ActionChannelbag,
    extras: dict[str, Any],
    scene_fps: float,
) -> None:
    motion = extras["root_motion"]
    info = motion["frame_info"]
    widths = {1: 2, 2: 3, 3: 4}
    deltas = motion["deltas"]
    if (
        len(deltas) != extras["frames"]
        or info not in widths
        or any(len(d) != widths[info] or not all(map(math.isfinite, d)) for d in deltas)
    ):
        raise ValueError("root_motion deltas disagree with frame_info or frames")

    # RRL parents models to halo_to_gltf. Blender may use that very object as its
    # armature. Convert Halo displacements through the export root exactly once.
    root = gltf.vnodes[gltf.data.scenes[gltf.data.scene].nodes[0]].blender_object
    to_parent = (arm.matrix_basis @ arm.matrix_world.inverted() @ root.matrix_world).to_3x3()
    up = (to_parent @ gltf.loc_gltf_to_blender((0, 0, 1))).normalized()
    origin, base_rotation, _ = arm.matrix_basis.decompose()
    arm.rotation_mode = "QUATERNION"
    locations, rotations = [origin], [base_rotation]
    offset = Vector((0, 0, 0))
    yaw = 0.0
    for d in deltas:
        dx, dy = d[:2]
        c, s = math.cos(yaw), math.sin(yaw)
        # [M] Translate in the pre-turn heading, then apply this frame's yaw.
        offset += Vector((c * dx - s * dy, s * dx + c * dy, d[2] if info == 3 else 0))
        if info >= 2:
            step = math.remainder(d[-1], math.tau)
            yaw += math.pi if step == -math.pi else step
        locations.append(origin + to_parent @ gltf.loc_gltf_to_blender(offset))
        turn = Quaternion((math.cos(yaw / 2), *(up * math.sin(yaw / 2))))
        rotations.append(turn @ base_rotation)

    for path, values in [("location", locations), ("rotation_quaternion", rotations)]:
        for axis in range(len(values[0])):
            curve = bag.fcurves.new(path, index=axis)
            points = curve.keyframe_points
            points.add(len(values))
            points.foreach_set(
                "co",
                [
                    coord
                    for i, value in enumerate(values)
                    for coord in (i * scene_fps / extras["fps"], value[axis])
                ],
            )
            for point in points:
                point.interpolation = "LINEAR"
            curve.update()


def animations(
    gltf: glTFImporter, selected: str | None, motion: bool, warnings: list[str]
) -> list[dict[str, Any]]:
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    source = gltf.data.animations or []
    if selected is not None:
        matches = [a.track_name for a in source if a.name == selected]
        if len(matches) > 1:
            raise ValueError(f"ambiguous clip {selected!r}")
        if matches:
            selected = matches[0]
    if selected is None and source:
        selected = source[0].track_name
    if selected is not None and selected not in {a.track_name for a in source}:
        raise ValueError(f"unknown clip {selected!r}")

    # NLA strips in one track cannot overlap. Give each class its own timeline and
    # common start frames across rigs, including clips that only target one rig.
    starts = {"base": 1, "replacement": 1}
    clips = {}
    for a in source:
        meta = a.extras
        kind = meta["type"]
        start = starts[kind]
        steps = meta["frames"] if motion and meta.get("root_motion") else max(meta["frames"] - 1, 1)
        duration = steps * fps / meta["fps"]
        clips[a.track_name] = {
            "name": a.name,
            "type": kind,
            "frames": meta["frames"],
            "fps": meta["fps"],
            "start": start,
            "end": start + duration,
            "selected": a.track_name == selected,
            "armatures": [],
        }
        starts[kind] = math.ceil(start + duration) + 1

    for arm in scene.objects:
        anim = arm.animation_data
        if arm.type != "ARMATURE" or anim is None:
            continue
        bindings = {}
        for track in list(anim.nla_tracks):
            if track.name in clips:
                strip = track.strips[0]
                bindings[track.name] = (strip.action, strip.action_slot)
                anim.nla_tracks.remove(track)
        anim.action = None
        base = anim.nla_tracks.new()
        base.name = "Base"
        replacement = anim.nla_tracks.new(prev=base)
        replacement.name = "Replacement"
        for a in source:
            clip = clips[a.track_name]
            if a.track_name not in bindings:
                continue
            action, slot = bindings[a.track_name]
            if motion and a.extras.get("root_motion"):
                bag = action.layers[0].strips[0].channelbag(slot)
                bake_motion(gltf, arm, bag, a.extras, fps)
            track = replacement if clip["type"] == "replacement" else base
            strip = track.strips.new(a.track_name, clip["start"], action)
            strip.action_slot = slot
            strip.action_frame_start = 0
            strip.action_frame_end = clip["end"] - clip["start"]
            strip.blend_type = "REPLACE"
            strip.extrapolation = "NOTHING"
            strip.mute = not clip["selected"]
            clip["armatures"].append(arm.name)
    for clip in clips.values():
        if not clip["armatures"]:
            warnings.append(f"{clip['name']}: no animated armature in GLB")
    if selected is not None:
        clip = clips[selected]
        scene.frame_start, scene.frame_end = clip["start"], math.ceil(clip["end"])
        scene.frame_set(scene.frame_start)
    return list(clips.values())


def preview(output: Path, turntable: int | None) -> None:
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    corners = []
    for obj in scene.objects:
        if obj.type == "MESH" and obj.visible_get() and not obj.hide_render:
            mesh = obj.evaluated_get(depsgraph)
            corners.extend(mesh.matrix_world @ Vector(p) for p in mesh.bound_box)
    if not corners:
        raise ValueError("preview requires visible mesh geometry")
    low = Vector([min(p[i] for p in corners) for i in range(3)])
    high = Vector([max(p[i] for p in corners) for i in range(3)])
    center = (low + high) / 2
    radius = max((high - low).length / 2, 0.001)

    data = bpy.data.cameras.new("Preview")
    camera = bpy.data.objects.new("Preview", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    data.type = "ORTHO"
    data.ortho_scale = radius * 2.12
    data.clip_start = radius * 0.01
    data.clip_end = radius * 6
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.color_type = "TEXTURE"
    scene.display.shading.light = "FLAT"
    scene.view_settings.view_transform = "Standard"
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    output.parent.mkdir(parents=True, exist_ok=True)

    def aim(angle: float) -> None:
        direction = Vector((math.cos(angle), math.sin(angle), 0.55)).normalized()
        camera.location = center + direction * (radius * 3)
        camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()

    first_angle = -math.pi / 3
    for i in range(turntable or 1):
        aim(first_angle + math.tau * i / (turntable or 1))
        frame = output.with_name(f"{output.stem}_{i + 1:04d}.png") if turntable else output
        scene.render.filepath = str(frame)
        bpy.ops.render.render(write_still=True)
        if turntable and i == 0:
            shutil.copyfile(frame, output)
    aim(first_angle)
    scene.render.filepath = str(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("glb", type=Path)
    parser.add_argument("--strength", type=float, default=1.0, help="lightmap emission strength")
    parser.add_argument("--clip", help="exact clip name to enable (default: first exported)")
    parser.add_argument("--root-motion", action="store_true", help="bake movement onto armatures")
    parser.add_argument("--textures", type=Path, help="directory of replacement PNGs")
    parser.add_argument("--preview", type=Path, help="render a Workbench texture still to PNG")
    parser.add_argument("--turntable", type=int, help="also render N evenly spaced orbit views")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
    if args.turntable is not None and (args.turntable < 1 or not args.preview):
        parser.error("--turntable needs a positive frame count and --preview OUT.png")
    if args.preview and args.preview.suffix.lower() != ".png":
        parser.error("--preview needs a .png output path")
    path = args.glb.resolve()
    gltf = read_glb(path)
    overrides = texture_overrides(gltf, path.parent, args.textures) if args.textures else {}
    warnings: list[str] = []
    create_scene(gltf, path, overrides, warnings)
    regions = variants(gltf, warnings)
    lit = lightmaps(path.parent, args.strength, overrides, warnings)
    clips = animations(gltf, args.clip, args.root_motion, warnings)
    objects = bpy.context.scene.objects
    materials = {s.material for obj in objects for s in obj.material_slots if s.material}
    targets = set(overrides.values())
    relinked = {
        node.image
        for mat in materials
        for node in mat.node_tree.nodes
        if node.type == "TEX_IMAGE"
        and node.image
        and Path(bpy.path.abspath(node.image.filepath)).resolve() in targets
    }
    object_count = len(objects)
    if args.preview:
        preview(args.preview.resolve(), args.turntable)
    print(
        json.dumps(
            {
                "objects": object_count,
                "materials": len(materials),
                "objects_lit": lit,
                "regions": regions,
                "images_relinked": len(relinked),
                "clips": clips,
                "warnings": warnings,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
