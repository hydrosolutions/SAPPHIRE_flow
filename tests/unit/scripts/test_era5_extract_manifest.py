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

from scripts.dhm_precip.era5_errors import ExtractionPostConditionError
from scripts.dhm_precip.era5_extract_manifest import (
    D9_PAYLOAD_FILES,
    ExtractionManifest,
    checksum_file,
    extraction_identity,
    points_root,
    prepare_staging_dir,
    publish_bundle,
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
    """A REAL D9 payload set — the P4a reconcile check AND the full D9
    schema validator (station uniqueness/equality, required columns/enum
    values, series dims/dtype/time-axis/encoding/attributes) both run the
    same reopen-and-validate bundle validator the staging path must pass,
    so a two-column stub is no longer sufficient (that stub is exactly what
    `TestPublishEnforcesFullD9Schema` below proves used to publish)."""
    import numpy as np
    import polars as pl
    import xarray as xr

    valid_time = np.array(
        ["2021-01-01T00", "2021-01-01T01", "2021-01-01T02"], dtype="datetime64[ns]"
    )
    values = np.zeros((len(_STATIONS), valid_time.size), dtype=np.float32)
    encoding = {
        "precipitation_mm_per_h": {
            "dtype": "float32",
            "zlib": True,
            "complevel": 4,
            "_FillValue": float("nan"),
        },
        "valid_time": {"units": "hours since 1970-01-01 00:00:00", "dtype": "int64"},
    }
    for name in ("series_nearest.nc", "series_bilinear.nc"):
        ds = xr.Dataset(
            {"precipitation_mm_per_h": (["station", "valid_time"], values)},
            coords={"station": list(_STATIONS), "valid_time": valid_time},
        )
        ds["valid_time"].attrs["timezone"] = "UTC"
        ds.to_netcdf(directory / name, engine="h5netcdf", encoding=encoding)
    pl.DataFrame(
        {
            "station": list(_STATIONS),
            "lat": [26.0, 26.1],
            "lon": [85.0, 85.1],
            "grid_lat": [26.0, 26.1],
            "grid_lon": [85.0, 85.1],
            "grid_i": [0, 1],
            "grid_j": [0, 1],
            "offset_km": [0.0, 0.0],
            "station_elev_m": [1500.0, 1900.0],
            "station_elevation_datum": ["UNKNOWN", "UNKNOWN"],
            "orography_elev_m": [1490.0, 1880.0],
            "orography_elevation_datum": ["LOCAL_MSL", "LOCAL_MSL"],
            "orography_source": ["MODEL_OROGRAPHY", "MODEL_OROGRAPHY"],
            "orography_product_id": ["p", "p"],
            "orography_product_version": ["1", "1"],
            "elev_mismatch_m": [10.0, 20.0],
            "datum_reconciled": ["UNRECONCILED", "UNRECONCILED"],
            "shared_cell_id": ["0_0", "1_1"],
            "stations_in_cell": [1, 1],
            "marker": [marker, marker],
        }
    ).write_csv(directory / "station_grid_elevation.csv")
    pl.DataFrame(
        {
            "scope": ["ACROSS_STATION"],
            "station": [None],
            "season": ["ALL"],
            "statistic": ["QUANTILE"],
            "quantile": [0.5],
            "nearest_value": [1.0],
            "bilinear_value": [1.0],
            "delta_absolute": [0.0],
            "delta_unit": ["MM_PER_H"],
            "ratio": [1.0],
            "n_hours_common_finite": [3],
            "n_hours_excluded": [0],
            "n_wet_nearest": [1],
            "n_wet_bilinear": [1],
            "sign_agreement_fraction": [1.0],
        }
    ).write_csv(directory / "operator_sensitivity.csv")


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
        staged, data_root=data_root, identity=identity, expected_station_count=2
    )


def _marker_of(directory: Path) -> str:
    import polars as pl

    return (
        pl.read_csv(directory / "station_grid_elevation.csv")["marker"].unique().item()
    )


def _published_dirs(data_root: Path) -> list[str]:
    root = points_root(data_root)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != ".staging")


