"""Plan 174 (M-A5) task 4a — ERA5-Land point-extraction CLI.

Extracts the M-A4 hourly-mm ERA5-Land product (Plan 171) at the 26 gauge
locations, under both the `nearest` (D1, primary) and `bilinear` (D1a,
sensitivity comparand) operators, publishing a per-run-unique, identity-
labelled bundle (D7/D9/P1) under
`data/dhm_precip/era5_land/points/<NNNN>-<extraction_identity>/`. There is
no `CURRENT` pointer (P2) and no adoption of an existing bundle (D7.3); a
re-run with identical inputs is allocated the next free run number and
every previous bundle is left untouched.

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
from scripts.dhm_precip.era5_acquire import redact_secrets  # noqa: E402
from scripts.dhm_precip.era5_deaccumulate import (  # noqa: E402
    DAY_START_HOUR,
    OUTPUT_SCHEMA_VERSION,
)
from scripts.dhm_precip.era5_errors import (  # noqa: E402
    Era5AcquisitionError,
    Era5OrographyError,
    Era5StorageError,
    ExtractionInputAbsentError,
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
    D9_PAYLOAD_FILES,
    POINTS_OUTPUT_ENCODING_SPEC,
    POINTS_TIME_UNITS,
    ExtractionIdentityInputs,
    ExtractionManifest,
    checksum_file,
    points_xarray_encoding,
    prepare_staging_dir,
    publish_bundle,
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
    orography_route_identity,
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
    expected_total_hours,
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

# D9's exit-code table. ORDERED SUBCLASS-FIRST, and that ordering is load
# bearing: `Era5StorageError` is a SUBCLASS of `Era5AcquisitionError`, so
# listing the base first made every storage failure exit 4 (an extraction
# post-condition) instead of the documented 5. `_exit_code_for` returns the
# FIRST match, so a base class may never precede one of its own subclasses
# (locked by a test).
_EXIT_BY_ERROR: tuple[tuple[type[Exception], int], ...] = (
    (DhmPrecipLoaderError, 2),
    (ExtractionInputAbsentError, 2),
    (Era5OrographyError, 3),
    (ExtractionPostConditionError, 4),
    (Era5StorageError, 5),
    # every other Era5*Error (extraction hierarchy) not covered above
    (Era5AcquisitionError, 4),
    # a raw OSError never reaches the typed hierarchy: it is storage.
    (OSError, 5),
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


# D9/D7 - the frozen on-disk encoding spec for the points bundle now lives
# in `era5_extract_manifest.py` (MAJOR, 2026-08-17 review) — it is the ONE
# module that both the writer below and the reopen-and-validate bundle
# schema check read, so the two can never drift on what "the frozen
# encoding" actually is. Re-exported here under their historical names so
# existing callers (including `extract_era5.POINTS_OUTPUT_ENCODING_SPEC` in
# tests) keep working unchanged.
#
# MAJOR (2026-08-17 review, P7) — a `_SEMANTIC_TIMEZONE` module constant
# used to duplicate `POINTS_OUTPUT_ENCODING_SPEC["valid_time"][
# "semantic_timezone_attr"]` instead of reading it: both happened to start
# equal, but the WRITER (`_write_series_netcdf`) consumed the separate
# constant, so changing the spec's hashed field alone changed
# `extraction_identity` without changing a single on-disk byte — false
# provenance by construction (P7). The writer below now reads the spec
# entry directly; no local alias survives to drift from it.
_POINTS_TIME_UNITS = POINTS_TIME_UNITS
_xarray_encoding = points_xarray_encoding


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


# D7 P7a (grill-me 2026-08-17) — the CDS request `RealOrographyDownloader`
# actually issues. `OrographySpec.product_id` is the MACHINE-VERIFIED half
# of the spec (asserted below, before any request); everything else on the
# spec (`product_version`, `download_url`, `licence_*`, `vertical_reference`,
# `source_crs`) is OPERATOR-ATTESTED — recorded from the 1a probe, labelled
# as such in the manifest (`_orography_spec_payload`), never asserted,
# because nothing in the CDS request lets this downloader check them.
_REQUEST_DATASET = "reanalysis-era5-land"
_REQUEST_VARIABLE = "geopotential"
_REQUEST_PRODUCT_ID = f"{_REQUEST_DATASET}:{_REQUEST_VARIABLE}"


def _build_geopotential_request(
    area: tuple[float, float, float, float],
) -> dict[str, object]:
    """D7 P7a (BLOCKER, slim review, 2026-08-17 fixer round 3) — the ACTUAL
    payload `RealOrographyDownloader.download()` passes to
    `cdsapi.Client().retrieve()`. Pulled into its own function so a locking
    test can capture what the REAL call issues (via a patched `cdsapi.
    Client`) and assert against it directly — the previous
    `_REQUEST_DOWNLOAD_URL` constant compared `spec.download_url` against a
    SECOND hardcoded module literal that `retrieve()` never reads at all
    (`retrieve()` takes only a dataset name and this payload; no URL is
    ever consumed), so a mutated `download_url` test locked a tautology —
    two comparands this module itself wrote, neither touching `cdsapi`."""
    return {
        "variable": [_REQUEST_VARIABLE],
        "year": [f"{STUDY_YEARS[0]:04d}"],
        "month": ["01"],
        "day": ["01"],
        "time": ["00:00"],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": list(area),
    }


def _assert_spec_matches_request(spec: OrographySpec) -> None:
    """D7 (BLOCKER, round 4, grill-me 2026-08-17) — "ASSERT AGREEMENT; do
    not build a request-builder." `RealOrographyDownloader.download()` used
    to take `spec` and ignore it entirely (`# noqa: ARG002` suppressing the
    exact lint finding that named this), hard-coding the CDS request — so a
    changed spec (new product id, new URL) silently re-fetched the OLD
    route while the manifest recorded the NEW spec: false provenance, the
    same defect class four consecutive review rounds kept finding one layer
    lower (identity clause -> reuse guard -> downloader).

    This downloader IS the source of truth for the request; the spec only
    LABELS it. Before ever requesting, assert the spec's machine-verifiable
    fields agree with what this downloader is about to ask for, and raise a
    typed error otherwise — a changed spec then fails loudly instead of
    recording provenance nothing verified.

    `download_url` is DELIBERATELY not asserted here (BLOCKER, slim review,
    2026-08-17 fixer round 3) — a browser landing-page URL is not a field
    of the machine request `cdsapi.Client().retrieve()` actually issues
    (dataset name + this payload dict; no URL), so there is nothing to
    verify it against. It is OPERATOR-ATTESTED below, and the request this
    downloader actually builds (dataset/variable/area) is what gets
    recorded and can be captured by a test instead
    (`_build_geopotential_request`, `_orography_spec_payload`'s
    `effective_cds_request`).

    `area` is not asserted as a separate spec field here: this downloader
    always requests `DEFAULT_REQUEST_SPEC.area` (module-level, never
    derived from an argument to this call), so there is no divergent value
    it could pick up at this layer — the possibility is removed, not
    checked (P1's own pattern). The resulting raster's actual grid is
    machine-verified downstream, once materialised, by `assert_grid_matches`
    against the CLI's resolved area. `conversion_rule` is machine-verified
    in `era5_orography.convert_field`, against the OBSERVED field
    magnitude — not here, since nothing has been fetched yet."""
    if spec.product_id != _REQUEST_PRODUCT_ID:
        raise Era5OrographyError(
            f"OrographySpec.product_id={spec.product_id!r} does not match "
            f"what RealOrographyDownloader actually requests "
            f"({_REQUEST_PRODUCT_ID!r}) — refusing to re-fetch the OLD "
            "route under a NEW spec's provenance (D7 P7a)"
        )


class RealOrographyDownloader:
    """Branch A (D3a): the CDS `geopotential` invariant field of the same
    `reanalysis-era5-land` dataset Plan 171 already acquires under. Untested
    against the live service here (constraint 5's precedent:
    `era5_acquire.RealCdsClient` is untested for the same reason) — no
    credentials are available to this implementer (1a's own note)."""

    def download(self, *, spec: OrographySpec, dest_dir: Path) -> tuple[Path, ...]:
        _assert_spec_matches_request(spec)
        import cdsapi  # dev-only (D13); imported lazily so this module loads

        # MAJOR (2026-08-17 review) — client construction and `.retrieve()`
        # were UNGUARDED: missing credentials, a CDS rejection or a
        # transport error escaped as an arbitrary exception (exit 1, never
        # D9's exit code 3) with an un-redacted message, unlike the
        # established acquisition client (`era5_acquire.RealCdsClient`).
        # Both are now wrapped into the typed `Era5OrographyError`
        # hierarchy, with `redact_secrets` applied, mirroring that
        # precedent's construction/request split.
        try:
            client = cdsapi.Client(retry_max=1)
        except Exception as exc:  # noqa: BLE001 - construction only fails on missing/invalid config
            raise Era5OrographyError(
                "CDS client configuration failed while fetching orography: "
                f"{redact_secrets(str(exc))}"
            ) from exc
        target = dest_dir / "era5_land_geopotential.nc"
        try:
            client.retrieve(
                _REQUEST_DATASET,
                _build_geopotential_request(DEFAULT_REQUEST_SPEC.area),
                str(target),
            )
        except Exception as exc:  # noqa: BLE001 - reclassified into our typed hierarchy
            raise Era5OrographyError(
                "CDS request failed while fetching orography: "
                f"{redact_secrets(str(exc))}"
            ) from exc
        return (target,)


# BLOCKER (2026-08-17) — the CDS geopotential response is service-shaped
# `z(valid_time, latitude, longitude)`, not `z(time, latitude, longitude)`;
# the ERA5-Land raw contract elsewhere in this package uses `valid_time`
# (`era5_acquire.py:319`). The old check only stripped a dimension literally
# named `time`, so a real one-timestamp `valid_time` response stayed 3-D and
# was handed unchanged to the 2-D aggregator, which cannot broadcast to it.
# Every synthetic test reader returned an already-2-D array, so nothing
# exercised this path.
_TEMPORAL_DIM_NAMES = ("valid_time", "time")


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
        temporal_dims = [d for d in var.dims if d in _TEMPORAL_DIM_NAMES]
        if len(temporal_dims) > 1:
            raise Era5OrographyError(
                f"downloaded orography raster at {path} has more than one "
                f"temporal dimension {temporal_dims} on the geopotential "
                "variable — an invariant field must carry at most one"
            )
        if temporal_dims:
            (temporal_dim,) = temporal_dims
            n_stamps = var.sizes[temporal_dim]
            if n_stamps != 1:
                raise Era5OrographyError(
                    f"downloaded orography raster at {path} has {n_stamps} "
                    f"'{temporal_dim}' stamp(s) — an invariant field must "
                    "carry exactly one"
                )
            var = var.isel({temporal_dim: 0}, drop=True)
        if set(var.dims) != {"latitude", "longitude"}:
            raise Era5OrographyError(
                f"downloaded orography raster at {path} has dims "
                f"{tuple(var.dims)} after dropping the temporal axis — "
                "expected exactly ('latitude', 'longitude')"
            )
        values = np.asarray(var.transpose("latitude", "longitude").values)
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

    # D7 (BLOCKER, 2026-08-17) — reuse is gated on the ROUTE, not on a file
    # existing. Reusing any record that merely had a `raster_path` meant a
    # changed frozen spec (new URL, product version, vertical reference,
    # conversion rule) silently reused the raster built from the OLD route
    # while the manifest serialised the NEW spec — provenance the artefact
    # does not have. A changed spec is a legitimate, expected event, so the
    # response is re-materialisation, never a raise.
    current_route_identity = orography_route_identity(OBSERVED_OROGRAPHY_SPEC)
    existing_record = read_orography_source_record(
        orography_source_record_path(data_root)
    )
    if (
        existing_record is not None
        and existing_record.raster_path is not None
        and existing_record.orography_route_identity == current_route_identity
    ):
        source_record = existing_record
    else:
        if (
            existing_record is not None
            and existing_record.orography_route_identity != current_route_identity
        ):
            log.info(
                "era5_extract.cli.orography_route_changed",
                recorded_route_identity=existing_record.orography_route_identity,
                current_route_identity=current_route_identity,
            )
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
    # D7 — "a materialised raster is never trusted because a file of the
    # right name exists": re-verify on EVERY run, freshly derived or reused,
    # and against the CURRENT spec rather than the record's own claim.
    oro_identity = verify_orography_materialisation(
        source_record,
        data_root=data_root,
        spec=OBSERVED_OROGRAPHY_SPEC,
        expected_lat=expected_lat,
        expected_lon=expected_lon,
    )
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
    # D7 (CORRECTED 2026-08-17) — keyed BY YEAR, because a bare list of
    # hashes cannot say which year each product's bytes belonged to: two
    # years' products exchanged left the identity unchanged.
    source_sha256s_by_year: dict[str, str] = {}

    for year in STUDY_YEARS:
        product_path = product_artifact_path(year, data_root)
        record = era5_manifest.transformed_years.get(str(year))
        if record is None:
            raise Era5StorageError(
                f"no transformed-year record for {year} in the acquisition manifest"
            )
        # MINOR (2026-08-17) — a missing annual product used to reach
        # `checksum_file`, which wraps the resulting `FileNotFoundError` as
        # `Era5StorageError` (exit 5). D9 assigns a missing product to exit
        # code 2 ("inputs absent"), not a storage failure: nothing was
        # written and nothing failed to write, the input simply is not
        # there yet (Plan 171 Task 4b has not run).
        if not product_path.exists():
            raise ExtractionInputAbsentError(
                f"acquired ERA5-Land product for {year} is missing at "
                f"{product_path} (D9 exit code 2: inputs absent)"
            )
        assert_source_checksum(product_path, expected_sha256=record.sha256)
        source_sha256s_by_year[str(year)] = record.sha256
        with xr.open_dataset(product_path, engine="h5netcdf") as reopened:
            ds = reopened.load()
        assert_extraction_source_valid(ds, expected_year=year, expected_area=area)
        assert_utc_epoch_encoding(ds)
        for station, coord in stations.by_station.items():
            nearest = extract_nearest_series(ds, coord)
            assert_no_missing_primary(nearest)
            bilinear = extract_bilinear_series(ds, coord)
            nearest_parts.setdefault(station, []).append(nearest)
            bilinear_parts.setdefault(station, []).append(bilinear)

    merged_nearest = {s: _concat_series(v) for s, v in nearest_parts.items()}
    merged_bilinear = {s: _concat_series(v) for s, v in bilinear_parts.items()}

    # MAJOR (2026-08-17) — the nearest grid cell for each station is read
    # off the already-computed PRIMARY series (`merged_nearest`), never
    # recomputed independently from raw lat/lon arrays; see
    # `build_station_grid_elevation_table`'s docstring for why the two used
    # to be able to disagree.
    elevation_rows = build_station_grid_elevation_table(
        stations,
        nearest_by_station=merged_nearest,
        orography_ds=orography_ds,
        orography_source=OBSERVED_OROGRAPHY_SPEC.source,
        orography_product_id=OBSERVED_OROGRAPHY_SPEC.product_id,
        orography_product_version=OBSERVED_OROGRAPHY_SPEC.product_version,
        orography_vertical_reference=OBSERVED_OROGRAPHY_SPEC.vertical_reference,
    )
    sensitivity = build_operator_sensitivity_table(
        merged_nearest, merged_bilinear, params=resolved_params
    )

    # MAJOR (2026-08-17 review) — a single typed `ExtractionIdentityInputs`
    # snapshot is now the ONE source for both the digest (`.digest()`,
    # equal to `extraction_identity(**kwargs)`) and the manifest's
    # `identity_inputs` (`.canonical_payload()`), so the two can never
    # disagree on what was actually hashed. `output_dtype` is read from
    # `POINTS_OUTPUT_ENCODING_SPEC` — the SAME spec the writer below reads —
    # rather than a separately hard-coded literal that could drift from it.
    identity_inputs = ExtractionIdentityInputs(
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256=coordinate_table_sha256,
        source_sha256s_by_year=dict(source_sha256s_by_year),
        orography_identity=oro_identity,
        jjas_months=tuple(resolved_params.jjas_months),
        djf_months=tuple(resolved_params.djf_months),
        mam_months=tuple(resolved_params.mam_months),
        on_months=tuple(resolved_params.on_months),
        wet_threshold_mm_per_h=resolved_params.wet_threshold_mm_per_h,
        wet_threshold_side=resolved_params.wet_threshold_side,
        zero_policy=resolved_params.zero_policy,
        quantile_definition=resolved_params.quantile_definition,
        quantile_grid=tuple(resolved_params.quantile_grid),
        station_elevation_datum=str(VerticalDatum.UNKNOWN),
        orography_elevation_datum=str(OBSERVED_OROGRAPHY_SPEC.vertical_reference),
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        output_format="netcdf4_h5netcdf",
        output_dtype=str(
            POINTS_OUTPUT_ENCODING_SPEC["precipitation_mm_per_h"]["dtype"]
        ),
        output_encoding=POINTS_OUTPUT_ENCODING_SPEC,
    )
    identity = identity_inputs.digest()

    staging = prepare_staging_dir(data_root, identity=identity)
    _write_series_netcdf(staging / "series_nearest.nc", merged_nearest)
    _write_series_netcdf(staging / "series_bilinear.nc", merged_bilinear)
    _write_elevation_csv(staging / "station_grid_elevation.csv", elevation_rows)
    sensitivity.write_csv(staging / "operator_sensitivity.csv")

    payload_sha256s = {name: checksum_file(staging / name) for name in D9_PAYLOAD_FILES}
    manifest = ExtractionManifest(
        orography_identity=oro_identity,
        extraction_identity=identity,
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256=coordinate_table_sha256,
        source_sha256s_by_year=dict(source_sha256s_by_year),
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
        identity_inputs=identity_inputs.canonical_payload(),
        generated_at=resolved_clock(),
    )
    write_extraction_manifest(manifest, staging / "extraction_manifest.json")
    # The PINNED count (D8/2d), not `len(stations)` — comparing the bundle
    # against the very inventory that produced it validates nothing.
    # P4/P4a — `publish_bundle` validates (including payload_sha256
    # reconciliation) INSIDE itself and refuses to publish on failure, then
    # allocates a per-run-unique numbered directory (P1/P1a). No adoption,
    # no quarantine, no `CURRENT` pointer (D7.3, P1-P6).
    final_dir = publish_bundle(
        staging,
        data_root=data_root,
        identity=identity,
        expected_station_count=resolved_params.expected_station_count,
        expected_hour_count=expected_total_hours(STUDY_YEARS),
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


# D7 P7a (slim review, grill-me 2026-08-17) — the assertion in
# `_assert_spec_matches_request` NARROWS the false-provenance gap; it does
# not remove it. `product_version` (and the other fields below) cannot be
# checked against anything the CDS request itself exposes — a longer
# assertion list cannot fix that, because there is nothing to compare
# against. Splitting the spec's fields by who can vouch for them makes the
# residue visible in the manifest instead of silently claiming "verified"
# about a field nothing verified.
#
# BLOCKER (2026-08-17 fixer round 3) — `download_url` was previously listed
# here as "machine-verified", but the assertion backing that claim compared
# `spec.download_url` against a SECOND hardcoded module constant that
# `cdsapi.Client().retrieve()` never reads (`retrieve()` takes a dataset
# name and a payload dict; no URL). Neither comparand ever touched the real
# request, so the "verification" was a tautology between two literals this
# module itself wrote. `download_url` is a browser landing page — there is
# nothing in the actual CDS call to verify it against — so it moves to
# `OPERATOR_ATTESTED_SPEC_FIELDS`, where P7a's own rule already puts fields
# nothing in the pipeline can check.
MACHINE_VERIFIED_SPEC_FIELDS: frozenset[str] = frozenset(
    {"product_id", "conversion_rule"}
)
"""Asserted against the actual request/observation before use: `product_id`
in `_assert_spec_matches_request` (before any CDS request — this downloader
IS the source of truth for the dataset+variable actually requested);
`conversion_rule` in `era5_orography.convert_field` (against the observed
field magnitude). `area` is not a spec field at all (see
`_assert_spec_matches_request`'s docstring); the actual request this
downloader issues (dataset/variable/area) is recorded separately, as
`effective_cds_request` below — captured and locked by a test against the
REAL `RealOrographyDownloader.download()` call path, not compared against a
second hardcoded constant."""

OPERATOR_ATTESTED_SPEC_FIELDS: frozenset[str] = frozenset(
    {
        "product_version",
        "download_url",
        "licence_name",
        "licence_version",
        "licence_url",
        "vertical_reference",
        "source_crs",
    }
)
"""Recorded verbatim from the 1a probe. Nothing in the pipeline can verify
these against the live service — CDS offers no way to select or confirm a
product version, for instance, and `download_url` is a human-facing landing
page no API call ever consumes — so they are labelled `attested`, never
`verified`, in the manifest."""


def _orography_spec_payload(spec: OrographySpec) -> dict[str, object]:
    """D9 — EVERY `OrographySpec` field, JSON-safe. Serialising only four of
    them (product id/version, source, vertical reference) dropped the
    licence, the URL, the CRS, the units, the no-data sentinel and both
    frozen rules — i.e. most of what makes the route reproducible.

    P7a — the payload also carries which fields are machine-verified versus
    operator-attested, so a reader cannot mistake the latter for the
    former, PLUS the `effective_cds_request` this downloader actually
    issues (dataset/variable/area) — the real machine-verified resource,
    recorded explicitly rather than left implicit in `download_url`'s
    now-corrected attested label (BLOCKER, 2026-08-17 fixer round 3)."""
    payload = asdict(spec)
    payload["source"] = str(spec.source)
    payload["vertical_reference"] = str(spec.vertical_reference)
    payload["conversion_rule"] = str(spec.conversion_rule)
    payload["probe_date"] = spec.probe_date.isoformat()
    payload["rejected_candidates"] = [
        asdict(candidate) for candidate in spec.rejected_candidates
    ]
    payload["provenance"] = {
        "machine_verified_fields": sorted(MACHINE_VERIFIED_SPEC_FIELDS),
        "operator_attested_fields": sorted(OPERATOR_ATTESTED_SPEC_FIELDS),
        "effective_cds_request": {
            "dataset": _REQUEST_DATASET,
            "variable": [_REQUEST_VARIABLE],
            "area": list(DEFAULT_REQUEST_SPEC.area),
        },
    }
    return payload


def _concat_series(parts: list[ExtractedSeries]) -> ExtractedSeries:
    first = parts[0]
    valid_time = np.concatenate([p.valid_time for p in parts])
    values = np.concatenate([p.values for p in parts])
    n_nan = int(np.isnan(values).sum())
    n_inf = int(np.isinf(values).sum())
    return replace(
        first,
        valid_time=valid_time,
        values=values,
        n_finite=values.size - n_nan - n_inf,
        n_nan=n_nan,
        n_inf=n_inf,
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
    # MAJOR (2026-08-17 review) — the dtype cast used to be a separately
    # hard-coded `np.float32` literal, independent of
    # `POINTS_OUTPUT_ENCODING_SPEC["precipitation_mm_per_h"]["dtype"]` (the
    # very value `output_dtype` hashes into `extraction_identity`). Deriving
    # the cast from the SAME spec means a spec change actually changes the
    # on-disk bytes, not merely the identity string.
    precip_dtype = np.dtype(
        POINTS_OUTPUT_ENCODING_SPEC["precipitation_mm_per_h"]["dtype"]  # type: ignore[arg-type]
    )
    values = np.stack([by_station[s].values for s in stations], axis=0).astype(
        precip_dtype
    )
    ds = xr.Dataset(
        {"precipitation_mm_per_h": (["station", "valid_time"], values)},
        coords={"station": [str(s) for s in stations], "valid_time": valid_time},
    )
    ds["valid_time"].attrs["timezone"] = str(
        POINTS_OUTPUT_ENCODING_SPEC["valid_time"]["semantic_timezone_attr"]
    )
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
    except (DhmPrecipLoaderError, Era5AcquisitionError, OSError) as exc:
        log.error(
            "era5_extract.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        return _exit_code_for(exc)


if __name__ == "__main__":
    raise SystemExit(main())
