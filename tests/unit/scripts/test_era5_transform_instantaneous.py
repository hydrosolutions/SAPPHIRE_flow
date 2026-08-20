"""Plan 191 task T3 — the instantaneous (t2m, K->degC) transform DRIVER,
`transform_year_instantaneous`, against on-disk fake raw files (no CDS
access). `era5_instantaneous.py`'s own pure functions (`convert_kelvin_to_celsius`,
`validate_instantaneous_schema`, `instantaneous_identity`) are locked
red-first in `test_era5_instantaneous.py`; this file exercises the DRIVER
that wires them to the manifest/atomic-publish primitives — the D1 sibling
of `transform_year`, never `transform_year` itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest
import xarray as xr

from scripts.dhm_precip.era5_errors import Era5StorageError
from scripts.dhm_precip.era5_instantaneous import instantaneous_identity
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
from scripts.dhm_precip.era5_transform import (
    _INSTANTANEOUS_OUTPUT_ENCODING_SPEC,  # white-box identity reconstruction (D8)
    INSTANTANEOUS_OUTPUT_FORMAT,
    INSTANTANEOUS_OUTPUT_SCHEMA_VERSION,
    INSTANTANEOUS_TRANSFORM_VERSION,
    INSTANTANEOUS_UNITS_CONVERSION,
    OUTPUT_DTYPE,
    transform_year_instantaneous,
)

if TYPE_CHECKING:
    from pathlib import Path

_LAT_COUNT, _LON_COUNT = expected_grid_shape(DEFAULT_REQUEST_SPEC.area)
_NORTH, _WEST, _SOUTH, _EAST = DEFAULT_REQUEST_SPEC.area
_LAT = np.linspace(_SOUTH, _NORTH, _LAT_COUNT)
_LON = np.linspace(_WEST, _EAST, _LON_COUNT)
_HOUR = np.timedelta64(1, "h")

_PROVENANCE = OperatorProvenance(
    cds_portal_url="https://cds.climate.copernicus.eu",
    dataset_landing_page_url="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land",
    licence_name="Licence to use Copernicus Products",
    licence_version="1.0",
    licence_accepted_at=datetime(2026, 8, 13, tzinfo=UTC),
)

_REQUEST_PAYLOAD: dict[str, object] = {
    "variable": ["2m_temperature"],
    "area": list(DEFAULT_REQUEST_SPEC.area),
    "data_format": "netcdf",
    "download_format": "unarchived",
}


def _write_raw_t2m(path: Path, valid_time: np.ndarray, k_value: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    shape = (valid_time.size, _LAT_COUNT, _LON_COUNT)
    values = np.full(shape, k_value, dtype=np.float32)
    ds = xr.Dataset(
        {"t2m": (["valid_time", "latitude", "longitude"], values)},
        coords={"valid_time": valid_time, "latitude": _LAT, "longitude": _LON},
    )
    ds["t2m"].attrs["units"] = "K"
    ds.to_netcdf(path)


def _seed_year(
    data_root: Path, year: int, *, k_value_for_month: dict[int, float] | None = None
) -> dict[str, str]:
    """Writes the TWELVE monthly t2m raw artifacts for `year` — deliberately
    NOT the two D4 boundary-context edge windows the accumulator path needs:
    proving the driver never touches them is part of what this file tests.
    Returns {window_id: sha256} for the twelve months, in month order."""
    k_value_for_month = k_value_for_month or {}
    manifest = Era5ProvenanceManifest(
        dataset="reanalysis-era5-land",
        client_package_version="1.2.3",
        operator_provenance=_PROVENANCE,
        variable="2m_temperature",
    )
    sha256_by_window: dict[str, str] = {}
    for month in range(1, 13):
        next_month_start = (
            np.datetime64(f"{year:04d}-{month + 1:02d}-01T00:00")
            if month < 12
            else np.datetime64(f"{year + 1:04d}-01-01T00:00")
        )
        month_start = np.datetime64(f"{year:04d}-{month:02d}-01T00:00")
        hours = int((next_month_start - month_start) / _HOUR)
        valid_time = month_start + np.arange(hours) * _HOUR
        window_id = f"{year:04d}-{month:02d}"
        path = raw_artifact_path(window_id, data_root, variable_code="t2m")
        k_value = k_value_for_month.get(month, 260.0 + month)
        _write_raw_t2m(path, valid_time, k_value)
        sha256 = checksum_file(path)
        sha256_by_window[window_id] = sha256
        manifest = with_raw_window(
            manifest,
            RawWindowRecord(
                window_id=window_id,
                dataset="reanalysis-era5-land",
                request_payload=dict(_REQUEST_PAYLOAD),
                raw_request_identity=f"identity-{window_id}",
                sha256=sha256,
                client_package_version="1.2.3",
                downloaded_at=datetime(2026, 8, 13, tzinfo=UTC),
            ),
        )
    write_manifest_atomic(manifest, manifest_path_for(data_root))
    return sha256_by_window


class TestHappyPath:
    def test_produces_the_named_product_with_correct_variable_and_units(
        self, tmp_path: Path
    ) -> None:
        """D8 seam test: assert on what is physically on disk — the
        filename, the data-variable name and its units — not merely on the
        function's return value."""
        _seed_year(tmp_path, 2020)
        record = transform_year_instantaneous(
            2020,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
        )
        product_path = product_artifact_path(
            2020,
            tmp_path,
            variable_code="t2m",
            product_dir_name="degc",
            unit_label="degc",
        )
        assert product_path.exists()
        assert "t2m_degc" in product_path.name
        assert checksum_file(product_path) == record.sha256

        with xr.open_dataset(product_path, engine="h5netcdf") as ds:
            assert "temperature" in ds
            assert ds["temperature"].attrs["units"] == "degC"
            assert "t2m" not in ds
            assert ds.sizes["valid_time"] == 366 * 24  # 2020 is a leap year

    def test_kelvin_to_celsius_conversion_is_numerically_correct(
        self, tmp_path: Path
    ) -> None:
        _seed_year(tmp_path, 2021, k_value_for_month={1: 300.15})
        transform_year_instantaneous(
            2021,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
        )
        product_path = product_artifact_path(
            2021,
            tmp_path,
            variable_code="t2m",
            product_dir_name="degc",
            unit_label="degc",
        )
        with xr.open_dataset(product_path, engine="h5netcdf") as ds:
            january = ds["temperature"].isel(valid_time=0).values
            assert np.allclose(january, 300.15 - 273.15, atol=1e-3)

    def test_manifest_records_the_transform(self, tmp_path: Path) -> None:
        _seed_year(tmp_path, 2022)
        record = transform_year_instantaneous(
            2022,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
        )
        manifest = read_manifest(manifest_path_for(tmp_path))
        assert manifest is not None
        assert "2022" in manifest.transformed_years
        assert manifest.transformed_years["2022"] == record
        assert record.dropped_boundary_stamp is None
        assert record.units_conversion == INSTANTANEOUS_UNITS_CONVERSION


