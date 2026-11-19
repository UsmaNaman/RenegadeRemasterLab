"""Exercise the standalone script with Blender and generated RRL exports."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from rrl.processors.gltf import Gltf

ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / "scripts/blender/rrl_import.py"


@pytest.fixture(scope="session")
def blender() -> str:
    executable = shutil.which("blender")
    mac_app = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    if not executable and mac_app.is_file():
        executable = str(mac_app)
    if not executable:
        pytest.skip("Blender executable unavailable on PATH or in /Applications/Blender.app")
    return executable


def level(folder: Path) -> Path:
    g = Gltf()
    uri = "textures/paint%20red.png"
    textures = folder / "textures"
    textures.mkdir()
    for name in ("paint red", "page_a", "page_b"):
        Image.new("RGBA", (4, 4), (180, 80, 40, 128)).save(textures / f"{name}.png")
    tri = np.array([0, 1, 2], np.uint16)
    positions = np.array([(0, 0, 0), (4, 0, 0), (0, 1, 2)], np.float32)
    uv = np.array([(0, 0), (1, 0), (0, 1)], np.float32)
    extras = {"template": "test", "bitmaps": ["paint red"], "diffuse_source": "slot 0"}
    materials = [g.material(mode, uri, extras, mode) for mode in ("MASK", "BLEND", "OPAQUE")]
    mesh = g.mesh("tri", positions, [(tri, m) for m in materials], texcoords=uv, texcoords1=uv)
    for i, page in enumerate(("page_a", "page_b", "missing")):
        g.node(f"cluster_{i}", mesh=mesh, extras={"lightmap_uri": f"textures/{page}.png"})
    path = folder / "level.glb"
    path.write_bytes(g.glb())
    return path


def run(blender: str, glb: Path, *args: str, check: str = "") -> dict[str, Any]:
    proc = subprocess.run(
        [
            blender,
            "--quiet",
            "-b",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(IMPORTER),
            *(["--python-expr", check] if check else []),
            "--",
            str(glb),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.splitlines()[-1])


def test_lightmaps_and_alpha(blender: str, tmp_path: Path) -> None:
    report = run(
        blender,
        level(tmp_path),
        "--strength",
        "1.5",
        check="""
import bpy
a, b, missing = [bpy.context.scene.objects[f"cluster_{i}"] for i in range(3)]
assert a.data == b.data == missing.data
for slot in a.material_slots:
    assert slot.link == "OBJECT"
    mat = slot.material
    assert list(mat["bitmaps"]) == ["paint red"] and mat["template"] == "test"
    assert mat["diffuse_source"] == "slot 0"
    tree = mat.node_tree
    assert tree.nodes["UV Map"].uv_map == a.data.uv_layers[1].name
    assert tree.nodes["Emission"].inputs["Strength"].default_value == 1.5
    surface = tree.nodes["Material Output"].inputs["Surface"].links[0].from_node
    if mat.name.startswith("OPAQUE"):
        assert surface.type == "EMISSION"
    else:
        factor = surface.inputs[0].links[0]
        assert surface.inputs[1].links[0].from_node.type == "BSDF_TRANSPARENT"
        if mat.name.startswith("MASK"):
            assert factor.from_node.type == "MATH"
        else:
            assert mat.surface_render_method == "BLENDED" and factor.from_socket.name == "Alpha"
assert all(s.material != t.material for s,t in zip(a.material_slots,b.material_slots))
""",
    )
    assert report["objects"] == 3
    assert report["objects_lit"] == 2 and report["materials"] == 9
    assert report["warnings"] == ["cluster_2: missing lightmap textures/missing.png"]


def test_variant_source_order(blender: str, tmp_path: Path) -> None:
    g = Gltf()
    mesh = g.mesh("section_0", np.eye(3, dtype=np.float32), [(np.arange(3, dtype=np.uint16), None)])
    for name in ("body:zulu", "body:alpha", "visor:clear", "visor:dark"):
        g.node(name, mesh=mesh, extras={"section": 0})
    g.node("marker:hand")
    path = tmp_path / "variants.glb"
    path.write_bytes(g.glb())
    report = run(
        blender,
        path,
        check="""
import bpy
scene = bpy.context.scene
for region, visible, hidden in [("body", "zulu", "alpha"), ("visor", "clear", "dark")]:
    children = scene.collection.children[region].objects
    a, b = (children[region + ":" + p] for p in [visible, hidden])
    assert not a.hide_render and not a.hide_viewport
    assert b.hide_render and b.hide_viewport and a.data == b.data
    assert len(b.users_collection) == 1
