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
    from collections.abc import Callable
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
    return publish_bundle(
        staged,
        data_root=data_root,
        identity=identity,
        expected_station_count=len(_STATIONS),
        clock_now=_CLOCK_NOW,
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

    def test_republish_of_a_reconciling_identity_adopts_it(
        self, tmp_path: Path
    ) -> None:
        _publish(_staged_bundle(tmp_path, identity="same-id"), tmp_path, "same-id")
        _publish(_staged_bundle(tmp_path, identity="same-id"), tmp_path, "same-id")
        assert _orphans(tmp_path) == []

    def test_republish_of_a_non_reconciling_identity_quarantines_it(
        self, tmp_path: Path
    ) -> None:
        _publish(_staged_bundle(tmp_path, identity="same-id"), tmp_path, "same-id")
        # Corrupt the published payload so its manifest no longer reconciles.
        (
            published_dir(tmp_path, identity="same-id") / "station_grid_elevation.csv"
        ).write_text("corrupted")

        _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="fresh"),
            tmp_path,
            "same-id",
        )
        assert _orphans(tmp_path)
        # The freshly-published one reconciles again.
        republished = published_dir(tmp_path, identity="same-id")
        assert "fresh" in (republished / "station_grid_elevation.csv").read_text()

    def test_manifest_never_hashes_itself_or_the_pointer(self, tmp_path: Path) -> None:
        staged = _staged_bundle(tmp_path, identity="abc123")
        manifest = read_extraction_manifest(staged / "extraction_manifest.json")
        assert manifest is not None
        assert "extraction_manifest.json" not in manifest.payload_sha256s
        assert "CURRENT" not in manifest.payload_sha256s


class TestAdoptionIsTheHardBranch:
    """D7.3 (CORRECTED 2026-08-16, blocker) — "reconciles" was never defined,
    and the loose reading DESTROYS the good bundle: reconcile iterated only
    whatever `payload_sha256s` the existing manifest happened to list, so an
    EMPTY map reconciled vacuously and the adopt path then deleted the
    freshly generated, complete staging directory in favour of the
    incomplete published one.

    Adoption is the dangerous branch, so it must be the hard-to-satisfy one.
    Every case below must QUARANTINE the published directory and publish the
    fresh bundle instead — and in every one of them the surviving bundle
    must be the fresh one, proven by its payload marker.
    """

    def _publish_then_damage(
        self, tmp_path: Path, damage: Callable[[Path], None]
    ) -> Path:
        _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="stale"),
            tmp_path,
            "same-id",
        )
        damage(published_dir(tmp_path, identity="same-id"))
        _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="fresh"),
            tmp_path,
            "same-id",
        )
        return published_dir(tmp_path, identity="same-id")

    def _assert_fresh_survived(self, final_dir: Path, data_root: Path) -> None:
        assert _orphans(data_root), "the damaged published bundle was ADOPTED"
        assert "fresh" in (final_dir / "station_grid_elevation.csv").read_text()

    def test_empty_payload_map_does_not_reconcile(self, tmp_path: Path) -> None:
        def damage(directory: Path) -> None:
            _write_manifest(directory, identity="same-id", payload_sha256s={})

        self._assert_fresh_survived(
            self._publish_then_damage(tmp_path, damage), tmp_path
        )

    def test_manifest_identity_must_equal_the_directory_name(
        self, tmp_path: Path
    ) -> None:
        def damage(directory: Path) -> None:
            _write_manifest(
                directory,
                identity="some-other-identity",
                payload_sha256s=_payload_sha256s(directory),
            )

        self._assert_fresh_survived(
            self._publish_then_damage(tmp_path, damage), tmp_path
        )

    def test_a_missing_payload_key_does_not_reconcile(self, tmp_path: Path) -> None:
        def damage(directory: Path) -> None:
            partial = _payload_sha256s(directory)
            partial.pop("operator_sensitivity.csv")
            _write_manifest(directory, identity="same-id", payload_sha256s=partial)

        self._assert_fresh_survived(
            self._publish_then_damage(tmp_path, damage), tmp_path
        )

    def test_an_extra_payload_key_does_not_reconcile(self, tmp_path: Path) -> None:
        def damage(directory: Path) -> None:
            extra = _payload_sha256s(directory)
            (directory / "stray.csv").write_text("a\n1\n")
            extra["stray.csv"] = checksum_file(directory / "stray.csv")
            _write_manifest(directory, identity="same-id", payload_sha256s=extra)

        self._assert_fresh_survived(
            self._publish_then_damage(tmp_path, damage), tmp_path
        )

    def test_a_bundle_failing_the_d9_validator_does_not_reconcile(
        self, tmp_path: Path
    ) -> None:
        """Hashes all match, keys all present — but the series carries the
        wrong station count. A published bundle is held to the identical
        standard as a fresh one (D7.3 clause 4)."""

        def damage(directory: Path) -> None:
            import numpy as np
            import xarray as xr

            valid_time = np.array(["2021-01-01T00"], dtype="datetime64[ns]")
            xr.Dataset(
                {
                    "precipitation_mm_per_h": (
                        ["station", "valid_time"],
                        np.zeros((1, 1), dtype=np.float32),
                    )
                },
                coords={"station": ["alpha"], "valid_time": valid_time},
            ).to_netcdf(directory / "series_nearest.nc", engine="h5netcdf")
            _write_manifest(
                directory,
                identity="same-id",
                payload_sha256s=_payload_sha256s(directory),
            )

        self._assert_fresh_survived(
            self._publish_then_damage(tmp_path, damage), tmp_path
        )

    def test_staging_is_never_discarded_before_adoption_succeeds(
        self, tmp_path: Path
    ) -> None:
        """The ordering rule: validate-then-discard, not discard-then-adopt.
        If the published bundle is unusable, the fresh one must still be
        there to publish."""
        _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="stale"),
            tmp_path,
            "same-id",
        )
        published = published_dir(tmp_path, identity="same-id")
        for name in D9_PAYLOAD_FILES:
            (published / name).unlink()
        _write_manifest(published, identity="same-id", payload_sha256s={})

        final_dir = _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="fresh"),
            tmp_path,
            "same-id",
        )
        assert sorted(p.name for p in final_dir.iterdir()) == sorted(
            [*D9_PAYLOAD_FILES, "extraction_manifest.json"]
        )
