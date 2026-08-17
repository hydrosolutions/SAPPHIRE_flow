"""Plan 174 (M-A5) task 4a — D7's `extraction_identity`, bundle publication,
and the manifest, exercised directly (cheaper than the full CLI).

M-11 (review finding): the identity test must cover EVERY declared
value-affecting input, not a subset — each parameter below is varied in
turn and must change the resulting identity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from scripts.dhm_precip.era5_extract_manifest import (
    D9_PAYLOAD_FILES,
    ExtractionManifest,
    checksum_file,
    current_pointer_path,
    extraction_identity,
    prepare_staging_dir,
    publish_bundle,
    published_dir,
    read_extraction_manifest,
    write_extraction_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path

_BASE_KWARGS: dict[str, object] = {
    "operator_id": "NEAREST",
    "coordinate_table_sha256": "a" * 64,
    "source_sha256s": ("b" * 64, "c" * 64),
    "orography_identity": "d" * 64,
    "jjas_months": (6, 7, 8, 9),
    "djf_months": (12, 1, 2),
    "mam_months": (3, 4, 5),
    "on_months": (10, 11),
    "wet_threshold_mm_per_h": 0.2,
    "wet_threshold_side": ">=",
    "zero_policy": "exclude_zero",
    "quantile_definition": "linear",
    "quantile_grid": (0.5, 0.9, 0.99),
    "station_elevation_datum": "UNKNOWN",
    "orography_elevation_datum": "LOCAL_MSL",
    "output_schema_version": "1",
    "output_format": "netcdf4_h5netcdf",
    "output_dtype": "float32",
    "output_encoding": {
        "valid_time": {"units": "hours since 1970-01-01 00:00:00", "dtype": "int64"}
    },
}


class TestExtractionIdentityCoversEveryDeclaredInput:
    """M-11 — each field D7 declares as part of the identity, varied alone,
    must change the digest. A broken implementation that forgets to hash
    one of these fields would pass a narrower test; this one would not."""

    def test_base_case_is_reproducible(self) -> None:
        assert extraction_identity(**_BASE_KWARGS) == extraction_identity(
            **_BASE_KWARGS
        )

    @pytest.mark.parametrize(
        ("field", "override"),
        [
            ("operator_id", "BILINEAR"),
            ("coordinate_table_sha256", "z" * 64),
            ("source_sha256s", ("z" * 64,)),
            ("orography_identity", "z" * 64),
            ("jjas_months", (7, 8, 9)),
            ("djf_months", (1, 2)),
            ("mam_months", (4, 5)),
            ("on_months", (11,)),
            ("wet_threshold_mm_per_h", 0.5),
            ("wet_threshold_side", ">"),
            ("zero_policy", "include_zero"),
            ("quantile_definition", "nearest"),
            ("quantile_grid", (0.5,)),
            ("station_elevation_datum", "EGM2008"),
            ("orography_elevation_datum", "EGM96"),
            ("output_schema_version", "2"),
            ("output_format", "netcdf4_scipy"),
            ("output_dtype", "float64"),
            ("output_encoding", {"valid_time": {"units": "x", "dtype": "int32"}}),
            ("extraction_code_version", "2"),
        ],
    )
    def test_changing_the_field_alone_changes_the_identity(
        self, field: str, override: object
    ) -> None:
        base = extraction_identity(**_BASE_KWARGS)
        changed = extraction_identity(**{**_BASE_KWARGS, field: override})
        assert base != changed, f"{field} is not covered by extraction_identity"


_STATIONS = ("alpha", "beta")
_CLOCK_NOW = datetime(2026, 8, 16, tzinfo=UTC)


def _write_payload_files(directory: Path, *, marker: str = "x") -> None:
    """A REAL D9 payload set — the D7.3 reconcile check runs the same
    reopen-and-validate bundle validator the staging path must pass, so a
    stub text file is no longer sufficient."""
    import numpy as np
    import polars as pl
    import xarray as xr

    valid_time = np.array(
        ["2021-01-01T00", "2021-01-01T01", "2021-01-01T02"], dtype="datetime64[ns]"
    )
    values = np.zeros((len(_STATIONS), valid_time.size), dtype=np.float32)
    for name in ("series_nearest.nc", "series_bilinear.nc"):
        xr.Dataset(
            {"precipitation_mm_per_h": (["station", "valid_time"], values)},
            coords={"station": list(_STATIONS), "valid_time": valid_time},
        ).to_netcdf(directory / name, engine="h5netcdf")
    pl.DataFrame(
        {"station": list(_STATIONS), "marker": [marker] * len(_STATIONS)}
    ).write_csv(directory / "station_grid_elevation.csv")
    pl.DataFrame({"scope": ["ACROSS_STATION"], "marker": [marker]}).write_csv(
        directory / "operator_sensitivity.csv"
    )


def _payload_sha256s(directory: Path) -> dict[str, str]:
    return {name: checksum_file(directory / name) for name in D9_PAYLOAD_FILES}


def _write_manifest(
    directory: Path, *, identity: str, payload_sha256s: dict[str, str]
) -> None:
    write_extraction_manifest(
        ExtractionManifest(
            orography_identity="o",
            extraction_identity=identity,
            operator_id="NEAREST",
            coordinate_table_sha256="a" * 64,
            source_sha256s=("b" * 64,),
            payload_sha256s=payload_sha256s,
            orography_spec={},
            orography_source_record={},
            accumulation_diagnostic={},
            generated_at=_CLOCK_NOW,
        ),
        directory / "extraction_manifest.json",
    )


def _staged_bundle(data_root: Path, *, identity: str, marker: str = "x") -> Path:
    staging = prepare_staging_dir(data_root, identity=identity)
    _write_payload_files(staging, marker=marker)
    _write_manifest(
        staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
    )
    return staging


def _publish(staged: Path, data_root: Path, identity: str) -> Path:
    return publish_bundle(staged, data_root=data_root, identity=identity)


def _marker_of(directory: Path) -> str:
    import polars as pl

    return (
        pl.read_csv(directory / "station_grid_elevation.csv")["marker"].unique().item()
    )


def _orphans(data_root: Path) -> list[str]:
    return [
        p.name
        for p in (data_root / "era5_land" / "points").iterdir()
        if "orphan" in p.name
    ]


class TestPublishBundleMechanics:
    def test_publish_sets_current_pointer(self, tmp_path: Path) -> None:
        staged = _staged_bundle(tmp_path, identity="abc123")
        _publish(staged, tmp_path, "abc123")
        assert current_pointer_path(tmp_path).read_text().strip() == "abc123"
        assert published_dir(tmp_path, identity="abc123").exists()

    def test_manifest_never_hashes_itself_or_the_pointer(self, tmp_path: Path) -> None:
        staged = _staged_bundle(tmp_path, identity="abc123")
        manifest = read_extraction_manifest(staged / "extraction_manifest.json")
        assert manifest is not None
        assert "extraction_manifest.json" not in manifest.payload_sha256s
        assert "CURRENT" not in manifest.payload_sha256s


class TestPublicationIsAlwaysTheFreshBundle:
    """D7.3 (owner decision 2026-08-17) — **the adopt-existing-bundle path is
    CUT**. There is no reconcile function, no adopt branch and no conditional
    discard of staging: a run always publishes the bundle it just generated
    and validated, and an existing `<identity>/` is quarantined
    unconditionally. Three review rounds each found a blocker inside that one
    branch (vacuous empty-payload reconcile; a validator too weak to bite);
    deleting the branch removes the defect class instead of moving it.
    """

    def test_an_intact_published_bundle_is_quarantined_anyway(
        self, tmp_path: Path
    ) -> None:
        """The exact case adoption existed for — the published bundle is
        complete, its hashes match and it passes the D9 validator. It is
        quarantined regardless, and the FRESH bundle is what `CURRENT`
        names."""
        _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="stale"),
            tmp_path,
            "same-id",
        )
        final_dir = _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="fresh"),
            tmp_path,
            "same-id",
        )
        assert _orphans(tmp_path) == ["same-id.orphan-0"]
        assert "fresh" in (final_dir / "station_grid_elevation.csv").read_text()
        assert current_pointer_path(tmp_path).read_text().strip() == "same-id"

    def test_quarantined_bundles_accumulate_and_are_never_deleted(
        self, tmp_path: Path
    ) -> None:
        """`.orphan-<n>` takes the first free n, and a quarantined bundle is
        left on disk untouched — a bundle is never destroyed, and the
        accumulating orphans are the visible signal the silent adopt path
        hid."""
        for marker in ("first", "second", "third"):
            _publish(
                _staged_bundle(tmp_path, identity="same-id", marker=marker),
                tmp_path,
                "same-id",
            )

        assert sorted(_orphans(tmp_path)) == [
            "same-id.orphan-0",
            "same-id.orphan-1",
        ]
        assert _marker_of(published_dir(tmp_path, identity="same-id.orphan-0")) == (
            "first"
        )
        assert _marker_of(published_dir(tmp_path, identity="same-id.orphan-1")) == (
            "second"
        )
        assert _marker_of(published_dir(tmp_path, identity="same-id")) == "third"

    def test_the_published_directory_is_exactly_the_staged_one(
        self, tmp_path: Path
    ) -> None:
        """Nothing of the prior directory survives into the published one —
        no stray file is inherited, and an incomplete prior bundle can never
        be published in place of the fresh one."""
        _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="stale"),
            tmp_path,
            "same-id",
        )
        published = published_dir(tmp_path, identity="same-id")
        (published / "stray.csv").write_text("a\n1\n")
        for name in D9_PAYLOAD_FILES:
            (published / name).unlink()

        final_dir = _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="fresh"),
            tmp_path,
            "same-id",
        )
        assert sorted(p.name for p in final_dir.iterdir()) == sorted(
            [*D9_PAYLOAD_FILES, "extraction_manifest.json"]
        )
        assert _marker_of(final_dir) == "fresh"
