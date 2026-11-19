"""The public API for RRL adapters.

Adapter authors import from this module only. Anything else in ``rrl`` is internal
and may change without notice. Changing anything here incompatibly bumps API_VERSION.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, ClassVar, Protocol, runtime_checkable

API_VERSION = 1

# Every capability RRL knows about. Only those with a Protocol in
# CAPABILITY_PROTOCOLS (rrl.adapters.registry) are callable in this API version.
KNOWN_CAPABILITIES = frozenset(
    {"identify", "inspect", "extract", "convert", "validate", "replace", "rebuild", "test"}
)


@dataclass(frozen=True)
class Source:
    """An original game image. RRL only ever opens it for reading."""

    path: Path
    sha256: str
    size: int

    def open(self) -> BinaryIO:
        return self.path.open("rb")


@dataclass(frozen=True)
class Match:
    """An adapter's claim that it recognises a source."""

    adapter_id: str
    platform: str
    confidence: float  # 0.0 to 1.0
    title: str | None = None
    title_id: str | None = None
    region: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Entry:
    """A file or directory inside a container (disc filesystem, archive...)."""

    path: str  # POSIX-style, relative to the container root, no leading slash
    size: int
    is_dir: bool


@runtime_checkable
class Container(Protocol):
    """Read-only view of a filesystem stored inside a source image."""

    def entries(self) -> Iterator[Entry]:
        """Every file and directory, recursively, parents before children."""
        ...

    def open(self, path: str) -> BinaryIO:
        """Open a file for reading. Raises FileNotFoundError if absent."""
        ...


class Adapter(Protocol):
    """Every adapter implements identify; other capabilities are declared and checked."""

    id: ClassVar[str]  # dotted, e.g. "xbox.original" or "xbox.halo2"
    name: ClassVar[str]
    version: ClassVar[str]
    api_version: ClassVar[int]
    capabilities: ClassVar[frozenset[str]]

    def identify(self, source: Source) -> Match | None: ...


@runtime_checkable
class Inspector(Protocol):
    def inspect(self, source: Source) -> dict[str, Any]:
        """Describe the source without extracting anything. Must be JSON-serialisable."""
        ...


@dataclass(frozen=True)
class Asset:
    """A file produced by extract(). RRL stores it; adapters never write files themselves."""

    logical_path: str  # relative POSIX path, e.g. "ascension/bitmaps/scenery/rock_00.png"
    data: bytes
    media_type: str  # e.g. "image/png"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Skipped:
    """Something extract() found but could not convert, with an honest reason."""

    logical_path: str
    reason: str


class SelectionError(ValueError):
    """Raised by extract() when the selection names something the source does not contain."""


@runtime_checkable
class Extractor(Protocol):
    def extract(
        self, source: Source, selection: Sequence[str] | None = None
    ) -> Iterator[Asset | Skipped]:
        """Yield assets lazily. `selection` narrows the work (adapter-defined); None means all."""
        ...
