from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from rrl.adapters.registry import AdapterError, check, identify
from rrl.api import API_VERSION, Match, Source


class Good:
    id: ClassVar[str] = "test.good"
    name: ClassVar[str] = "Good"
    version: ClassVar[str] = "1"
    api_version: ClassVar[int] = API_VERSION
    capabilities: ClassVar[frozenset[str]] = frozenset({"identify", "inspect"})

    def identify(self, source: Source) -> Match | None:
        return Match(self.id, "test", 0.5)

    def inspect(self, source: Source) -> dict[str, Any]:
        return {}


class Liar(Good):
    id = "test.liar"
    capabilities = frozenset({"identify", "inspect"})
    inspect = None  # type: ignore[assignment]  # claims inspect, doesn't have it


class Planned(Good):
    capabilities = frozenset({"identify", "rebuild"})  # known, but not in this API version


class Future(Good):
    api_version = API_VERSION + 1


class Crashy(Good):
    id = "test.crashy"

    def identify(self, source: Source) -> Match | None:
        raise ValueError("bad plugin")


def test_honest_adapter_passes() -> None:
    check(Good())


@pytest.mark.parametrize("cls", [Liar, Planned, Future])
def test_dishonest_or_incompatible_adapters_rejected(cls: type) -> None:
    with pytest.raises(AdapterError):
        check(cls())


def test_identify_isolates_crashing_adapter() -> None:
    src = Source(Path("/dev/null"), "0" * 64, 0)
    matches = identify(src, [Crashy(), Good()])  # type: ignore[list-item]
    assert [m.adapter_id for m in matches] == ["test.good"]