class TestPublishBundleMechanics:
    def test_publish_creates_a_numbered_directory_named_by_the_identity(
        self, tmp_path: Path
    ) -> None:
        staged = _staged_bundle(tmp_path, identity="abc123")
        final_dir = _publish(staged, tmp_path, "abc123")
        assert final_dir.name == "0000-abc123"
        assert final_dir.exists()
        for name in D9_PAYLOAD_FILES:
            assert (final_dir / name).exists(), name

    def test_manifest_never_hashes_itself(self, tmp_path: Path) -> None:
        staged = _staged_bundle(tmp_path, identity="abc123")
        manifest = read_extraction_manifest(staged / "extraction_manifest.json")
        assert manifest is not None
        assert "extraction_manifest.json" not in manifest.payload_sha256s


class TestPerRunUniquePublication:
    """P1/P1a/P2 (redesigned 2026-08-17) — **there is no adoption, no
    quarantine and no `CURRENT` pointer.** Every run publishes into a fresh,
    per-run-unique numbered directory; every previous bundle is left
    untouched, and nothing is ever renamed after the fact. Three review
    rounds each found a blocker inside the old "adopt if the manifest
    reconciles" / quarantine design; the redesign removes the branch and the
    window instead of patching either again.
    """

    def test_rerun_with_the_same_identity_gets_a_new_run_number(
        self, tmp_path: Path
    ) -> None:
        first = _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="first"),
            tmp_path,
            "same-id",
        )
        second = _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="second"),
            tmp_path,
            "same-id",
        )
        assert first.name == "0000-same-id"
        assert second.name == "0001-same-id"
        # The first bundle survives completely untouched — nothing renamed,
        # nothing quarantined, nothing deleted.
        assert first.exists()
        assert _marker_of(first) == "first"
        assert _marker_of(second) == "second"
        assert _published_dirs(tmp_path) == ["0000-same-id", "0001-same-id"]
        # No `.orphan-*` path is ever created — the round-3 window is
        # unrepresentable now, tested here by absence.
        assert not any("orphan" in name for name in _published_dirs(tmp_path))

    def test_different_identities_each_get_their_own_numbered_directory(
        self, tmp_path: Path
    ) -> None:
        first = _publish(_staged_bundle(tmp_path, identity="id-a"), tmp_path, "id-a")
        second = _publish(_staged_bundle(tmp_path, identity="id-b"), tmp_path, "id-b")
        assert first.name == "0000-id-a"
        assert second.name == "0001-id-b"

    def test_there_is_no_current_pointer_file(self, tmp_path: Path) -> None:
        _publish(_staged_bundle(tmp_path, identity="abc123"), tmp_path, "abc123")
        assert not (points_root(tmp_path) / "CURRENT").exists()

    def test_run_number_allocation_retries_past_a_concurrent_reservation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P1a — allocation must be robust to a race: a concurrent winner
        can reserve a number AFTER this process's scan already ran (the scan
        is only an optimisation, per P1a). Simulate that by stubbing the
        scan to report no existing numbers while `0000-abc123` already
        exists on disk. The allocator's `mkdir(exist_ok=False)` must hit
        `FileExistsError`, retry to `0001-abc123`, succeed, and never touch
        the concurrently-reserved directory."""
        import scripts.dhm_precip.era5_extract_manifest as manifest_mod

        manifest_mod.points_root(tmp_path).mkdir(parents=True, exist_ok=True)
        (manifest_mod.points_root(tmp_path) / "0000-abc123").mkdir()
        monkeypatch.setattr(manifest_mod, "_existing_run_numbers", lambda _root: [])

        final_dir = _publish(
            _staged_bundle(tmp_path, identity="abc123"), tmp_path, "abc123"
        )
        assert final_dir.name == "0001-abc123"
        # The concurrently-reserved directory is untouched (still empty).
        assert (
            list((manifest_mod.points_root(tmp_path) / "0000-abc123").iterdir()) == []
        )


class TestPublishRefusesAnInvalidBundle:
    """P4/P4a — validation moves INSIDE `publish_bundle`, applying exactly
    the predicate discovery would apply. A staging directory that fails
    validation is never allocated a numbered directory at all — nothing is
    published, and no partial bundle is left where discovery could find it.
    """

    def test_publish_refuses_a_malformed_staged_bundle(self, tmp_path: Path) -> None:
        staged = _staged_bundle(tmp_path, identity="broken")
        (staged / "series_nearest.nc").unlink()
        with pytest.raises(ExtractionPostConditionError, match="series_nearest.nc"):
            _publish(staged, tmp_path, "broken")
        # Nothing was published — no numbered directory was ever created.
        assert _published_dirs(tmp_path) == []

    def test_publish_refuses_a_payload_modified_after_its_hash_was_computed(
        self, tmp_path: Path
    ) -> None:
        """P4a — the manifest's `payload_sha256s` must reconcile against the
        actual file bytes. A payload edited after `write_extraction_manifest`
        ran must fail publication, not merely a later discovery read.

        The tampering here preserves every D9-required column/value (only
        the extra, non-required `marker` column changes) so this exercises
        the P4a sha256 reconciliation specifically, not the schema checks
        covered separately by `TestPublishEnforcesFullD9Schema`."""
        import polars as pl

        staged = _staged_bundle(tmp_path, identity="tampered")
        frame = pl.read_csv(staged / "station_grid_elevation.csv")
        frame = frame.with_columns(pl.lit("tampered").alias("marker"))
        frame.write_csv(staged / "station_grid_elevation.csv")
        with pytest.raises(
            ExtractionPostConditionError, match="station_grid_elevation.csv"
        ):
            _publish(staged, tmp_path, "tampered")
        assert _published_dirs(tmp_path) == []


class TestPublishEnforcesFullD9Schema:
    """BLOCKER (2026-08-17 review) — the D9 validator used to check only
    variable presence, station COUNTS and CSV readability, so a subtly
    broken writer could hash a malformed payload and publish successfully.
    Each case below passed the OLD validator (proven by stashing this fix
    and re-running); each must now be refused.

    Every tampered file is rewritten AFTER the manifest so the manifest's
    `payload_sha256s` reconcile against the tampered bytes — isolating the
    SCHEMA check under test from the separate P4a sha256-reconciliation
    check above.
    """

    def _restaged(
        self, tmp_path: Path, *, identity: str, elevation_csv_text: str
    ) -> Path:
        staging = _staged_bundle(tmp_path, identity=identity)
        (staging / "station_grid_elevation.csv").write_text(elevation_csv_text)
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        return staging

    def test_duplicate_station_rows_in_the_elevation_csv_are_refused(
        self, tmp_path: Path
    ) -> None:
        header = (
            "station,lat,lon,grid_lat,grid_lon,grid_i,grid_j,offset_km,"
            "station_elev_m,station_elevation_datum,orography_elev_m,"
            "orography_elevation_datum,orography_source,orography_product_id,"
            "orography_product_version,elev_mismatch_m,datum_reconciled,"
            "shared_cell_id,stations_in_cell\n"
        )
        row = (
            "alpha,26.0,85.0,26.0,85.0,0,0,0.0,1500.0,UNKNOWN,1490.0,"
            "LOCAL_MSL,MODEL_OROGRAPHY,p,1,10.0,UNRECONCILED,0_0,1\n"
        )
        staging = self._restaged(
            tmp_path, identity="dup-station", elevation_csv_text=header + row + row
        )
        with pytest.raises(ExtractionPostConditionError, match="duplicate"):
            _publish(staging, tmp_path, "dup-station")
        assert _published_dirs(tmp_path) == []

    def test_elevation_stations_disagreeing_with_the_series_are_refused(
        self, tmp_path: Path
    ) -> None:
        import polars as pl

        staging = _staged_bundle(tmp_path, identity="mismatched-station")
        frame = pl.read_csv(staging / "station_grid_elevation.csv")
        frame = frame.with_columns(pl.Series("station", ["alpha", "gamma"]))
        frame.write_csv(staging / "station_grid_elevation.csv")
        _write_manifest(
            staging,
            identity="mismatched-station",
            payload_sha256s=_payload_sha256s(staging),
        )
        with pytest.raises(ExtractionPostConditionError, match="station set"):
            _publish(staging, tmp_path, "mismatched-station")
        assert _published_dirs(tmp_path) == []

    def test_elevation_csv_missing_required_d9_columns_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The exact shape the OLD `_write_payload_files` fixture used
        (`station, marker` only, two of the eighteen required D9 columns) —
        proof that a subtly broken writer emitting almost none of the
        required columns used to publish successfully."""
        staging = self._restaged(
            tmp_path,
            identity="missing-columns",
            elevation_csv_text="station,marker\nalpha,x\nbeta,x\n",
        )
        with pytest.raises(ExtractionPostConditionError, match="required column"):
            _publish(staging, tmp_path, "missing-columns")
        assert _published_dirs(tmp_path) == []
