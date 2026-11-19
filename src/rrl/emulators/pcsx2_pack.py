"""Build a PCSX2 replacement folder using the dump as the filename manifest."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass
class PackReport:
    replacements: str
    packed: int
    missing: list[str]
    extra: list[str]
    size_mismatched: list[str]


def _png_names(folder: Path) -> set[str]:
    if not folder.is_dir():
        raise ValueError(f"input directory does not exist: {folder}")
    names: set[str] = set()
    folded: set[str] = set()
    for path in folder.iterdir():
        if path.suffix.lower() != ".png":
            continue
        name = path.name
        if (
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*\.[pP][nN][gG]", name)
            or ".." in name
            or re.match(r"(con|prn|aux|nul|(?:com|lpt)[0-9])\.", name, re.IGNORECASE)
            or path.is_symlink()
            or not path.is_file()
        ):
            raise ValueError(f"unsafe PNG filename in {folder}: {name!r}")
        if name.casefold() in folded:
            raise ValueError(f"ambiguous PNG filenames in {folder}: {name!r}")
        folded.add(name.casefold())
        names.add(name)
    return names


def _size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path, formats=["PNG"]) as im:
            im.load()
            return im.size
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError(f"invalid PNG {path}: {exc}") from exc


def build_pack(dump: Path, images: Path, pack: Path, serial: str) -> PackReport:
    if not re.fullmatch(r"[A-Z]{4}-[0-9]{5}", serial):
        raise ValueError("expected a PS2 serial such as SLUS-12345")
    dump, images = dump.resolve(), images.resolve()
    target = pack.resolve() / "textures" / serial / "replacements"
    if target.resolve() != target:
        raise ValueError(f"symlink in replacement directory: {target}")
    for source in (dump, images):
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError("replacement directory overlaps an input directory")
    originals, edits = _png_names(dump), _png_names(images)
    if not originals:
        raise ValueError(f"no dumped PNGs found in {dump}")
    report = PackReport(str(target), 0, sorted(originals - edits), sorted(edits - originals), [])
    target.mkdir(parents=True, exist_ok=True)
    for name in sorted(originals & edits):
        width, height = _size(dump / name)
        dest = target / name
        if dest.is_symlink():
            raise ValueError(f"symlink at replacement filename: {dest}")
        # Validate exactly the bytes being published, even if the editor replaces its file.
        with tempfile.TemporaryDirectory(dir=target) as tmp:
            stage = Path(tmp) / name
            shutil.copyfile(images / name, stage)
            w, h = _size(stage)
            scale, remainder = divmod(w, width)
            if remainder or scale < 1 or h != height * scale:
                report.size_mismatched.append(f"{name}: dump {width}x{height}, edited {w}x{h}")
                continue
            stage.replace(dest)
            report.packed += 1
    return report
