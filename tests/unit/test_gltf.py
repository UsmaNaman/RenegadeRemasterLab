"""glTF .glb writer: round-trip through read_glb with synthetic geometry."""

from __future__ import annotations

import struct
from typing import Any

import numpy as np
import pytest

from rrl.processors.gltf import Gltf, GltfError, read_glb

DTYPES = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _array(doc: dict[str, Any], binary: bytes, index: int) -> Any:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    assert view["byteOffset"] % 4 == 0
    dtype = np.dtype(DTYPES[acc["componentType"]]).newbyteorder("<")
    raw = binary[view["byteOffset"] : view["byteOffset"] + view["byteLength"]]
    return np.frombuffer(raw, dtype).reshape(acc["count"], WIDTH[acc["type"]])


def test_round_trip() -> None:
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0]], np.float32)
    joints = np.array([[0, 1, 0, 0]] * 3, np.uint8)  # 12 bytes: the next view must realign
    weights = np.array([[0.5, 0.5, 0, 0]] * 3, np.float32)
    g = Gltf(extras={"tag": "synthetic"})
    root = g.node("root", rotation=(0.0, 0.0, 0.0, 1.0), scale=(2.0, 2.0, 2.0))
    j0 = g.node("j0", parent=root)
    j1 = g.node("j1", parent=j0, translation=(1.0, 0.0, 0.0))
    mat = g.material("m", "tex/a_00.png", {"shader": "s"})
    bare = g.material("bare")
    mesh = g.mesh(
        "tri",
        positions,
        [(np.array([0, 1, 2], np.uint16), mat), (np.array([2, 1, 0], np.uint16), bare)],
        joints=joints,
        weights=weights,
    )
    ibm = np.tile(np.eye(4, dtype=np.float32).reshape(16), (2, 1))
    skin = g.skin([j0, j1], ibm, skeleton=j0)
    g.node("tri", parent=root, mesh=mesh, skin=skin)
    data = g.glb()

    assert data[:4] == b"glTF" and len(data) % 4 == 0
    assert struct.unpack_from("<II", data, 4) == (2, len(data))
    (json_len,) = struct.unpack_from("<I", data, 12)
    assert json_len % 4 == 0
    doc, binary = read_glb(data)
    assert doc["asset"] == {"version": "2.0", "generator": "RRL", "extras": {"tag": "synthetic"}}
    assert doc["buffers"] == [{"byteLength": len(binary)}]
    assert doc["scenes"] == [{"nodes": [root]}]
    assert doc["nodes"][root]["children"] == [j0, 3]
    prims = doc["meshes"][0]["primitives"]
    assert [p.get("material") for p in prims] == [mat, bare]
    attrs = prims[0]["attributes"]
    pos = doc["accessors"][attrs["POSITION"]]
    assert pos["count"] == 3 and pos["min"] == [0, 0, 0] and pos["max"] == [1, 2, 0]
    np.testing.assert_array_equal(_array(doc, binary, attrs["POSITION"]), positions)
    np.testing.assert_array_equal(_array(doc, binary, attrs["JOINTS_0"]), joints)
    np.testing.assert_array_equal(_array(doc, binary, attrs["WEIGHTS_0"]), weights)
    assert _array(doc, binary, prims[1]["indices"]).ravel().tolist() == [2, 1, 0]
    assert "min" not in doc["accessors"][doc["skins"][0]["inverseBindMatrices"]]
    assert doc["materials"][mat]["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}
    assert doc["images"] == [{"uri": "tex/a_00.png"}]
    assert "baseColorTexture" not in doc["materials"][bare]["pbrMetallicRoughness"]


@pytest.mark.parametrize(
    ("indices", "message"),
    [([0, 1], "triangle list"), ([0, 1, 5], "triangle list"), ([], "no triangles")],
)
def test_bad_mesh(indices: list[int], message: str) -> None:
    pos = np.zeros((3, 3), np.float32)
    with pytest.raises(GltfError, match=message):
        Gltf().mesh("m", pos, [(np.array(indices, np.uint16), None)])


def test_bad_inputs() -> None:
    g = Gltf()
    with pytest.raises(GltfError, match="not added yet"):
        g.node("orphan", parent=5)
    with pytest.raises(GltfError, match="unsupported component"):
        g.mesh("m", np.zeros((3, 3), np.float64), [])  # type: ignore[arg-type]
    with pytest.raises(GltfError, match="shape"):
        g.skin([0], np.zeros((2, 16), np.float32))
    with pytest.raises(GltfError, match="alpha mode"):
        g.material("m", alpha_mode="ADD")
    with pytest.raises(GltfError, match="TEXCOORD_1 without TEXCOORD_0"):
        g.mesh("m", np.zeros((3, 3), np.float32), [], texcoords1=np.zeros((3, 2), np.float32))


@pytest.mark.parametrize(
    "data",
    [b"nope" + b"\0" * 16, b"glTF" + struct.pack("<II", 2, 99) + b"\0" * 12, b"glTF" * 2],
)
def test_read_glb_rejects(data: bytes) -> None:
    with pytest.raises(GltfError):
        read_glb(data)
