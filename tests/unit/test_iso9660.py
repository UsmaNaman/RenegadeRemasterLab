import io
from pathlib import Path

import pytest
from fixtures.iso_builder import BLOCK, PAYLOAD, both, cue, image

from rrl.containers.iso9660 import Iso9660Image, IsoError


@pytest.mark.parametrize(
    ("mode", "stride", "header"),
    [("MODE1/2048", 2048, 0), ("MODE1/2352", 2352, 16), ("MODE2/2352", 2352, 24)],
)
def test_file_view(mode: str, stride: int, header: int) -> None:
    with Iso9660Image(io.BytesIO(image(mode=mode))) as iso:
        assert iso.mode == mode
        assert [e.path for e in iso.entries()] == ["BIN", "BIN/SLUS_123.45", "SYSTEM.CNF"]
        path = "bin/slus_123.45;1"
        assert iso.locate(path) == (
            23 * stride + header,
            len(PAYLOAD) + (len(PAYLOAD) - 1) // BLOCK * (stride - BLOCK),
        )
        with iso.open(path) as f:
            assert f.read() == PAYLOAD
            f.seek(2047)
            assert f.read(11) == PAYLOAD[2047:2058]


@pytest.mark.parametrize(
    ("pos", "bad", "msg"),
    [
        (16 * BLOCK + 1, b"wrong", "descriptor"),
        (20 * BLOCK + 68 + 2, both(42, 4), "extent"),
        (20 * BLOCK + 68 + 2, both(20, 4), "cycle"),
        (16 * BLOCK + 84, bytes(4), "both-endian"),
        (20 * BLOCK, b"\1", "record length"),
    ],
)
def test_malformed(pos: int, bad: bytes, msg: str) -> None:
    data = bytearray(image())
    data[pos : pos + len(bad)] = bad
    with pytest.raises(IsoError, match=msg):
        Iso9660Image(io.BytesIO(data))


def test_cue(tmp_path: Path) -> None:
    path = cue(tmp_path)
    with Iso9660Image(path) as iso:
        assert iso.locate("bin/sles_987.65")[0] == 24 * 2352 + 24
    path.write_text('FILE "data track.bin" BINARY\nTRACK 01 MODE2/2352\n')
    with pytest.raises(IsoError, match="index"):
        Iso9660Image(path)


def test_xa_form2_is_rejected() -> None:
    data = bytearray(image(mode="MODE2/2352"))
    data[16 * 2352 + 18] = data[16 * 2352 + 22] = 32
    with pytest.raises(IsoError, match="XA Form 1"):
        Iso9660Image(io.BytesIO(data))


def test_track_end_bounds_the_volume(tmp_path: Path) -> None:
    path = cue(tmp_path)
    with path.open("a") as sheet:
        sheet.write(" TRACK 02 AUDIO\n INDEX 01 00:00:30\n")
    with pytest.raises(IsoError, match="volume size"):
        Iso9660Image(path)