assert "marker" not in scene.collection.children
""",
    )
    assert report["regions"] == {"body": "zulu", "visor": "clear"}
    assert report["objects"] == 5 and not report["warnings"]


def animated_scene(folder: Path, *, single: str | None = None) -> Path:
    from dataclasses import replace

    from unit.test_halo2_models import _decode, build

    from rrl.adapters.xbox_halo2.render_model import ModelPart, models_to_glb

    model, meshes = _decode(build())
    weapon = replace(
        model,
        nodes=tuple(replace(n, name="w_" + n.name) for n in model.nodes),
        regions=tuple(
            replace(r, permutations=tuple(replace(p, name="weapon") for p in r.permutations))
            for r in model.regions
        ),
    )

    def animate(g: Gltf, joints: dict[str, int]) -> list[str]:
        values = np.array([(0, 0, 1), (0, 0, 2)], np.float32)
        times = np.array([0, 2 / 30], np.float32)
        for name, kind, info, deltas in [
            ("walk", "base", 3, [[1, 0, 0, 2.5 * np.pi], [1, 0, 1, 0], [0, 1, 0, 0]]),
            ("aim", "replacement", 1, [[0, 1]] * 3),
            ("turn", "replacement", 2, [[0, 0, -np.pi]] * 3),
            ("idle", "base", 0, []),
        ]:
            if single is not None and name != "walk":
                continue
            extras = {"type": kind, "frames": 3, "fps": 30}
            if info:
                extras["root_motion"] = {"frame_info": info, "deltas": deltas}
            channels = [(joints[j], "translation", times, values) for j in ("pelvis", "w_pelvis")]
            g.animation(single or name, channels, extras)
        return []

    parts = [
        ModelPart(model, meshes, [None, None], lambda b: ""),
        ModelPart(weapon, meshes, [None, None], lambda b: "", {"w_pelvis": "spine"}),
    ]
    payload, warnings = models_to_glb(parts, {}, animate=animate)
    assert not warnings
    path = folder / "arms.glb"
    path.write_bytes(payload)
    return path


@pytest.mark.parametrize(
    ("selected", "motion", "end"),
    [
        ("walk", True, (0, 1, 1)),
        ("aim", True, (0, 3, 0)),
        ("turn", True, (0, 0, 0)),
        ("walk", False, (0, 0, 0)),
    ],
)
def test_nla_motion(
    blender: str, tmp_path: Path, selected: str, motion: bool, end: tuple[int, int, int]
) -> None:
    options = ["--clip", selected] + (["--root-motion"] if motion else [])
    check = """
import bpy
from mathutils import Vector
scene = bpy.context.scene
arm = next(o for o in scene.objects if o.type == "ARMATURE")
anim = arm.animation_data
assert all(not o.hide_render and not o.hide_viewport for o in scene.objects if o.type == "MESH")
assert anim.action is None and len(anim.nla_tracks) == 2
assert [t.name for t in anim.nla_tracks] == ["Base", "Replacement"]
assert {s.name for s in anim.nla_tracks["Base"].strips} == {"walk", "idle"}
assert {s.name for s in anim.nla_tracks["Replacement"].strips} == {"aim", "turn"}
strips = [s for t in anim.nla_tracks for s in t.strips]
active, = [s for s in strips if not s.mute]
assert active.name == SELECTED and active.blend_type == "REPLACE"
assert active.action_slot and active.frame_start == scene.frame_start
assert scene.frame_end == active.frame_end and scene.render.fps == 30
assert arm.matrix_world.translation.length < 1e-5
scene.frame_set(scene.frame_end)
assert (arm.matrix_world.translation - 3.048 * Vector(END)).length < 1e-4, arm.matrix_world
if SELECTED == "turn":
    scene.frame_set(scene.frame_start + 1, subframe=0.5)
    heading = arm.matrix_world.to_3x3() @ Vector((1,0,0))
    assert heading.y < -3.04, heading
