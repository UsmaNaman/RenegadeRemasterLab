"""Adapter discovery via entry points, with capability checks and failure isolation."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any

from rrl.api import API_VERSION, KNOWN_CAPABILITIES, Adapter, Extractor, Inspector, Match, Source

ENTRY_POINT_GROUP = "rrl.adapters"

# Capabilities callable in this API version. A capability listed in KNOWN_CAPABILITIES
# but not here is planned, and adapters may not claim it yet.
CAPABILITY_PROTOCOLS: dict[str, type] = {"inspect": Inspector, "extract": Extractor}

log = logging.getLogger(__name__)


class AdapterError(Exception):
    pass


def check(adapter: Any) -> None:
    """Raise AdapterError unless the adapter honestly implements what it declares."""
    for attr in ("id", "name", "version", "api_version", "capabilities"):
        if not hasattr(adapter, attr):
            raise AdapterError(f"{type(adapter).__name__} is missing {attr!r}")
    if adapter.api_version != API_VERSION:
        raise AdapterError(
            f"{adapter.id}: built for API v{adapter.api_version}, RRL provides v{API_VERSION}"
        )
    caps = adapter.capabilities
    if "identify" not in caps or not callable(getattr(adapter, "identify", None)):
        raise AdapterError(f"{adapter.id}: every adapter must implement identify")
    for cap in caps - {"identify"}:
        if cap not in KNOWN_CAPABILITIES:
            raise AdapterError(f"{adapter.id}: unknown capability {cap!r}")
        proto = CAPABILITY_PROTOCOLS.get(cap)
        if proto is None:
            raise AdapterError(f"{adapter.id}: {cap!r} is not available in API v{API_VERSION}")
        if not isinstance(adapter, proto):
            raise AdapterError(f"{adapter.id}: declares {cap!r} but does not implement it")


def load_adapters() -> tuple[list[Adapter], dict[str, str]]:
    """Return (working adapters, {entry point name: error}). A broken plugin never crashes RRL."""
    adapters: list[Adapter] = []
    errors: dict[str, str] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            adapter = ep.load()()
            check(adapter)
        except Exception as exc:  # third-party code: isolate any failure
            errors[ep.name] = f"{type(exc).__name__}: {exc}"
            log.warning("adapter %s disabled: %s", ep.name, errors[ep.name])
            continue
        adapters.append(adapter)
    return adapters, errors


def identify(source: Source, adapters: list[Adapter]) -> list[Match]:
    """Ask every adapter; return matches, most confident first."""
    matches: list[Match] = []
    for adapter in adapters:
        try:
            match = adapter.identify(source)
        except Exception:
            log.exception("adapter %s failed during identify", adapter.id)
            continue
        if match is not None:
            matches.append(match)
    return sorted(matches, key=lambda m: m.confidence, reverse=True)


def by_id(adapters: list[Adapter], adapter_id: str) -> Adapter:
    for adapter in adapters:
        if adapter.id == adapter_id:
            return adapter
    raise AdapterError(f"no adapter with id {adapter_id!r}")
