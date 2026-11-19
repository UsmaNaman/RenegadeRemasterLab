"""Original Xbox disc images (XISO or Redump layout): identify and inspect."""

from __future__ import annotations

import heapq
from typing import Any, BinaryIO, ClassVar

from rrl.adapters.xbox_original.xbe import Certificate, read_certificate
from rrl.api import API_VERSION, Match, Source
from rrl.containers.xdvdfs import LAYOUTS, XdvdfsImage, find_partition

MAX_LISTED = 5000


class XboxOriginalAdapter:
    id: ClassVar[str] = "xbox.original"
    name: ClassVar[str] = "Original Xbox"
    version: ClassVar[str] = "0.1.0"
    api_version: ClassVar[int] = API_VERSION
    capabilities: ClassVar[frozenset[str]] = frozenset({"identify", "inspect"})

    def _probe(self, f: BinaryIO) -> tuple[XdvdfsImage, Certificate] | None:
        offset = find_partition(f)
        if offset is None:
            return None
        try:
            image = XdvdfsImage(f, offset)
            with image.open("default.xbe") as xbe:
                return image, read_certificate(xbe)
        except (ValueError, OSError):  # malformed filesystem/XBE, or no default.xbe
            return None

    def _match(self, image: XdvdfsImage, cert: Certificate) -> Match:
        return Match(
            adapter_id=self.id,
            platform=self.name,
            confidence=0.9,
            title=cert.title_name or None,
            title_id=cert.title_id_str,
            region=cert.region_str,
            details={
                "partition_offset": image.partition_offset,
                "layout": LAYOUTS[image.partition_offset],
                "title_id_hex": cert.title_id_hex,
                "version": cert.version,
                "allowed_media": cert.media_flags,
            },
        )

    def identify(self, source: Source) -> Match | None:
        with source.open() as f:
            probed = self._probe(f)
            return None if probed is None else self._match(*probed)

    def inspect(self, source: Source) -> dict[str, Any]:
        with source.open() as f:
            probed = self._probe(f)
            if probed is None:
                raise ValueError(f"{source.path.name} is not an Original Xbox disc image")
            image, cert = probed
            files: list[dict[str, Any]] = []
            top: list[tuple[int, str]] = []  # min-heap of the 10 largest files
            dirs = count = total = 0
            for entry in image.entries():
                if entry.is_dir:
                    dirs += 1
                    continue
                count += 1
                total += entry.size
                heapq.heappush(top, (entry.size, entry.path))
                if len(top) > 10:
                    heapq.heappop(top)
                if len(files) < MAX_LISTED:
                    files.append({"path": entry.path, "size": entry.size})
            match = self._match(image, cert)
        largest = [{"path": p, "size": s} for s, p in sorted(top, reverse=True)]
        return {
            "platform": match.platform,
            "title": match.title,
            "title_id": match.title_id,
            "region": match.region,
            **match.details,
            "file_count": count,
            "dir_count": dirs,
            "total_bytes": total,
            "largest_files": largest,
            "files": files,
            "files_truncated": count > len(files),
        }