"""
    report = run(
        blender,
        animated_scene(tmp_path),
        *options,
        check=check.replace("SELECTED", repr(selected)).replace("END", repr(end)),
    )
    assert [c["name"] for c in report["clips"] if c["selected"]] == [selected]
    assert all(c["armatures"] and c["frames"] == 3 for c in report["clips"])
    for region in ("body", "arms", "legs"):
        assert report["regions"][region] == "base"
        assert report["regions"][region + ".001"] == "weapon"
    assert not report["warnings"]


@pytest.mark.parametrize("choose", [True, False])
def test_single_long_clip(blender: str, tmp_path: Path, choose: bool) -> None:
    name = "stand:" + "long-name-" * 8
    report = run(
        blender, animated_scene(tmp_path, single=name), *(["--clip", name] if choose else [])
    )
    (clip,) = report["clips"]
    assert clip["name"] == name and clip["selected"] and clip["armatures"]
    assert not report["warnings"]


@pytest.mark.parametrize("mirror", [False, True])
def test_texture_relink(blender: str, tmp_path: Path, mirror: bool) -> None:
    export = tmp_path / "export" / "arena"
    meshes = export / "models" if mirror else export
    meshes.mkdir(parents=True)
    g = Gltf(extras={"map": "arena"})
    textures = tmp_path / "upscaled"
    target = textures / "arena" if mirror else textures
    prefix = "../" if mirror else ""
    for subdir, size in [("paint", 5), ("metal", 7), ("pages", 9)]:
        folder = target / subdir
        folder.mkdir(parents=True)
        Image.new("RGB", (size, size)).save(folder / "tile 1.png")
    diffuse = [
        g.material(d, prefix + d + "/tile%201.png", extras={"bitmaps": [None, d]})
        for d in ("paint", "metal")
    ]
    uv = np.ones((3, 2), np.float32)
    mesh = g.mesh(
        "shared",
        np.eye(3, dtype=np.float32),
        [(np.arange(3, dtype=np.uint16), m) for m in diffuse],
        texcoords=uv,
        texcoords1=uv,
    )
    g.node("cluster", mesh=mesh, extras={"lightmap_uri": prefix + "pages/tile%201.png"})
    path = meshes / "scene.glb"
    path.write_bytes(g.glb())
    report = run(
        blender,
        path,
        "--textures",
        str(textures),
        check="""
import bpy,json
obj = bpy.context.scene.objects["cluster"]
for slot in obj.material_slots:
    mat = slot.material
    d = json.loads(mat["bitmaps"])[1]
    images = [n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE"]
    assert {i.size[0] for i in images} == {9, 5 if d == "paint" else 7}
""",
    )
    assert report["objects_lit"] == 1 and report["images_relinked"] == 3 and not report["warnings"]


@pytest.mark.parametrize("frames", [None, 4])
def test_preview(blender: str, tmp_path: Path, frames: int | None) -> None:
    g = Gltf()
    positions = np.array([(0, 0, 0), (1, 0, 0), (0.25, 3, 1)], np.float32)
    uv = np.array([(0, 0), (1, 0), (0, 1)], np.float32)
    Image.new("RGB", (4, 4), (235, 50, 20)).save(tmp_path / "red.png")
    mat = g.material("paint", "red.png")
    mesh = g.mesh("tri", positions, [(np.arange(3, dtype=np.uint16), mat)], texcoords=uv)
    g.node("part:first", mesh=mesh, translation=(10, -30, 40))
    g.node("part:far", mesh=mesh, translation=(3000, 2000, 1000))
    path = tmp_path / "preview.glb"
    path.write_bytes(g.glb())
    output = tmp_path / "renders" / "still.png"
    report = run(
        blender, path, "--preview", str(output), *(["--turntable", str(frames)] if frames else [])
    )
    assert report["objects"] == 2 and not report["warnings"]
    sequence = sorted(output.parent.glob("still_*.png"))
    assert len(sequence) == (frames or 0)
    pixels = []
    for file in [output, *sequence]:
        with Image.open(file) as image:
            assert image.format == "PNG" and image.size == (512, 512)
            r, g, b, a = np.asarray(image).transpose(2, 0, 1)
            y, x = np.where(a > 200)
            assert len(x) > 300 and 0 < x.min() < x.max() < 511 and 0 < y.min() < y.max() < 511
            assert r[y, x].mean() > 2 * g[y, x].mean() > 2 * b[y, x].mean()
            pixels.append(image.tobytes())
    if frames:
        assert pixels[0] == pixels[1] and len(set(pixels[1:])) == frames


def test_incomplete_lightmap_metadata(blender: str, tmp_path: Path) -> None:
    g = Gltf()
    mesh = g.mesh("tri", np.eye(3, dtype=np.float32), [(np.arange(3, dtype=np.uint16), None)])
    g.node("no_uri", mesh=mesh, extras={"lightmap_bitmap": 0})
    g.node("no_uv", mesh=mesh, extras={"lightmap_uri": "page.png"})
    path = tmp_path / "unlit.glb"
    path.write_bytes(g.glb())
    report = run(blender, path)
    assert report["objects_lit"] == 0
    assert set(report["warnings"]) == {
        "no_uri: lightmap bitmap has no lightmap_uri",
        "no_uv: lightmap needs TEXCOORD_1",
    }
