from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from sapphire_flow.cli.import_basin_package import (
    NepalStaticResolverSnapshot,
    _nepal21_static_guard_sql,  # pyright: ignore[reportPrivateUsage]
    _raise_if_nepal_static_resolver_changed,  # pyright: ignore[reportPrivateUsage]
    _validate_nepal21_static_only_args,  # pyright: ignore[reportPrivateUsage]
)


def _package(
    tmp_path: Path,
    *,
    package_id: str = "nepal21_20261012",
    hru: str = "nepal21_20261012",
) -> Path:
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "network": "dhm",
                "package_id": package_id,
                "gateway_hru_names": [hru],
            }
        )
    )
    return package_dir


def _snapshot(*, hru: str | None = "nepal21_20261012") -> NepalStaticResolverSnapshot:
    return NepalStaticResolverSnapshot(
        station_id="00000000-0000-0000-0000-000000000001",
        nwp_source="era5_land",
        role="reanalysis",
        hru_name=hru,
        polygon_name="g_123" if hru is not None else None,
        spatial_type="basin_average" if hru is not None else None,
        band_id=None,
    )


def test_nepal21_static_only_requires_exact_package_and_hru_pins(
    tmp_path: Path,
) -> None:
    package_dir = _package(tmp_path)
    _validate_nepal21_static_only_args(
        package_dir, "nepal21_20261012", "nepal21_20261012"
    )
    with pytest.raises(ValueError, match="package_id"):
        _validate_nepal21_static_only_args(package_dir, "wrong", "nepal21_20261012")
    with pytest.raises(ValueError, match="HRU"):
        _validate_nepal21_static_only_args(package_dir, "nepal21_20261012", "wrong")


def test_nepal21_static_only_uses_static_guard_not_weather_activation() -> None:
    assert (
        _nepal21_static_guard_sql()
        == "SELECT public.sf_lock_nepal_static_guard(:tenant_id)"
    )


def test_nepal21_static_resolver_guard_allows_identical_snapshots() -> None:
    before = (_snapshot(),)
    _raise_if_nepal_static_resolver_changed(before=before, after=before)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ((), (_snapshot(),), "added"),
        ((_snapshot(),), (), "removed"),
        ((_snapshot(hru="old"),), (_snapshot(hru="new"),), "changed"),
    ],
)
def test_nepal21_static_resolver_guard_rejects_active_consumer_changes(
    before: tuple[NepalStaticResolverSnapshot, ...],
    after: tuple[NepalStaticResolverSnapshot, ...],
    expected: str,
) -> None:
    with pytest.raises(RuntimeError, match=expected):
        _raise_if_nepal_static_resolver_changed(before=before, after=after)
