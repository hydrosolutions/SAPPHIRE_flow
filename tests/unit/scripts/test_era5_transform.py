"""Plan 171 task 3b — the transform driver, against on-disk fake raw files
(no CDS access; constraint 5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest
import xarray as xr

from scripts.dhm_precip.era5_errors import (
    Era5MissingBoundaryContextError,
    Era5PackingPostConditionError,
    Era5SchemaValidationError,
    Era5StorageError,
)
from scripts.dhm_precip.era5_manifest import (
    Era5ProvenanceManifest,
    OperatorProvenance,
    RawWindowRecord,
    checksum_file,
    manifest_path_for,
    product_artifact_path,
    raw_artifact_path,
    read_manifest,
    with_raw_window,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import DEFAULT_REQUEST_SPEC, expected_grid_shape
from scripts.dhm_precip.era5_transform import transform_year, validate_output_encoding

if TYPE_CHECKING:
    from pathlib import Path

_HOUR = np.timedelta64(1, "h")

# The transform driver validates against the REAL study-box grid
# (`DEFAULT_REQUEST_SPEC.area`, D9's exact-shape check) — a toy 2x2 grid
# would fail schema validation before any of these fixtures' scenarios are
# even reached, so every raw fixture in this file uses the real shape.
_LAT_COUNT, _LON_COUNT = expected_grid_shape(DEFAULT_REQUEST_SPEC.area)
_NORTH, _WEST, _SOUTH, _EAST = DEFAULT_REQUEST_SPEC.area
_LAT = np.linspace(_SOUTH, _NORTH, _LAT_COUNT)
_LON = np.linspace(_WEST, _EAST, _LON_COUNT)

_PROVENANCE = OperatorProvenance(
    cds_portal_url="https://cds.climate.copernicus.eu",
    dataset_landing_page_url="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land",
    licence_name="Licence to use Copernicus Products",
    licence_version="1.0",
    licence_accepted_at=datetime(2026, 8, 13, tzinfo=UTC),
)


def _accumulator_from_true(valid_time: np.ndarray, true_m: np.ndarray) -> np.ndarray:
    days = valid_time.astype("datetime64[D]")
    hours = ((valid_time - days) / _HOUR).astype(int)
    acc = np.empty_like(true_m)
    running = np.zeros(true_m.shape[1:], dtype=true_m.dtype)
    for i in range(valid_time.size):
        running = true_m[i].copy() if hours[i] == 1 else running + true_m[i]
        acc[i] = running
    return acc


def _write_raw(path: Path, valid_time: np.ndarray, acc_m: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ds = xr.Dataset(
        {"tp": (["valid_time", "latitude", "longitude"], acc_m.astype(np.float32))},
        coords={
            "valid_time": valid_time,
            "latitude": _LAT,
            "longitude": _LON,
        },
    )
    ds["tp"].attrs["units"] = "m"
    ds.to_netcdf(path)


def _seed_manifest(
    data_root: Path, *, window_ids_and_paths: list[tuple[str, Path]]
) -> Era5ProvenanceManifest:
    manifest = Era5ProvenanceManifest(
        dataset="reanalysis-era5-land",
        client_package_version="0.7.7",
        operator_provenance=_PROVENANCE,
    )
    for window_id, path in window_ids_and_paths:
        record = RawWindowRecord(
            window_id=window_id,
            dataset="reanalysis-era5-land",
            request_payload={"year": window_id},
            raw_request_identity=f"identity-{window_id}",
            sha256=checksum_file(path),
            client_package_version="0.7.7",
            downloaded_at=datetime(2026, 8, 13, tzinfo=UTC),
        )
        manifest = with_raw_window(manifest, record)
    write_manifest_atomic(manifest, manifest_path_for(data_root))
    return manifest


def _seed_study_year(data_root: Path, year: int, *, rng_seed: int = 0) -> None:
    """Builds ONE continuous, physically-consistent accumulator series
    spanning [Dec 31 (year-1) 01:00 .. Jan 1 (year+1) 00:00] and splits it
    into prev-context / year / next-context raw files, exactly as the real
    D4 windows would (a single boundary stamp is all the driver ever reads
    from prev/next)."""
    series_start = np.datetime64(f"{year - 1:04d}-12-31T01:00")
    series_end = np.datetime64(f"{year + 1:04d}-01-01T00:00")
    hours = int((series_end - series_start) / _HOUR) + 1
    valid_time = series_start + np.arange(hours) * _HOUR
    rng = np.random.default_rng(rng_seed)
    true_mm_1d = rng.uniform(0.1, 2.0, size=hours)
    true_mm = (
        np.broadcast_to(true_mm_1d[:, None, None], (hours, _LAT_COUNT, _LON_COUNT))
        .astype(np.float64)
        .copy()
    )
    acc_m = _accumulator_from_true(valid_time, true_mm / 1000.0)

    prev_ts = np.datetime64(f"{year - 1:04d}-12-31T23:00")
    year_start_ts = np.datetime64(f"{year:04d}-01-01T00:00")
    year_end_ts = np.datetime64(f"{year:04d}-12-31T23:00")
    next_ts = np.datetime64(f"{year + 1:04d}-01-01T00:00")

    prev_idx = int(np.where(valid_time == prev_ts)[0][0])
    year_start_idx = int(np.where(valid_time == year_start_ts)[0][0])
    year_end_idx = int(np.where(valid_time == year_end_ts)[0][0])
    next_idx = int(np.where(valid_time == next_ts)[0][0])

    prev_window_id = f"{year - 1:04d}-12-31" if year == 2020 else f"{year - 1:04d}"
    next_window_id = f"{year + 1:04d}-01-01T00" if year == 2025 else f"{year + 1:04d}"

    _write_raw(
        raw_artifact_path(f"{year:04d}", data_root),
        valid_time[year_start_idx : year_end_idx + 1],
        acc_m[year_start_idx : year_end_idx + 1],
    )
    _write_raw(
        raw_artifact_path(prev_window_id, data_root),
        valid_time[[prev_idx]],
        acc_m[[prev_idx]],
    )
    _write_raw(
        raw_artifact_path(next_window_id, data_root),
        valid_time[[next_idx]],
        acc_m[[next_idx]],
    )
    _seed_manifest(
        data_root,
        window_ids_and_paths=[
            (f"{year:04d}", raw_artifact_path(f"{year:04d}", data_root)),
            (prev_window_id, raw_artifact_path(prev_window_id, data_root)),
            (next_window_id, raw_artifact_path(next_window_id, data_root)),
        ],
    )


class TestHappyPath:
    def test_writes_valid_product_and_updates_manifest(self, tmp_path: Path) -> None:
        _seed_study_year(tmp_path, 2021)
        record = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        assert record.product_year == 2021
        product_path = product_artifact_path(2021, tmp_path)
        assert product_path.exists()
        assert checksum_file(product_path) == record.sha256

        with xr.open_dataset(product_path, engine="h5netcdf") as ds:
            assert "precipitation" in ds
            assert ds["precipitation"].attrs["units"] == "mm"
            assert ds.sizes["valid_time"] == 365 * 24

        manifest = read_manifest(manifest_path_for(tmp_path))
        assert manifest is not None
        assert "2021" in manifest.transformed_years

    def test_edge_year_2020_uses_dec2019_and_jan2026_context(
        self, tmp_path: Path
    ) -> None:
        _seed_study_year(tmp_path, 2020)
        record = transform_year(
            2020,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        assert record.product_year == 2020
        with xr.open_dataset(
            product_artifact_path(2020, tmp_path), engine="h5netcdf"
        ) as ds:
            assert ds.sizes["valid_time"] == 366 * 24  # 2020 is a leap year


class TestResume:
    def test_skips_a_completed_year(self, tmp_path: Path) -> None:
        _seed_study_year(tmp_path, 2021)
        record_a = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        product_path = product_artifact_path(2021, tmp_path)
        mtime_a = product_path.stat().st_mtime_ns

        record_b = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(
                2099, 1, 1, tzinfo=UTC
            ),  # would prove a re-run if used
        )
        assert record_b == record_a
        assert product_path.stat().st_mtime_ns == mtime_a

    def test_bumped_transform_version_forces_retransform(self, tmp_path: Path) -> None:
        _seed_study_year(tmp_path, 2021)
        record_a = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        record_b = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
            transform_version="2",
        )
        assert record_b.transform_identity != record_a.transform_identity
        assert record_b.transformed_at != record_a.transformed_at

    def test_crash_between_replace_and_manifest_update_forces_retransform(
        self, tmp_path: Path
    ) -> None:
        _seed_study_year(tmp_path, 2021)
        record = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        # Simulate the crash: the manifest entry is stale (as if the process
        # died after publish_atomic but before write_manifest_atomic) by
        # reverting the manifest to not know about this transform.
        manifest = read_manifest(manifest_path_for(tmp_path))
        assert manifest is not None
        stale_manifest = replace(manifest, transformed_years={})
        write_manifest_atomic(stale_manifest, manifest_path_for(tmp_path))

        record_b = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 15, tzinfo=UTC),
        )
        assert record_b.transformed_at == datetime(2026, 8, 15, tzinfo=UTC)
        assert record_b.sha256 == record.sha256  # same bytes, re-verified not skipped

        manifest_after = read_manifest(manifest_path_for(tmp_path))
        assert manifest_after is not None
        assert "2021" in manifest_after.transformed_years


class TestMissingBoundaryContext:
    def test_missing_neighbour_raw_file_raises(self, tmp_path: Path) -> None:
        _seed_study_year(tmp_path, 2021)
        raw_artifact_path("2020", tmp_path).unlink()

        with pytest.raises(Era5MissingBoundaryContextError):
            transform_year(
                2021,
                data_root=tmp_path,
                provenance=_PROVENANCE,
                clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            )


class TestReopenedFileSatisfiesSchema:
    def test_reopen_validates_d9_schema(self, tmp_path: Path) -> None:
        _seed_study_year(tmp_path, 2021)
        record = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        with xr.open_dataset(
            product_artifact_path(2021, tmp_path), engine="h5netcdf"
        ) as ds:
            valid_time = ds["valid_time"].values
            assert valid_time[0] == np.datetime64("2021-01-01T00:00:00")
            assert valid_time[-1] == np.datetime64("2021-12-31T23:00:00")
        assert record.non_finite_cell_count == 0


class TestPostConditionFailureLeavesNoFinalFile:
    def test_material_negative_in_source_leaves_no_product(
        self, tmp_path: Path
    ) -> None:
        _seed_study_year(tmp_path, 2021)
        year_path = raw_artifact_path("2021", tmp_path)
        with xr.open_dataset(year_path) as ds:
            loaded = ds.load()
        loaded["tp"].values[10] -= 1.0  # a material negative diff
        loaded.to_netcdf(year_path)
        _seed_manifest(
            tmp_path,
            window_ids_and_paths=[
                ("2021", year_path),
                ("2020", raw_artifact_path("2020", tmp_path)),
                ("2022", raw_artifact_path("2022", tmp_path)),
            ],
        )

        with pytest.raises(Era5PackingPostConditionError, match="material negative"):
            transform_year(
                2021,
                data_root=tmp_path,
                provenance=_PROVENANCE,
                clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            )
        assert not product_artifact_path(2021, tmp_path).exists()

    def test_failed_retransform_leaves_previous_good_file_untouched(
        self, tmp_path: Path
    ) -> None:
        _seed_study_year(tmp_path, 2021)
        good_record = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        product_path = product_artifact_path(2021, tmp_path)
        good_bytes = product_path.read_bytes()

        # Corrupt the raw source so a forced re-transform fails.
        year_path = raw_artifact_path("2021", tmp_path)
        with xr.open_dataset(year_path) as ds:
            loaded = ds.load()
        loaded["tp"].values[10] -= 1.0
        loaded.to_netcdf(year_path)
        manifest = read_manifest(manifest_path_for(tmp_path))
        assert manifest is not None
        record_2021 = RawWindowRecord(
            window_id="2021",
            dataset="reanalysis-era5-land",
            request_payload={"year": "2021"},
            raw_request_identity="identity-2021",
            sha256=checksum_file(year_path),
            client_package_version="0.7.7",
            downloaded_at=datetime(2026, 8, 13, tzinfo=UTC),
        )
        write_manifest_atomic(
            with_raw_window(manifest, record_2021), manifest_path_for(tmp_path)
        )

        with pytest.raises(Era5PackingPostConditionError, match="material negative"):
            transform_year(
                2021,
                data_root=tmp_path,
                provenance=_PROVENANCE,
                clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
                transform_version="2",  # forces the retry past the resume-skip
            )

        assert product_path.read_bytes() == good_bytes
        manifest_after = read_manifest(manifest_path_for(tmp_path))
        assert manifest_after is not None
        assert manifest_after.transformed_years["2021"] == good_record


class TestOutsideStudyRange:
    def test_year_outside_study_range_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(Era5StorageError):
            transform_year(
                2019,
                data_root=tmp_path,
                provenance=_PROVENANCE,
                clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            )


class TestProvenanceMismatchIsRejected:
    """A review finding: `transform_year`'s `provenance` parameter was
    accepted but never checked against the manifest — a stale/wrong
    `--provenance` file was silently ignored."""

    def test_wrong_provenance_is_rejected(self, tmp_path: Path) -> None:
        _seed_study_year(tmp_path, 2021)
        wrong_provenance = replace(_PROVENANCE, licence_version="2.0")
        with pytest.raises(Era5StorageError, match="provenance"):
            transform_year(
                2021,
                data_root=tmp_path,
                provenance=wrong_provenance,
                clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            )


class TestDatasetImmutability:
    """A review finding: transform only checked per-file checksums, never
    that all consumed raw records share the manifest's dataset — a stale
    top-level `dataset` field could mislabel the final product's
    `source_dataset` attr for data actually acquired under a different one."""

    def test_raw_record_dataset_mismatch_is_rejected(self, tmp_path: Path) -> None:
        _seed_study_year(tmp_path, 2021)
        manifest = read_manifest(manifest_path_for(tmp_path))
        assert manifest is not None
        mismatched = replace(
            manifest.raw_windows["2021"], dataset="reanalysis-era5-single-levels"
        )
        updated = with_raw_window(manifest, mismatched)
        write_manifest_atomic(updated, manifest_path_for(tmp_path))

        with pytest.raises(Era5StorageError, match="dataset"):
            transform_year(
                2021,
                data_root=tmp_path,
                provenance=_PROVENANCE,
                clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            )


class TestManifestWriteFailureDuringRevisionPreservesOldGood:
    """D5: a manifest-write failure must never destroy a previously-good
    checkpoint. A review finding showed the product file was replaced
    BEFORE the manifest was updated — if that later write failed, the old
    good product was already gone while the (stale, now-orphaned) old
    manifest entry remained."""

    def test_failure_after_replace_restores_old_product_and_manifest_stays_consistent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_study_year(tmp_path, 2021)
        good_record = transform_year(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        product_path = product_artifact_path(2021, tmp_path)
        good_bytes = product_path.read_bytes()

        import scripts.dhm_precip.era5_transform as era5_transform_module

        def _boom(*_a: object, **_k: object) -> None:
            raise OSError("simulated disk failure writing manifest")

        monkeypatch.setattr(era5_transform_module, "write_manifest_atomic", _boom)

        with pytest.raises(OSError, match="simulated disk failure"):
            transform_year(
                2021,
                data_root=tmp_path,
                provenance=_PROVENANCE,
                clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
                transform_version="2",  # forces a real re-transform past resume-skip
            )

        # The product file must survive: still the OLD good bytes — not
        # missing, and not silently replaced by the unrecorded new bytes.
        assert product_path.exists()
        assert product_path.read_bytes() == good_bytes
        manifest_after = read_manifest(manifest_path_for(tmp_path))
        assert manifest_after is not None
        assert manifest_after.transformed_years["2021"] == good_record
        # No orphaned backup left behind.
        assert not product_path.with_name(product_path.name + ".prev").exists()


class TestValidateOutputEncoding:
    """D9's on-disk HDF5 write parameters — a review finding noted the
    schema validator checked logical schema only (dims, dtype, coords,
    attrs), never the actual compression/chunking/fill-value/time-encoding
    D5's identity also covers. `.encoding` is only populated by a REAL
    file round-trip, so every case here writes to disk and reopens."""

    def _write_and_reopen(
        self, tmp_path: Path, *, encoding_overrides: dict[str, dict[str, object]]
    ) -> xr.Dataset:
        # `validate_output_encoding` checks chunksizes against the REAL
        # study-box grid (`_EXPECTED_CHUNKSIZES`) — the grid here must
        # match it exactly for the "correct encoding" case to actually be
        # correct. Time span stays short (48h); memory scales with time,
        # not the fixed grid.
        valid_time = np.datetime64("2021-01-01T00:00") + np.arange(48) * _HOUR
        precip = np.zeros((48, _LAT_COUNT, _LON_COUNT), dtype=np.float32)
        ds = xr.Dataset(
            {"precipitation": (["valid_time", "latitude", "longitude"], precip)},
            coords={"valid_time": valid_time, "latitude": _LAT, "longitude": _LON},
        )
        ds["precipitation"].attrs["units"] = "mm"
        encoding: dict[str, dict[str, object]] = {
            "precipitation": {
                "dtype": "float32",
                "zlib": True,
                "complevel": 4,
                "chunksizes": (24, _LAT_COUNT, _LON_COUNT),
                "_FillValue": np.nan,
            },
            "valid_time": {
                "units": "hours since 1970-01-01 00:00:00",
                "dtype": "int64",
            },
        }
        for var, overrides in encoding_overrides.items():
            encoding[var].update(overrides)
            # h5py rejects `compression_opts` (complevel) without a
            # `compression` method — drop complevel when the test disables
            # compression, so the "no compression" case is itself valid to
            # write (the assertion under test is entirely about what
            # `validate_output_encoding` does with the RESULT).
            if overrides.get("zlib") is False:
                encoding[var].pop("complevel", None)
        path = tmp_path / "probe.nc"
        ds.to_netcdf(path, engine="h5netcdf", encoding=encoding)
        with xr.open_dataset(path, engine="h5netcdf") as reopened:
            return reopened.load()

    def test_correct_encoding_passes(self, tmp_path: Path) -> None:
        ds = self._write_and_reopen(tmp_path, encoding_overrides={})
        validate_output_encoding(ds)  # no exception

    def test_wrong_complevel_rejected(self, tmp_path: Path) -> None:
        ds = self._write_and_reopen(
            tmp_path, encoding_overrides={"precipitation": {"complevel": 1}}
        )
        with pytest.raises(Era5SchemaValidationError, match="complevel"):
            validate_output_encoding(ds)

    def test_wrong_chunksizes_rejected(self, tmp_path: Path) -> None:
        ds = self._write_and_reopen(
            tmp_path, encoding_overrides={"precipitation": {"chunksizes": (1, 2, 2)}}
        )
        with pytest.raises(Era5SchemaValidationError, match="chunksizes"):
            validate_output_encoding(ds)

    def test_no_compression_rejected(self, tmp_path: Path) -> None:
        ds = self._write_and_reopen(
            tmp_path, encoding_overrides={"precipitation": {"zlib": False}}
        )
        with pytest.raises(Era5SchemaValidationError, match="zlib"):
            validate_output_encoding(ds)

    def test_wrong_fill_value_rejected(self, tmp_path: Path) -> None:
        ds = self._write_and_reopen(
            tmp_path, encoding_overrides={"precipitation": {"_FillValue": -9999.0}}
        )
        with pytest.raises(Era5SchemaValidationError, match="_FillValue"):
            validate_output_encoding(ds)

    def test_wrong_time_units_rejected(self, tmp_path: Path) -> None:
        ds = self._write_and_reopen(
            tmp_path,
            encoding_overrides={
                "valid_time": {"units": "hours since 2000-01-01 00:00:00"}
            },
        )
        with pytest.raises(Era5SchemaValidationError, match="units"):
            validate_output_encoding(ds)

    def test_wrong_time_dtype_rejected(self, tmp_path: Path) -> None:
        ds = self._write_and_reopen(
            tmp_path, encoding_overrides={"valid_time": {"dtype": "int32"}}
        )
        with pytest.raises(Era5SchemaValidationError, match="dtype"):
            validate_output_encoding(ds)
