"""M-A4 (Plan 171) task 3b — the transform driver. Reads local raw files
(the product year plus its D4 boundary-context neighbours), applies 3a,
trims boundary context, writes the final product atomically, reopens to
validate the D9 schema, and updates the manifest atomically. Never touches
CDS (D3): fully re-runnable against local raw files.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it.
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import structlog
import xarray as xr

from scripts.dhm_precip.era5_deaccumulate import (
    ACCUMULATION_RULE_ID,
    DEFAULT_CONSERVATION_TOLERANCE_M,
    DEFAULT_PACKING_TOLERANCE_MM,
    OUTPUT_SCHEMA_VERSION,
    convert_units,
    deaccumulate_precipitation,
    validate_output_schema,
)
from scripts.dhm_precip.era5_errors import (
    Era5MissingBoundaryContextError,
    Era5StorageError,
)
from scripts.dhm_precip.era5_manifest import (
    OperatorProvenance,
    TransformYearRecord,
    checksum_file,
    hourly_mm_dir,
    manifest_path_for,
    product_artifact_path,
    publish_atomic,
    raw_artifact_path,
    read_manifest,
    tmp_path_for,
    transform_identity,
    transform_year_is_current,
    with_transform_year,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import DEFAULT_REQUEST_SPEC, STUDY_YEARS

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

log = structlog.get_logger(__name__)

TRANSFORM_VERSION = "1"
OUTPUT_FORMAT = "netcdf4_h5netcdf"
OUTPUT_DTYPE = "float32"
_OUTPUT_ENCODING_SPEC: dict[str, object] = {"zlib": True, "complevel": 4}
_HOUR = np.timedelta64(1, "h")


def _prev_context_window_id(year: int) -> str:
    """D4 — the edge window (2019-12-31) for 2020; otherwise the whole
    previous year's own window id."""
    if year == STUDY_YEARS[0]:
        return f"{year - 1:04d}-12-31"
    return f"{year - 1:04d}"


def _next_context_window_id(year: int) -> str:
    """D4 — the edge window (2026-01-01T00) for 2025; otherwise the whole
    next year's own window id."""
    if year == STUDY_YEARS[-1]:
        return f"{year + 1:04d}-01-01T00"
    return f"{year + 1:04d}"


