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

import os
from contextlib import ExitStack
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
    Era5SchemaValidationError,
    Era5StorageError,
)
from scripts.dhm_precip.era5_instantaneous import (
    convert_kelvin_to_celsius,
    instantaneous_identity,
    validate_instantaneous_schema,
)
from scripts.dhm_precip.era5_manifest import (
    OperatorProvenance,
    TransformYearRecord,
    checksum_file,
    hourly_mm_dir,
    manifest_path_for,
    product_artifact_path,
    product_dir,
    publish_atomic,
    raw_artifact_path,
    read_manifest,
    tmp_path_for,
    transform_identity,
    transform_year_is_current,
    with_transform_year,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import (
    DEFAULT_REQUEST_SPEC,
    STUDY_YEARS,
    expected_grid_shape,
    variable_code,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

log = structlog.get_logger(__name__)

TRANSFORM_VERSION = "1"
OUTPUT_FORMAT = "netcdf4_h5netcdf"
OUTPUT_DTYPE = "float32"
_HOUR = np.timedelta64(1, "h")

# The single source of truth for BOTH the actual `to_netcdf(encoding=...)`
# call below AND the D5 resume identity (`transform_identity`'s
# `output_encoding`) — a review finding showed these had drifted apart
# (identity carried only zlib/complevel while the real write also pinned
# chunksizes/fill-value/time-units), so a changed encoding could silently
# resume-serve a stale product. The study box is fixed (D2), so the grid
# shape — and therefore the chunk shape — is a module-level constant, not
# something computed per-file from whatever happens to be on disk.
_CHUNK_HOURS = 24
_TIME_ENCODING_UNITS = "hours since 1970-01-01 00:00:00"
# `to_netcdf`'s own units string above round-trips through h5netcdf with
# trailing zero time-of-day dropped ("...1970-01-01 00:00:00" ->
# "...1970-01-01" on reopen) — `validate_output_encoding` below checks the
# REOPENED file, so it matches on this prefix rather than the exact string
# used to WRITE it.
_TIME_ENCODING_UNITS_PREFIX = "hours since 1970-01-01"
_TIME_ENCODING_DTYPE = "int64"
_FILL_VALUE = float("nan")
_EXPECTED_LAT_COUNT, _EXPECTED_LON_COUNT = expected_grid_shape(
    DEFAULT_REQUEST_SPEC.area
)
_EXPECTED_CHUNKSIZES = (_CHUNK_HOURS, _EXPECTED_LAT_COUNT, _EXPECTED_LON_COUNT)
_EXPECTED_COMPLEVEL = 4

_OUTPUT_ENCODING_SPEC: dict[str, dict[str, object]] = {
    "precipitation": {
        "dtype": OUTPUT_DTYPE,
        "zlib": True,
        "complevel": _EXPECTED_COMPLEVEL,
        "chunksizes": _EXPECTED_CHUNKSIZES,
        "_FillValue": _FILL_VALUE,
    },
    "valid_time": {"units": _TIME_ENCODING_UNITS, "dtype": _TIME_ENCODING_DTYPE},
}

# --- Plan 191 T3 — the instantaneous (K->degC) transform driver. D1: a
# SEPARATE path from `transform_year` above, sharing only the storage
# primitives (atomic publish, .prev rollback, manifest reconciliation) and
# the grid-derived encoding constants (`_EXPECTED_CHUNKSIZES` etc. — those
# describe the shared study-box grid, not accumulator behaviour). No
# deaccumulation, no boundary context (D4), no packing/conservation
# accounting.
INSTANTANEOUS_TRANSFORM_VERSION = "1"
INSTANTANEOUS_OUTPUT_SCHEMA_VERSION = "1"
INSTANTANEOUS_OUTPUT_FORMAT = "netcdf4_h5netcdf"
INSTANTANEOUS_UNITS_CONVERSION = "kelvin_to_celsius_subtract_273.15"

_INSTANTANEOUS_OUTPUT_ENCODING_SPEC: dict[str, dict[str, object]] = {
    "temperature": {
        "dtype": OUTPUT_DTYPE,
        "zlib": True,
        "complevel": _EXPECTED_COMPLEVEL,
        "chunksizes": _EXPECTED_CHUNKSIZES,
        "_FillValue": _FILL_VALUE,
    },
    "valid_time": {"units": _TIME_ENCODING_UNITS, "dtype": _TIME_ENCODING_DTYPE},
}


def validate_output_encoding(ds: xr.Dataset) -> None:
    """D9 — the on-disk HDF5 write parameters (compression, chunking, fill
    value, time units/dtype) that D5's resume identity also covers (via
    `_OUTPUT_ENCODING_SPEC`, fed to `transform_identity`). Checked ONLY on a
    REOPENED (from-disk) dataset: `.encoding` is populated by xarray at open
    time, not on an in-memory (pre-write) dataset — calling this before the
    file is written would find every encoding dict empty. A review finding
    noted `validate_output_schema` covered logical schema (dims, dtype,
    coords, attrs) but nothing about how the bytes are actually packed on
    disk, so a materially different encoding could silently pass."""
    var = ds["precipitation"]
    enc = var.encoding
    if not enc.get("zlib"):
        raise Era5SchemaValidationError(
            "'precipitation' is not zlib-compressed on disk"
        )
    if enc.get("complevel") != _EXPECTED_COMPLEVEL:
        raise Era5SchemaValidationError(
            f"'precipitation' complevel {enc.get('complevel')!r} != "
            f"{_EXPECTED_COMPLEVEL!r}"
        )
    chunksizes = enc.get("chunksizes")
    if chunksizes is None or tuple(chunksizes) != _EXPECTED_CHUNKSIZES:
        raise Era5SchemaValidationError(
            f"'precipitation' chunksizes {chunksizes!r} != {_EXPECTED_CHUNKSIZES!r}"
        )
    fill_value = enc.get("_FillValue")
    if fill_value is None or not bool(np.isnan(fill_value)):
        raise Era5SchemaValidationError(
            f"'precipitation' _FillValue {fill_value!r} is not NaN"
        )
    time_enc = ds["valid_time"].encoding
    units = str(time_enc.get("units", ""))
    if not units.startswith(_TIME_ENCODING_UNITS_PREFIX):
        raise Era5SchemaValidationError(
            f"'valid_time' on-disk units {units!r} do not start with "
            f"{_TIME_ENCODING_UNITS_PREFIX!r} (UTC epoch encoding expected)"
        )
    if str(time_enc.get("dtype")) != _TIME_ENCODING_DTYPE:
        raise Era5SchemaValidationError(
            f"'valid_time' on-disk dtype {time_enc.get('dtype')!r} != "
            f"{_TIME_ENCODING_DTYPE!r}"
        )


def _prev_context_window_id(year: int) -> str:
    """D4 (corrected 2026-08-17) — the edge window (2019-12-31) for 2020;
    otherwise DECEMBER of the previous year. Acquisition is monthly now, so
    the neighbour on disk is a month, not a year; the driver still reads
    only its final stamp (23 UTC, 31 Dec)."""
    if year == STUDY_YEARS[0]:
        return f"{year - 1:04d}-12-31"
    return f"{year - 1:04d}-12"


def _next_context_window_id(year: int) -> str:
    """D4 (corrected 2026-08-17) — the edge window (2026-01-01T00) for 2025;
    otherwise JANUARY of the next year, of which only the first stamp
    (00 UTC, 1 Jan — the closing stamp of 31 December's accumulation day) is
    read."""
    if year == STUDY_YEARS[-1]:
        return f"{year + 1:04d}-01-01T00"
    return f"{year + 1:04d}-01"


def _product_year_window_ids(year: int) -> tuple[str, ...]:
    """D4 (corrected 2026-08-17) — the TWELVE monthly raw artifacts a
    product year is assembled from. The transform stays year-granular; only
    what it reads changes. Chronological order matters: the concatenated
    series must be contiguous and strictly hourly, which is what makes every
    MONTH seam (`23 -> 00 -> 01`, closing at 00 UTC in the following
    month's file) fall under the same conservation post-condition as the two
    year edges."""
    return tuple(f"{year:04d}-{month:02d}" for month in range(1, 13))


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

    month_window_ids = _product_year_window_ids(year)
    prev_window_id = _prev_context_window_id(year)
    next_window_id = _next_context_window_id(year)

    # Chronological, and the ONLY place the consumed set is enumerated —
    # existence, manifest reconciliation, identity and the concat below all
    # walk this same ordered list, so a month can never be checked but not
    # read (or read but not checked).
    consumed: tuple[tuple[str, str], ...] = (
        (prev_window_id, "previous-month boundary context"),
        *((window_id, "product month") for window_id in month_window_ids),
        (next_window_id, "next-month boundary context"),
    )

    # The manifest must be read BEFORE `consumed_paths` is built: the raw
    # artifacts' variable code is recorded on the manifest (`manifest.variable`,
    # set once at acquisition), not defaulted, so resolving the same paths
    # `acquire_window` actually wrote requires knowing it first.
    manifest_path = manifest_path_for(data_root)
    manifest = read_manifest(manifest_path)
    if manifest is None:
        raise Era5StorageError(f"no acquisition manifest found at {manifest_path}")
    if manifest.operator_provenance != provenance:
        # A stale/wrong `--provenance` file must be caught, not silently
        # accepted: the manifest's provenance is set once, by 2a's
        # acquisition (D11/D15), and transforming under a DIFFERENT
        # provenance than what was actually acquired would mislabel the
        # product's licence/portal record.
        raise Era5StorageError(
            f"--provenance does not match the acquisition manifest at "
            f"{manifest_path}: {provenance} != {manifest.operator_provenance}"
        )

    consumed_variable_code = variable_code(manifest.variable)
    consumed_paths = {
        window_id: raw_artifact_path(
            window_id, data_root, variable_code=consumed_variable_code
        )
        for window_id, _ in consumed
    }
    for window_id, label in consumed:
        path = consumed_paths[window_id]
        if not path.exists():
            raise Era5MissingBoundaryContextError(
                f"required raw artifact for {label} window {window_id!r} is "
                f"missing: {path}"
            )

    raw_sha256s: list[str] = []
    request_family: dict[str, object] | None = None
    request_family_label = ""
    for window_id, label in consumed:
        path = consumed_paths[window_id]
        record = manifest.raw_windows.get(window_id)
        if record is None or checksum_file(path) != record.sha256:
            raise Era5StorageError(
                f"raw artifact for {label} window {window_id!r} has no valid "
                "manifest entry (missing or checksum mismatch)"
            )
        if record.dataset != manifest.dataset:
            # The final product's `source_dataset` attr (below) is taken
            # from the manifest's TOP-LEVEL `dataset` field — if any
            # consumed raw window was actually acquired under a different
            # dataset (e.g. the manifest's top-level field went stale after
            # a manual edit, or windows were acquired across a dataset
            # change), that label would be wrong for this window's data.
            raise Era5StorageError(
                f"raw artifact for {label} window {window_id!r} was acquired "
                f"under dataset {record.dataset!r}, but the manifest's "
                f"dataset is {manifest.dataset!r} — refusing to mix datasets "
                "within one transformed product"
            )
        # Beyond the dataset, the three consumed windows must belong to the
        # same REQUEST FAMILY. A review finding: checking `dataset` alone still
        # permits stitching a product out of windows acquired over different
        # areas, variables or formats — e.g. a boundary-context window fetched
        # for a smaller box, which would silently produce a product whose edges
        # came from a different extraction than its interior. The payload
        # carries these, so reconcile them rather than trusting the caller.
        family = {
            key: record.request_payload.get(key)
            for key in ("area", "variable", "data_format", "download_format")
        }
        if request_family is None:
            request_family = family
            request_family_label = label
        elif family != request_family:
            differing = sorted(k for k in family if family[k] != request_family.get(k))
            raise Era5StorageError(
                f"raw artifact for {label} window {window_id!r} was acquired "
                f"with a different request family than the {request_family_label} "
                f"window — {differing} differ. Refusing to build one product "
                "from inconsistent extractions"
            )
        raw_sha256s.append(record.sha256)

    identity = transform_identity(
        raw_sha256s=raw_sha256s,
        accumulation_rule_id=ACCUMULATION_RULE_ID,
        packing_tolerance_mm=tolerance_mm,
        conservation_tolerance_m=conservation_tolerance_m,
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

    # D4 (corrected 2026-08-17): fourteen files, not three — one leading
    # context stamp, the year's twelve monthly artifacts, one trailing
    # context stamp. They are concatenated into ONE continuous hourly series
    # BEFORE deaccumulation, which is what makes every month seam behave
    # exactly like the year edges did: `deaccumulate_precipitation` sees a
    # contiguous axis (`_require_hourly_grid` enforces it) and its
    # conservation post-condition therefore covers the last accumulation day
    # of every month, whose closing 00 UTC stamp came from the next month's
    # file. Deaccumulating month-by-month would leave each month's final
    # accumulation day unclosed.
    with ExitStack() as stack:
        prev_ds = stack.enter_context(xr.open_dataset(consumed_paths[prev_window_id]))
        month_datasets = [
            stack.enter_context(xr.open_dataset(consumed_paths[window_id]))
            for window_id in month_window_ids
        ]
        next_ds = stack.enter_context(xr.open_dataset(consumed_paths[next_window_id]))
        combined = xr.concat(
            [
                prev_ds.isel(valid_time=[-1]),
                *month_datasets,
                next_ds.isel(valid_time=[0]),
            ],
            dim="valid_time",
        ).load()

    deaccumulated = deaccumulate_precipitation(
        combined,
        tolerance_mm=tolerance_mm,
        conservation_tolerance_m=conservation_tolerance_m,
        required_range=(required_start, required_end),
    )
    converted = convert_units(deaccumulated.dataset)
    trimmed = converted.sel(valid_time=slice(required_start, required_end)).copy()
    # The D9 required attrs (period_ending convention, accumulation rule,
    # both version identifiers, source dataset) are assigned HERE, BEFORE
    # the first `validate_output_schema` call below — they are part of
    # what D9 declares the schema to be, not something added only at write
    # time, so the pre-write sanity check must see them too (a review
    # finding on the ATTRS check itself surfaced that the pre-write call
    # previously ran before these were ever set, so it could never
    # actually pass once the required-attrs check existed).
    trimmed.attrs.update(
        {
            "period_ending_convention": "hour t covers t-1 -> t (UTC)",
            "accumulation_rule": ACCUMULATION_RULE_ID,
            "transform_version": transform_version,
            "output_schema_version": output_schema_version,
            "source_dataset": manifest.dataset,
        }
    )

    validate_output_schema(
        trimmed, expected_year=year, expected_area=DEFAULT_REQUEST_SPEC.area
    )

    hourly_mm_dir(data_root).mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_path_for(product_path)
    tmp_path.unlink(missing_ok=True)

    # `_OUTPUT_ENCODING_SPEC` (module level) is the SAME dict already fed
    # into `identity` above — writing and identity can never drift apart.
    trimmed.to_netcdf(tmp_path, engine="h5netcdf", encoding=_OUTPUT_ENCODING_SPEC)

    # D5: a REVISION (a previous good product already exists at
    # `product_path`) must survive a manifest-write failure below. Keep the
    # previous generation recoverable at `backup_path` until the manifest
    # publish succeeds; only then is it discarded.
    backup_path = product_path.with_name(product_path.name + ".prev")
    if backup_path.exists():
        # A prior crash left an orphaned backup (died between the
        # os.replace below and either a rollback or the final cleanup).
        # `product_path` missing means the crash happened before the new
        # file was ever published — restore the old good generation now,
        # before this run does anything else with it.
        if not product_path.exists():
            os.replace(backup_path, product_path)
        else:
            backup_path.unlink()
    had_previous_product = product_path.exists()

    try:
        if had_previous_product:
            os.replace(product_path, backup_path)
        try:
            with xr.open_dataset(tmp_path, engine="h5netcdf") as reopened:
                loaded = reopened.load()
                schema_result = validate_output_schema(
                    loaded,
                    expected_year=year,
                    expected_area=DEFAULT_REQUEST_SPEC.area,
                )
                validate_output_encoding(loaded)
            sha256 = checksum_file(tmp_path)
            publish_atomic(tmp_path, product_path)
        except Exception:
            if had_previous_product and not product_path.exists():
                os.replace(backup_path, product_path)
            raise
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
    try:
        write_manifest_atomic(updated_manifest, manifest_path)
    except Exception:
        if had_previous_product and backup_path.exists():
            # The new product replaced the old one, but the manifest that
            # would describe it failed to publish — restore the previous
            # good generation so `product_path` stays consistent with the
            # OLD (untouched, still-valid) manifest entry, per D5.
            os.replace(backup_path, product_path)
        else:
            product_path.unlink(missing_ok=True)
        raise
    finally:
        if backup_path.exists():
            backup_path.unlink()
    log.info("era5.transform.complete", year=year, sha256=sha256)
    return record


def transform_year_instantaneous(
    year: int,
    *,
    data_root: Path,
    provenance: OperatorProvenance,
    clock: Callable[[], datetime],
    transform_version: str = INSTANTANEOUS_TRANSFORM_VERSION,
    output_schema_version: str = INSTANTANEOUS_OUTPUT_SCHEMA_VERSION,
) -> TransformYearRecord:
    """Plan 191 T3 — the instantaneous (t2m, K->degC) transform driver.

    D1: a SEPARATE function from `transform_year` above — not a strategy
    flag threaded through it. D4: reads ONLY the twelve monthly raw
    artifacts of the product year, no previous/next-month boundary context
    (there is no 01 UTC accumulation-day reset to close), so
    `Era5MissingBoundaryContextError` never applies here. Reuses the same
    atomic-publish / `.prev` rollback / manifest-update primitives as
    `transform_year`.
    """
    if year not in STUDY_YEARS:
        raise Era5StorageError(f"year {year} outside study range {STUDY_YEARS}")

    month_window_ids = _product_year_window_ids(year)

    manifest_path = manifest_path_for(data_root)
    manifest = read_manifest(manifest_path)
    if manifest is None:
        raise Era5StorageError(f"no acquisition manifest found at {manifest_path}")
    if manifest.operator_provenance != provenance:
        raise Era5StorageError(
            f"--provenance does not match the acquisition manifest at "
            f"{manifest_path}: {provenance} != {manifest.operator_provenance}"
        )

    consumed_variable_code = variable_code(manifest.variable)
    consumed_paths = {
        window_id: raw_artifact_path(
            window_id, data_root, variable_code=consumed_variable_code
        )
        for window_id in month_window_ids
    }
    for window_id in month_window_ids:
        path = consumed_paths[window_id]
        if not path.exists():
            raise Era5StorageError(
                f"required raw artifact for product month {window_id!r} is "
                f"missing: {path}"
            )

    raw_sha256s: list[str] = []
    request_family: dict[str, object] | None = None
    request_family_label = ""
    for window_id in month_window_ids:
        path = consumed_paths[window_id]
        record = manifest.raw_windows.get(window_id)
        if record is None or checksum_file(path) != record.sha256:
            raise Era5StorageError(
                f"raw artifact for product month {window_id!r} has no valid "
                "manifest entry (missing or checksum mismatch)"
            )
        if record.dataset != manifest.dataset:
            raise Era5StorageError(
                f"raw artifact for product month {window_id!r} was acquired "
                f"under dataset {record.dataset!r}, but the manifest's "
                f"dataset is {manifest.dataset!r} — refusing to mix datasets "
                "within one transformed product"
            )
        family = {
            key: record.request_payload.get(key)
            for key in ("area", "variable", "data_format", "download_format")
        }
        if request_family is None:
            request_family = family
            request_family_label = window_id
        elif family != request_family:
            differing = sorted(k for k in family if family[k] != request_family.get(k))
            raise Era5StorageError(
                f"raw artifact for product month {window_id!r} was acquired "
                f"with a different request family than {request_family_label} "
                f"— {differing} differ. Refusing to build one product from "
                "inconsistent extractions"
            )
        raw_sha256s.append(record.sha256)

    identity = instantaneous_identity(
        raw_sha256s=raw_sha256s,
        units_conversion=INSTANTANEOUS_UNITS_CONVERSION,
        output_schema_version=output_schema_version,
        transform_version=transform_version,
        output_format=INSTANTANEOUS_OUTPUT_FORMAT,
        output_dtype=OUTPUT_DTYPE,
        output_encoding=_INSTANTANEOUS_OUTPUT_ENCODING_SPEC,
    )

    product_path = product_artifact_path(
        year,
        data_root,
        variable_code="t2m",
        product_dir_name="degc",
        unit_label="degc",
    )
    if transform_year_is_current(
        manifest, year=year, expected_identity=identity, final_path=product_path
    ):
        log.info("era5.transform_instantaneous.skip_resume", year=year)
        return manifest.transformed_years[str(year)]

    # D4: exactly the twelve monthly artifacts — no prev/next boundary
    # datasets in this concat, unlike `transform_year` above.
    with ExitStack() as stack:
        month_datasets = [
            stack.enter_context(xr.open_dataset(consumed_paths[window_id]))
            for window_id in month_window_ids
        ]
        combined = xr.concat(month_datasets, dim="valid_time").load()

    converted = convert_kelvin_to_celsius(combined)
    # The D7 required attrs for an instantaneous product (T2's judgment
    # call): `period_ending_convention`/`accumulation_rule` describe
    # accumulator semantics that do not exist here — `units_conversion` is
    # their analogue, naming the transform actually applied.
    converted.attrs.update(
        {
            "transform_version": transform_version,
            "output_schema_version": output_schema_version,
            "source_dataset": manifest.dataset,
            "units_conversion": INSTANTANEOUS_UNITS_CONVERSION,
        }
    )

    validate_instantaneous_schema(
        converted, expected_year=year, expected_area=DEFAULT_REQUEST_SPEC.area
    )

    product_dir(data_root, "degc").mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_path_for(product_path)
    tmp_path.unlink(missing_ok=True)

    converted.to_netcdf(
        tmp_path, engine="h5netcdf", encoding=_INSTANTANEOUS_OUTPUT_ENCODING_SPEC
    )

    backup_path = product_path.with_name(product_path.name + ".prev")
    if backup_path.exists():
        if not product_path.exists():
            os.replace(backup_path, product_path)
        else:
            backup_path.unlink()
    had_previous_product = product_path.exists()

    try:
        if had_previous_product:
            os.replace(product_path, backup_path)
        try:
            with xr.open_dataset(tmp_path, engine="h5netcdf") as reopened:
                loaded = reopened.load()
                schema_result = validate_instantaneous_schema(
                    loaded, expected_year=year, expected_area=DEFAULT_REQUEST_SPEC.area
                )
            sha256 = checksum_file(tmp_path)
            publish_atomic(tmp_path, product_path)
        except Exception:
            if had_previous_product and not product_path.exists():
                os.replace(backup_path, product_path)
            raise
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    record = TransformYearRecord(
        product_year=year,
        transform_identity=identity,
        sha256=sha256,
        accumulation_convention="instantaneous_no_accumulation",
        units_conversion=INSTANTANEOUS_UNITS_CONVERSION,
        # `packing` is genuinely absent, not zero-filled: this transform
        # never clamps and never conserves mass, so there is no packing
        # correction or mass-adjustment quantity to report (see
        # `TransformYearRecord.packing`'s docstring).
        packing=None,
        non_finite_cell_count=schema_result.non_finite_cell_count,
        dropped_boundary_stamp=None,
        transformed_at=clock(),
    )
    updated_manifest = with_transform_year(manifest, record)
    try:
        write_manifest_atomic(updated_manifest, manifest_path)
    except Exception:
        if had_previous_product and backup_path.exists():
            os.replace(backup_path, product_path)
        else:
            product_path.unlink(missing_ok=True)
        raise
    finally:
        if backup_path.exists():
            backup_path.unlink()
    log.info("era5.transform_instantaneous.complete", year=year, sha256=sha256)
    return record
