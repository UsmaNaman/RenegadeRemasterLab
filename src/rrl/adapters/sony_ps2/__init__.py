"""Identify and inspect a PlayStation 2 disc using its ISO primary tree and SYSTEM.CNF."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from rrl.api import API_VERSION, Match, Source
from rrl.containers.iso9660 import Iso9660Image, IsoError

REGIONS = {
    "SLUS": "NTSC-U",
    "SCUS": "NTSC-U",
    "SLES": "PAL",
    "SCES": "PAL",
    "SLPS": "NTSC-J",
    "SLPM": "NTSC-J",
    "SCPS": "NTSC-J",
}


class PlayStationAdapter:
    id: ClassVar[str]
    name: ClassVar[str]
    boot_key: ClassVar[str]
    device: ClassVar[str]
    version: ClassVar[str] = "0.1.0"
    api_version: ClassVar[int] = API_VERSION
    capabilities: ClassVar[frozenset[str]] = frozenset({"identify", "inspect"})

    def _boot(self, image: Iso9660Image) -> tuple[Match, dict[str, str], str, int]:
        with image.open("SYSTEM.CNF") as f:
            data = f.read(65537)
        if len(data) > 65536:
            raise IsoError("SYSTEM.CNF exceeds 64 KiB")
        fields = {}
        for line in data.decode("ascii").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields[key.strip().upper()] = value.strip()
        device, sep, path = fields.get(self.boot_key, "").partition(":")
        if not sep or device.lower() != self.device:
            raise IsoError(f"missing {self.boot_key} boot path")
        path = path.replace("\\", "/").lstrip("/")
        serial = re.fullmatch(r"([A-Z]{4})_(\d{3})\.(\d{2})(?:;1)?", path.split("/")[-1].upper())
        if not serial:
            raise IsoError("unsupported boot executable name")
        title_id = f"{serial[1]}-{serial[2]}{serial[3]}"
        with image.open(path) as executable:
            size = executable.seek(0, 2)
        match = Match(self.id, self.name, 0.95, title_id, title_id, REGIONS.get(serial[1]))
        return match, fields, path, size

    def identify(self, source: Source) -> Match | None:
        try:
            with Iso9660Image(source.path) as image:
                return self._boot(image)[0]
        except (ValueError, OSError):
            return None

    def inspect(self, source: Source) -> dict[str, Any]:
        with Iso9660Image(source.path) as image:
            match, fields, boot, size = self._boot(image)
            files = [e for e in image.entries() if not e.is_dir]
            return {
                "platform": match.platform,
                "title": match.title,
                "title_id": match.title_id,
                "region": match.region,
                "layout": "ISO9660",
                "sector_mode": image.mode,
                "system_cnf": fields,
                "boot_path": boot,
                "boot_size": size,
                "file_count": len(files),
                "total_bytes": sum(e.size for e in files),
                "files": [{"path": e.path, "size": e.size} for e in files[:5000]],
                "files_truncated": len(files) > 5000,
            }


class PS2Adapter(PlayStationAdapter):
    id = "sony.ps2"
    name = "PlayStation 2"
    boot_key = "BOOT2"
    device = "cdrom0"
