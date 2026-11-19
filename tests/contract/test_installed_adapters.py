"""Every installed adapter must load and honestly implement what it declares."""

from __future__ import annotations

from rrl.adapters.registry import load_adapters


def test_all_installed_adapters_load_and_pass_checks() -> None:
    adapters, errors = load_adapters()
    assert errors == {}, errors
    ids = [a.id for a in adapters]
    assert len(ids) == len(set(ids)), f"duplicate adapter ids: {ids}"
