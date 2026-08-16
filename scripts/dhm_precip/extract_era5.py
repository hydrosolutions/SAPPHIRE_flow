"""Plan 174 (M-A5) task 4a — ERA5-Land point-extraction CLI.

Extracts the M-A4 hourly-mm ERA5-Land product (Plan 171) at the 26 gauge
locations, under both the `nearest` (D1, primary) and `bilinear` (D1a,
sensitivity comparand) operators, publishing an identity-addressed bundle
(D7/D9) under `data/dhm_precip/era5_land/points/<extraction_identity>/`.

Usage:
    uv run python scripts/dhm_precip/extract_era5.py --stage orography
    uv run python scripts/dhm_precip/extract_era5.py --stage all

Stage note (D9 names four stages; this implementation note is deliberate,
not a deviation from the CLI *contract*): D7 requires the points bundle to
publish as ONE atomic unit — no partial bundle can be reopened-and-validated
or published on its own. `orography` therefore only ever materialises the
orography raster (1b, no points bundle). `extract`, `sensitivity` and `all`
each run the full points pipeline (orography-if-missing -> per-station
series -> elevation table -> sensitivity envelope -> one bundle publish) —
they are accepted as distinct stage names (D9's CLI contract) but currently
behave identically beyond `orography`, since the bundle cannot be split.

Environment:
    DHM_PRECIP_XLSX      required for the REAL station-set boundary input
                         (2d) — read for its column INVENTORY only, never
                         gauge values (D4). Not needed when a coordinate
                         table + expected-station set are injected (tests).
    DHM_PRECIP_ERA5_ROOT the real-data integration gate (D10); this CLI
                         itself does not read it directly (4b's own test does).

Exit codes (D9):
    0  success
    2  inputs absent (no product / no coordinate table / no orography spec)
    3  orography acquisition or validation failed
    4  an extraction post-condition failed (bounds, NaN, station set, axis,
       checksum)
    5  storage/manifest write failed
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import structlog  # noqa: E402
import xarray as xr  # noqa: E402

from sapphire_flow.logging import configure_cli_logging  # noqa: E402
from scripts.dhm_precip.domain_types import (  # noqa: E402
    ExtractionOperator,
    Station,
    VerticalDatum,
)
from scripts.dhm_precip.era5_deaccumulate import (  # noqa: E402
    DAY_START_HOUR,
    OUTPUT_SCHEMA_VERSION,
)
from scripts.dhm_precip.era5_errors import (  # noqa: E402
    Era5AcquisitionError,
    Era5OrographyError,
    Era5StorageError,
    ExtractionPostConditionError,
)
from scripts.dhm_precip.era5_extract import (  # noqa: E402
    ExtractedSeries,
    StationGridElevationRow,
    assert_expected_station_cardinality,
    assert_extraction_source_valid,
    assert_no_missing_primary,
    assert_source_checksum,
    assert_utc_epoch_encoding,
    build_operator_sensitivity_table,
    build_station_grid_elevation_table,
    extract_bilinear_series,
    extract_nearest_series,
    load_expected_station_coordinates,
    station_accounting_entry,
)
from scripts.dhm_precip.era5_extract_manifest import (  # noqa: E402
    ExtractionManifest,
    checksum_file,
    extraction_identity,
    prepare_staging_dir,
    publish_bundle,
    reopen_and_validate_bundle,
    write_extraction_manifest,
)
from scripts.dhm_precip.era5_manifest import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    manifest_path_for,
    passing_accumulation_diagnostic,
    product_artifact_path,
    read_manifest,
)
from scripts.dhm_precip.era5_orography import (  # noqa: E402
    OrographyDownloader,
    materialise_orography,
    orography_raster_path,
    orography_source_record_path,
    read_orography_source_record,
    verify_orography_materialisation,
)
from scripts.dhm_precip.era5_orography_spec import (  # noqa: E402
    OBSERVED_OROGRAPHY_SPEC,
    OrographySpec,
)
from scripts.dhm_precip.era5_request import (  # noqa: E402
    DEFAULT_REQUEST_SPEC,
    GRID_SPACING_DEG,
    STUDY_YEARS,
    expected_grid_shape,
)
from scripts.dhm_precip.loader import (  # noqa: E402
    PRODUCTION_SOURCE_SHA256,
    DhmPrecipLoaderError,
    load_long_frame,
    resolve_coords_path,
    resolve_source_path,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from scripts.dhm_precip.params import DhmPrecipParams

log = structlog.get_logger(__name__)

_EXIT_BY_ERROR: tuple[tuple[type[Exception], int], ...] = (
    (DhmPrecipLoaderError, 2),
    (Era5OrographyError, 3),
    (ExtractionPostConditionError, 4),
    (
        Era5AcquisitionError,
        4,
    ),  # every other Era5*Error (extraction hierarchy) not covered above
    (Era5StorageError, 5),
)


def _exit_code_for(exc: Exception) -> int:
    for exc_type, code in _EXIT_BY_ERROR:
        if isinstance(exc, exc_type):
            return code
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="extract_era5", description=__doc__)
    parser.add_argument(
        "--stage", choices=("orography", "extract", "sensitivity", "all"), default="all"
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path for a human-readable JSON run summary.",
    )
    return parser


# --- D9/D7 - the frozen on-disk encoding spec for the points bundle ---
#
# MAJOR (2026-08-16): the identity's "full encoding spec" previously carried
# only the `valid_time` units/dtype, so a change to the precipitation fill
# value, compression, chunking or the station encoding produced the SAME
# `extraction_identity` and the stale bundle was reused. Mirrors M-A4's own
# `era5_transform._OUTPUT_ENCODING_SPEC`.
#
# Chunking is declared as a POLICY (one station, one station-year per chunk)
# rather than a literal shape, because the hour count depends on the study
# years; `_xarray_encoding` clamps it to the array actually being written.
_POINTS_CHUNK_STATIONS = 1
_POINTS_CHUNK_HOURS = 8760
_SEMANTIC_TIMEZONE = "UTC"
_POINTS_TIME_UNITS = "hours since 1970-01-01 00:00:00"

POINTS_OUTPUT_ENCODING_SPEC: dict[str, dict[str, object]] = {
    "precipitation_mm_per_h": {
        "dtype": "float32",
        "zlib": True,
        "complevel": 4,
        "chunk_stations": _POINTS_CHUNK_STATIONS,
        "chunk_hours": _POINTS_CHUNK_HOURS,
        "_FillValue": "NaN",
    },
    "valid_time": {
        "units": _POINTS_TIME_UNITS,
        "dtype": "int64",
        "semantic_timezone_attr": _SEMANTIC_TIMEZONE,
    },
    "station": {"dtype": "S1", "fixed_length": True},
}


def _xarray_encoding(shape: tuple[int, ...]) -> dict[str, dict[str, object]]:
    """Translate the frozen spec into the encoding dict the pinned encoder
    takes, clamping the declared chunk policy to the array being written."""
    n_stations, n_hours = shape
    return {
        "precipitation_mm_per_h": {
            "dtype": "float32",
            "zlib": True,
            "complevel": 4,
            "chunksizes": (
                min(_POINTS_CHUNK_STATIONS, n_stations),
                min(_POINTS_CHUNK_HOURS, n_hours),
            ),
            "_FillValue": float("nan"),
        },
        "valid_time": {"units": _POINTS_TIME_UNITS, "dtype": "int64"},
        "station": {"dtype": "S1"},
    }


def _default_expected_stations() -> frozenset[Station]:
    """The workbook-derived usable-station inventory (2d's boundary
    decision) — reads the pinned production workbook's COLUMN inventory
    only, never a gauge value (D4), mirroring `run.py:100-107`."""
    source_path = resolve_source_path()
    _long_frame, inventory = load_long_frame(
        source_path, expected_sha256=PRODUCTION_SOURCE_SHA256
    )
    return frozenset(
        Station(name)
        for name in inventory.all_columns
        if name not in inventory.empty_columns
    )


class RealOrographyDownloader:
    """Branch A (D3a): the CDS `geopotential` invariant field of the same
    `reanalysis-era5-land` dataset Plan 171 already acquires under. Untested
    against the live service here (constraint 5's precedent:
    `era5_acquire.RealCdsClient` is untested for the same reason) — no
    credentials are available to this implementer (1a's own note)."""

    def download(self, *, spec: object, dest_dir: Path) -> tuple[Path, ...]:  # noqa: ARG002
        import cdsapi  # dev-only (D13); imported lazily so this module loads

        client = cdsapi.Client(retry_max=1)
        target = dest_dir / "era5_land_geopotential.nc"
        client.retrieve(
            "reanalysis-era5-land",
            {
                "variable": ["geopotential"],
                "year": [f"{STUDY_YEARS[0]:04d}"],
                "month": ["01"],
                "day": ["01"],
                "time": ["00:00"],
                "data_format": "netcdf",
                "download_format": "unarchived",
                "area": list(DEFAULT_REQUEST_SPEC.area),
            },
            str(target),
        )
        return (target,)


def _real_orography_raw_reader(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with xr.open_dataset(path) as ds:
        for candidate in ("z", "geopotential"):
            if candidate in ds:
                var = ds[candidate]
                break
        else:
            raise Era5OrographyError(
                f"downloaded orography raster at {path} has neither 'z' nor "
                "'geopotential' — cannot locate the geopotential variable"
            )
        values = np.asarray(
            var.isel(time=0).values if "time" in var.dims else var.values
        )
        lat = np.asarray(ds["latitude"].values)
        lon = np.asarray(ds["longitude"].values)
    return values, lat, lon


def run(
    args: argparse.Namespace,
    *,
    clock: Callable[[], datetime] | None = None,
    orography_downloader: OrographyDownloader | None = None,
    orography_raw_reader: Callable[[Path], tuple[np.ndarray, np.ndarray, np.ndarray]]
    | None = None,
    coords_path: Path | None = None,
    expected_stations: frozenset[Station] | None = None,
    request_area: tuple[float, float, float, float] | None = None,
    params: DhmPrecipParams | None = None,
) -> int:
    data_root: Path = args.data_root
    resolved_params = params if params is not None else DEFAULT_PARAMS
    resolved_clock: Callable[[], datetime] = (
        clock if clock is not None else (lambda: datetime.now(UTC))
    )
    downloader: OrographyDownloader = (
        orography_downloader
        if orography_downloader is not None
        else RealOrographyDownloader()
    )
    raw_reader: Callable[[Path], tuple[np.ndarray, np.ndarray, np.ndarray]] = (
        orography_raw_reader
        if orography_raw_reader is not None
        else _real_orography_raw_reader
    )
    area = request_area if request_area is not None else DEFAULT_REQUEST_SPEC.area

    lat_count, lon_count = expected_grid_shape(area)
    north, west, south, east = area
    expected_lat = np.round(np.linspace(south, north, lat_count), 10)
    expected_lon = np.round(np.linspace(west, east, lon_count), 10)

    existing_record = read_orography_source_record(
        orography_source_record_path(data_root)
    )
    if existing_record is None or existing_record.raster_path is None:
        source_record = materialise_orography(
            OBSERVED_OROGRAPHY_SPEC,
            downloader=downloader,
            raw_reader=raw_reader,
            data_root=data_root,
            expected_lat=expected_lat,
            expected_lon=expected_lon,
            target_spacing_deg=GRID_SPACING_DEG,
            clock=resolved_clock,
        )
    else:
        source_record = existing_record
    # D7 — "a materialised raster is never trusted because a file of the
    # right name exists": re-verify on EVERY run, freshly derived or adopted.
    oro_identity = verify_orography_materialisation(source_record, data_root=data_root)
    log.info("era5_extract.cli.orography_ready", orography_identity=oro_identity)

    if args.stage == "orography":
        return 0

    resolved_coords_path = (
        coords_path if coords_path is not None else resolve_coords_path()
    )
    resolved_expected_stations = (
        expected_stations
        if expected_stations is not None
        else _default_expected_stations()
    )
    # D8/2d — the cardinality tripwire fires on the BOUNDARY INPUT, before
    # any extraction: equality against a self-supplied inventory is not a
    # constraint, so the count is pinned independently.
    assert_expected_station_cardinality(
        resolved_expected_stations,
        expected_count=resolved_params.expected_station_count,
    )
    stations = load_expected_station_coordinates(
        resolved_coords_path, expected_stations=resolved_expected_stations
    )
    coordinate_table_sha256 = checksum_file(resolved_coords_path)

    manifest_path = manifest_path_for(data_root)
    era5_manifest = read_manifest(manifest_path)
    if era5_manifest is None:
        raise Era5StorageError(
            f"no ERA5-Land acquisition manifest found at {manifest_path}"
        )
    diagnostic = passing_accumulation_diagnostic(
        era5_manifest, expected_reset_hour=DAY_START_HOUR
    )
    if diagnostic is None:
        raise ExtractionPostConditionError(
            "no passing AccumulationDiagnosticRecord in the acquisition manifest "
            "(D5.2) — run `acquire_era5.py --stage diagnose` against a real "
            "window first"
        )

    orography_ds_path = orography_raster_path(
        data_root, route_identity=source_record.orography_route_identity
    )
    with xr.open_dataset(orography_ds_path, engine="h5netcdf") as oro_reopened:
        orography_ds = oro_reopened.load()

    nearest_parts: dict[Station, list[ExtractedSeries]] = {}
    bilinear_parts: dict[Station, list[ExtractedSeries]] = {}
    source_sha256s: list[str] = []
    precip_lat: np.ndarray | None = None
    precip_lon: np.ndarray | None = None

    for year in STUDY_YEARS:
        product_path = product_artifact_path(year, data_root)
        record = era5_manifest.transformed_years.get(str(year))
        if record is None:
            raise Era5StorageError(
                f"no transformed-year record for {year} in the acquisition manifest"
            )
        assert_source_checksum(product_path, expected_sha256=record.sha256)
        source_sha256s.append(record.sha256)
        with xr.open_dataset(product_path, engine="h5netcdf") as reopened:
            ds = reopened.load()
        assert_extraction_source_valid(ds, expected_year=year, expected_area=area)
        assert_utc_epoch_encoding(ds)
        precip_lat = ds["latitude"].values
        precip_lon = ds["longitude"].values
        for station, coord in stations.by_station.items():
            nearest = extract_nearest_series(ds, coord)
            assert_no_missing_primary(nearest)
            bilinear = extract_bilinear_series(ds, coord)
            nearest_parts.setdefault(station, []).append(nearest)
            bilinear_parts.setdefault(station, []).append(bilinear)

    assert (
        precip_lat is not None and precip_lon is not None
    )  # STUDY_YEARS is never empty

    merged_nearest = {s: _concat_series(v) for s, v in nearest_parts.items()}
    merged_bilinear = {s: _concat_series(v) for s, v in bilinear_parts.items()}

    elevation_rows = build_station_grid_elevation_table(
        stations,
        precip_lat=precip_lat,
        precip_lon=precip_lon,
        orography_ds=orography_ds,
        orography_source=OBSERVED_OROGRAPHY_SPEC.source,
        orography_product_id=OBSERVED_OROGRAPHY_SPEC.product_id,
        orography_product_version=OBSERVED_OROGRAPHY_SPEC.product_version,
        orography_vertical_reference=OBSERVED_OROGRAPHY_SPEC.vertical_reference,
    )
    sensitivity = build_operator_sensitivity_table(
        merged_nearest, merged_bilinear, params=resolved_params
    )

    identity = extraction_identity(
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256=coordinate_table_sha256,
        source_sha256s=source_sha256s,
        orography_identity=oro_identity,
        jjas_months=resolved_params.jjas_months,
        djf_months=resolved_params.djf_months,
        mam_months=resolved_params.mam_months,
        on_months=resolved_params.on_months,
        wet_threshold_mm_per_h=resolved_params.wet_threshold_mm_per_h,
        wet_threshold_side=resolved_params.wet_threshold_side,
        zero_policy=resolved_params.zero_policy,
        quantile_definition=resolved_params.quantile_definition,
        quantile_grid=resolved_params.quantile_grid,
        station_elevation_datum=str(VerticalDatum.UNKNOWN),
        orography_elevation_datum=str(OBSERVED_OROGRAPHY_SPEC.vertical_reference),
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        output_format="netcdf4_h5netcdf",
        output_dtype="float32",
        output_encoding=POINTS_OUTPUT_ENCODING_SPEC,
    )

    staging = prepare_staging_dir(data_root, identity=identity)
    _write_series_netcdf(staging / "series_nearest.nc", merged_nearest)
    _write_series_netcdf(staging / "series_bilinear.nc", merged_bilinear)
    _write_elevation_csv(staging / "station_grid_elevation.csv", elevation_rows)
    sensitivity.write_csv(staging / "operator_sensitivity.csv")

    payload_sha256s = {
        name: checksum_file(staging / name)
        for name in (
            "series_nearest.nc",
            "series_bilinear.nc",
            "station_grid_elevation.csv",
            "operator_sensitivity.csv",
        )
    }
    manifest = ExtractionManifest(
        orography_identity=oro_identity,
        extraction_identity=identity,
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256=coordinate_table_sha256,
        source_sha256s=tuple(source_sha256s),
        payload_sha256s=payload_sha256s,
        orography_spec=_orography_spec_payload(OBSERVED_OROGRAPHY_SPEC),
        orography_source_record={
            "orography_route_identity": source_record.orography_route_identity,
            "orography_identity": oro_identity,
            "fetched_at": source_record.fetched_at.isoformat(),
            "downloaded_files": [asdict(f) for f in source_record.downloaded_files],
            "raster_path": source_record.raster_path,
            "raster_sha256": source_record.raster_sha256,
            "raster_schema_version": source_record.raster_schema_version,
        },
        accumulation_diagnostic={
            "window_id": diagnostic.window_id,
            "source_sha256": diagnostic.source_sha256,
            "reset_hour": diagnostic.reset_hour,
            "terminal_hour": diagnostic.terminal_hour,
            "monotone_within_day": diagnostic.monotone_within_day,
            "sample_size_days": diagnostic.sample_size_days,
            "recorded_at": diagnostic.recorded_at.isoformat(),
        },
        station_accounting={
            str(ExtractionOperator.NEAREST): {
                str(station): station_accounting_entry(series)
                for station, series in sorted(merged_nearest.items())
            },
            str(ExtractionOperator.BILINEAR): {
                str(station): station_accounting_entry(series)
                for station, series in sorted(merged_bilinear.items())
            },
        },
        generated_at=resolved_clock(),
    )
    write_extraction_manifest(manifest, staging / "extraction_manifest.json")
    # The PINNED count (D8/2d), not `len(stations)` — comparing the bundle
    # against the very inventory that produced it validates nothing.
    reopen_and_validate_bundle(
        staging, expected_station_count=resolved_params.expected_station_count
    )
    final_dir = publish_bundle(
        staging,
        data_root=data_root,
        identity=identity,
        expected_station_count=resolved_params.expected_station_count,
        clock_now=resolved_clock(),
    )

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {"extraction_identity": identity, "published_dir": str(final_dir)},
                indent=2,
            )
        )

    log.info("era5_extract.cli.published", extraction_identity=identity)
    return 0


