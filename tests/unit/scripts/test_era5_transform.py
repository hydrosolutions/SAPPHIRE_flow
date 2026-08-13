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
from scripts.dhm_precip.era5_transform import transform_year

if TYPE_CHECKING:
    from pathlib import Path

_HOUR = np.timedelta64(1, "h")

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
            "latitude": np.array([26.0, 26.1]),
            "longitude": np.array([80.0, 80.1]),
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
        np.broadcast_to(true_mm_1d[:, None, None], (hours, 2, 2))
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

        with pytest.raises(Exception):  # noqa: B017, PT011 - any typed transform failure
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

        with pytest.raises(Exception):  # noqa: B017, PT011 - any typed transform failure
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