class TestD4NoBoundaryContext:
    """D4 — an instantaneous field needs no previous/next-month context.
    Proven physically: the two edge/boundary windows are never even written
    to disk, and the transform still succeeds."""

    def test_succeeds_with_no_boundary_windows_present_at_all(
        self, tmp_path: Path
    ) -> None:
        _seed_year(tmp_path, 2020)
        # The accumulator path's boundary windows for 2020 would be
        # "2019-12-31" and "2021-01" — deliberately absent here.
        assert not raw_artifact_path(
            "2019-12-31", tmp_path, variable_code="t2m"
        ).exists()
        assert not raw_artifact_path("2021-01", tmp_path, variable_code="t2m").exists()
        record = transform_year_instantaneous(
            2020,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
        )
        assert record.product_year == 2020

    def test_identity_is_built_from_exactly_the_twelve_product_month_hashes(
        self, tmp_path: Path
    ) -> None:
        """D8 — reconstructs the identity independently from the twelve raw
        sha256s actually on disk and asserts it against the identity
        `transform_year_instantaneous` actually recorded in the manifest."""
        sha256_by_window = _seed_year(tmp_path, 2023)
        record = transform_year_instantaneous(
            2023,
            data_root=tmp_path,
            provenance=_PROVENANCE,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
        )
        twelve_hashes = [sha256_by_window[f"2023-{m:02d}"] for m in range(1, 13)]
        assert len(twelve_hashes) == 12
        expected_identity = instantaneous_identity(
            raw_sha256s=twelve_hashes,
            units_conversion=INSTANTANEOUS_UNITS_CONVERSION,
            output_schema_version=INSTANTANEOUS_OUTPUT_SCHEMA_VERSION,
            transform_version=INSTANTANEOUS_TRANSFORM_VERSION,
            output_format=INSTANTANEOUS_OUTPUT_FORMAT,
            output_dtype=OUTPUT_DTYPE,
            output_encoding=_INSTANTANEOUS_OUTPUT_ENCODING_SPEC,
        )
        assert record.transform_identity == expected_identity


class TestMissingOrInvalidRawArtifacts:
    def test_a_missing_product_month_is_a_storage_error_not_boundary_context(
        self, tmp_path: Path
    ) -> None:
        """D4 — `Era5MissingBoundaryContextError` is not on this path: a
        missing raw month is reported as a plain storage defect."""
        _seed_year(tmp_path, 2024)
        raw_artifact_path("2024-06", tmp_path, variable_code="t2m").unlink()
        with pytest.raises(Era5StorageError, match="2024-06"):
            transform_year_instantaneous(
                2024,
                data_root=tmp_path,
                provenance=_PROVENANCE,
                clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
            )

    def test_a_corrupted_raw_month_fails_the_checksum_reconciliation(
        self, tmp_path: Path
    ) -> None:
        _seed_year(tmp_path, 2025)
        path = raw_artifact_path("2025-03", tmp_path, variable_code="t2m")
        path.write_bytes(path.read_bytes() + b"\x00")
        with pytest.raises(Era5StorageError, match="checksum mismatch"):
            transform_year_instantaneous(
                2025,
                data_root=tmp_path,
                provenance=_PROVENANCE,
                clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
            )


class TestResume:
    def test_a_second_call_with_unchanged_inputs_resumes_without_rewriting(
        self, tmp_path: Path
    ) -> None:
        _seed_year(tmp_path, 2021)
        clock_calls: list[datetime] = []

        def clock() -> datetime:
            stamp = datetime(2026, 8, 20, tzinfo=UTC)
            clock_calls.append(stamp)
            return stamp

        first = transform_year_instantaneous(
            2021, data_root=tmp_path, provenance=_PROVENANCE, clock=clock
        )
        second = transform_year_instantaneous(
            2021, data_root=tmp_path, provenance=_PROVENANCE, clock=clock
        )
        assert second == first
        assert len(clock_calls) == 1  # the resumed call never reaches `clock()`