def _orography_spec_payload(spec: OrographySpec) -> dict[str, object]:
    """D9 — EVERY `OrographySpec` field, JSON-safe. Serialising only four of
    them (product id/version, source, vertical reference) dropped the
    licence, the URL, the CRS, the units, the no-data sentinel and both
    frozen rules — i.e. most of what makes the route reproducible."""
    payload = asdict(spec)
    payload["source"] = str(spec.source)
    payload["vertical_reference"] = str(spec.vertical_reference)
    payload["conversion_rule"] = str(spec.conversion_rule)
    payload["probe_date"] = spec.probe_date.isoformat()
    payload["rejected_candidates"] = [
        asdict(candidate) for candidate in spec.rejected_candidates
    ]
    return payload


def _concat_series(parts: list[ExtractedSeries]) -> ExtractedSeries:
    first = parts[0]
    valid_time = np.concatenate([p.valid_time for p in parts])
    values = np.concatenate([p.values for p in parts])
    n_nan = int(np.isnan(values).sum())
    return replace(
        first,
        valid_time=valid_time,
        values=values,
        n_finite=values.size - n_nan,
        n_nan=n_nan,
    )


def _write_series_netcdf(
    path: Path, by_station: dict[Station, ExtractedSeries]
) -> None:
    """D9/D5.0 — the on-disk schema is part of the contract:

    * `station` is a FIXED-LENGTH string coordinate (`dtype="S1"` writes the
      netCDF char-array representation over a `stringN` dimension), not
      h5netcdf's variable-length default;
    * `precipitation_mm_per_h` carries the declared fill value, compression
      and chunking — all of which `POINTS_OUTPUT_ENCODING_SPEC` hashes into
      `extraction_identity`;
    * `valid_time` stays CF-encoded and timezone-NAIVE (D5.0 — a tz-aware
      coordinate cannot be written by the pinned encoder at all), with the
      SEMANTIC UTC attribute carrying the claim the dtype cannot.
    """
    stations = sorted(by_station)
    valid_time = by_station[stations[0]].valid_time
    values = np.stack([by_station[s].values for s in stations], axis=0).astype(
        np.float32
    )
    ds = xr.Dataset(
        {"precipitation_mm_per_h": (["station", "valid_time"], values)},
        coords={"station": [str(s) for s in stations], "valid_time": valid_time},
    )
    ds["valid_time"].attrs["timezone"] = _SEMANTIC_TIMEZONE
    ds["precipitation_mm_per_h"].attrs["units"] = "mm h-1"
    ds.to_netcdf(path, engine="h5netcdf", encoding=_xarray_encoding(values.shape))


def _row_to_dict(row: StationGridElevationRow) -> dict[str, object]:
    return {k: (v.value if hasattr(v, "value") else v) for k, v in asdict(row).items()}


def _write_elevation_csv(path: Path, rows: tuple[StationGridElevationRow, ...]) -> None:
    import polars as pl

    frame = pl.DataFrame([_row_to_dict(r) for r in rows])
    frame.write_csv(path)


def main(argv: list[str] | None = None, **kwargs: object) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_cli_logging()
    try:
        return run(args, **kwargs)  # type: ignore[arg-type]
    except (DhmPrecipLoaderError, Era5AcquisitionError) as exc:
        log.error(
            "era5_extract.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        return _exit_code_for(exc)


if __name__ == "__main__":
    raise SystemExit(main())