def transform_year(
    year: int,
    *,
    data_root: Path,
    provenance: OperatorProvenance,
    clock: Callable[[], datetime],
    tolerance_mm: float = DEFAULT_PACKING_TOLERANCE_MM,
    conservation_tolerance_m: float = DEFAULT_CONSERVATION_TOLERANCE_M,
    transform_version: str = TRANSFORM_VERSION,
    output_schema_version: str = OUTPUT_SCHEMA_VERSION,
) -> TransformYearRecord:
    if year not in STUDY_YEARS:
        raise Era5StorageError(f"year {year} outside study range {STUDY_YEARS}")

    year_window_id = f"{year:04d}"
    prev_window_id = _prev_context_window_id(year)
    next_window_id = _next_context_window_id(year)

    year_path = raw_artifact_path(year_window_id, data_root)
    prev_path = raw_artifact_path(prev_window_id, data_root)
    next_path = raw_artifact_path(next_window_id, data_root)
    for path, label in (
        (year_path, "product year"),
        (prev_path, "previous-year boundary context"),
        (next_path, "next-year boundary context"),
    ):
        if not path.exists():
            raise Era5MissingBoundaryContextError(
                f"required raw artifact for {label} window is missing: {path}"
            )

    manifest_path = manifest_path_for(data_root)
    manifest = read_manifest(manifest_path)
    if manifest is None:
        raise Era5StorageError(f"no acquisition manifest found at {manifest_path}")

    raw_sha256s: list[str] = []
    for window_id, path, label in (
        (prev_window_id, prev_path, "previous-year boundary context"),
        (year_window_id, year_path, "product year"),
        (next_window_id, next_path, "next-year boundary context"),
    ):
        record = manifest.raw_windows.get(window_id)
        if record is None or checksum_file(path) != record.sha256:
            raise Era5StorageError(
                f"raw artifact for {label} window {window_id!r} has no valid "
                "manifest entry (missing or checksum mismatch)"
            )
        raw_sha256s.append(record.sha256)

    identity = transform_identity(
        raw_sha256s=raw_sha256s,
        accumulation_rule_id=ACCUMULATION_RULE_ID,
        packing_tolerance_mm=tolerance_mm,
        units_factor=1000.0,
        output_schema_version=output_schema_version,
        transform_version=transform_version,
        output_format=OUTPUT_FORMAT,
        output_dtype=OUTPUT_DTYPE,
        output_encoding=_OUTPUT_ENCODING_SPEC,
    )

    product_path = product_artifact_path(year, data_root)
    if transform_year_is_current(
        manifest, year=year, expected_identity=identity, final_path=product_path
    ):
        log.info("era5.transform.skip_resume", year=year)
        existing = manifest.transformed_years[str(year)]
        return existing

    required_start = np.datetime64(f"{year:04d}-01-01T00:00:00")
    required_end = np.datetime64(f"{year:04d}-12-31T23:00:00")

    with (
        xr.open_dataset(prev_path) as prev_ds,
        xr.open_dataset(year_path) as year_ds,
        xr.open_dataset(next_path) as next_ds,
    ):
        combined = xr.concat(
            [prev_ds.isel(valid_time=[-1]), year_ds, next_ds.isel(valid_time=[0])],
            dim="valid_time",
        ).load()

    deaccumulated = deaccumulate_precipitation(
        combined,
        tolerance_mm=tolerance_mm,
        conservation_tolerance_m=conservation_tolerance_m,
        required_range=(required_start, required_end),
    )
    converted = convert_units(deaccumulated.dataset)
    trimmed = converted.sel(valid_time=slice(required_start, required_end))

    validate_output_schema(
        trimmed, expected_year=year, expected_area=DEFAULT_REQUEST_SPEC.area
    )

    hourly_mm_dir(data_root).mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_path_for(product_path)
    tmp_path.unlink(missing_ok=True)

    lat_size = trimmed.sizes["latitude"]
    lon_size = trimmed.sizes["longitude"]
    encoding = {
        "precipitation": {
            "dtype": OUTPUT_DTYPE,
            "zlib": True,
            "complevel": 4,
            "chunksizes": (min(24, trimmed.sizes["valid_time"]), lat_size, lon_size),
            "_FillValue": np.nan,
        },
        "valid_time": {"units": "hours since 1970-01-01 00:00:00", "dtype": "int64"},
    }
    trimmed = trimmed.copy()
    trimmed.attrs.update(
        {
            "period_ending_convention": "hour t covers t-1 -> t (UTC)",
            "accumulation_rule": ACCUMULATION_RULE_ID,
            "transform_version": transform_version,
            "output_schema_version": output_schema_version,
            "source_dataset": manifest.dataset,
        }
    )
    trimmed.to_netcdf(tmp_path, engine="h5netcdf", encoding=encoding)

    try:
        with xr.open_dataset(tmp_path, engine="h5netcdf") as reopened:
            schema_result = validate_output_schema(
                reopened.load(),
                expected_year=year,
                expected_area=DEFAULT_REQUEST_SPEC.area,
            )
        sha256 = checksum_file(tmp_path)
        publish_atomic(tmp_path, product_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    record = TransformYearRecord(
        product_year=year,
        transform_identity=identity,
        sha256=sha256,
        accumulation_convention=ACCUMULATION_RULE_ID,
        units_conversion="metres_to_mm_x1000",
        packing=deaccumulated.packing,
        non_finite_cell_count=schema_result.non_finite_cell_count,
        dropped_boundary_stamp=f"{prev_window_id}[-1],{next_window_id}[0]",
        transformed_at=clock(),
    )
    updated_manifest = with_transform_year(manifest, record)
    write_manifest_atomic(updated_manifest, manifest_path)
    log.info("era5.transform.complete", year=year, sha256=sha256)
    return record
