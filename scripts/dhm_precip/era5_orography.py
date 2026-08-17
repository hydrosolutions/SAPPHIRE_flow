"""Plan 174 (M-A5) task 1b — fetch, convert, aggregate and validate the
orography raster per the frozen `OrographySpec` (1a).

D3a: `OrographySpec` (1a) describes the ROUTE; this module describes what
was actually MATERIALISED (`OrographySourceRecord`) and performs the
conversion + `mean_of_contained_cells` aggregation to the ERA5-Land 0.1 deg
grid, with the no-data policy and the exact-grid-vector post-condition.

Per-station lookup and per-station finiteness are explicitly OUT of scope
here (they are 3a's job, `era5_extract.py`) — this module validates only the
aggregated grid and its no-data mask.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it.
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime  # noqa: TC003 - pydantic must resolve this at runtime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import structlog
import xarray as xr
from pydantic import BaseModel

from scripts.dhm_precip.era5_errors import Era5OrographyError, Era5StorageError
from scripts.dhm_precip.era5_manifest import checksum_file, publish_atomic, tmp_path_for
from scripts.dhm_precip.era5_orography_spec import (
    G0_M_PER_S2,
    OROGRAPHY_SCHEMA_VERSION,
    OrographyConversionRule,
    OrographySpec,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

log = structlog.get_logger(__name__)

_GRID_TOLERANCE_DEG = 1e-6
DEFAULT_NO_DATA_THRESHOLD_FRACTION = 0.05

# D3a Branch A magnitude discrimination — CORRECTED 2026-08-16 (blocker B1).
# The original band bounded the field MINIMUM at 1e4, but the acquired box
# (26-31 N) contains the Terai lowlands at ~60 m, i.e. ~588 m2 s-2, so
# `10000 <= 588` is false and Branch A raised on EVERY real field. The
# minimum carries no signal: it is small under BOTH units. The MAXIMUM is
# unambiguous — Everest is 8,849 m but 86,779 m2 s-2 — so that is what
# classifies the field, and the declared `conversion_rule` must AGREE with
# the classification.
_GEOPOTENTIAL_MAX_THRESHOLD = 9_999.0

# The only surviving lower bound: reject the PHYSICALLY IMPOSSIBLE, which is
# what catches an unmasked no-data sentinel such as -32768.
_MIN_PLAUSIBLE_METRES = -500.0
_MIN_PLAUSIBLE_GEOPOTENTIAL = -5_000.0

_OROGRAPHY_CODE_VERSION = "1"

# M-8 / D7 — the FROZEN orography raster schema, hashed into
# `orography_identity` so a silent change of variable name, dims, dtype or
# mask variable forces regeneration instead of being adopted invisibly.
#
# MAJOR (2026-08-17) — this schema was incomplete (no encoding/compression/
# fill policy, no required-attrs list) and, worse, was never actually
# ENFORCED: write-time validation only checked that `orography_elev_m`
# existed, and reuse (`verify_orography_materialisation`) never reopened the
# raster at all — it only compared hashes, so a record claiming the wrong
# `raster_schema_version`, or a consistently rehashed malformed raster, was
# silently trusted. `assert_orography_raster_schema` below is now the ONE
# validator run both after write and on every reuse.
OROGRAPHY_RASTER_SCHEMA: dict[str, object] = {
    "elevation_variable": "orography_elev_m",
    "elevation_dtype": "float32",
    "elevation_units": "m",
    "no_data_fraction_variable": "no_data_fraction",
    "no_data_fraction_dtype": "float32",
    "mask_variable": "no_data_flag",
    "mask_dtype": "bool",
    "dims": ["latitude", "longitude"],
    "schema_version": OROGRAPHY_SCHEMA_VERSION,
    "encoding": {
        "orography_elev_m": {"zlib": True, "complevel": 4, "_FillValue": "NaN"},
        "no_data_fraction": {"zlib": True, "complevel": 4, "_FillValue": "NaN"},
        "no_data_flag": {"zlib": True, "complevel": 4},
    },
    "required_attrs": [
        "orography_source",
        "orography_product_id",
        "orography_product_version",
        "orography_vertical_reference",
        "orography_aggregation_rule",
        "orography_schema_version",
    ],
}


def _assert_orography_encoding(ds: xr.Dataset) -> None:
    """MAJOR (2026-08-17 review) — the frozen schema's `encoding` block
    (compression/fill policy) is hashed into `orography_identity` but was
    never enforced on reopen: a rehashed raster written with uncompressed
    encoding or a different fill policy still passed reuse, because nothing
    compared the reopened `.encoding` against the declared schema."""
    schema_encoding: dict[str, dict[str, object]] = OROGRAPHY_RASTER_SCHEMA[  # type: ignore[assignment]
        "encoding"
    ]
    for var_name, expected in schema_encoding.items():
        if var_name not in ds:
            continue
        enc = ds[var_name].encoding
        expected_zlib = bool(expected.get("zlib", False))
        if bool(enc.get("zlib")) != expected_zlib:
            raise Era5OrographyError(
                f"orography raster variable {var_name!r} has zlib="
                f"{enc.get('zlib')!r} on reopen, expected {expected_zlib!r} "
                "(M-8's frozen encoding spec)"
            )
        expected_complevel = expected.get("complevel")
        if (
            expected_complevel is not None
            and enc.get("complevel") != expected_complevel
        ):
            raise Era5OrographyError(
                f"orography raster variable {var_name!r} has complevel="
                f"{enc.get('complevel')!r} on reopen, expected "
                f"{expected_complevel!r} (M-8's frozen encoding spec)"
            )
        if expected.get("_FillValue") == "NaN":
            fill = enc.get("_FillValue")
            if fill is None or not np.isnan(float(fill)):
                raise Era5OrographyError(
                    f"orography raster variable {var_name!r} has "
                    f"_FillValue={fill!r} on reopen, expected NaN (M-8's "
                    "frozen encoding spec)"
                )


def _assert_orography_provenance_matches_spec(
    ds: xr.Dataset, *, spec: OrographySpec
) -> None:
    """MAJOR (2026-08-17 review) — required-attrs was checked for PRESENCE
    only, never for agreement with the CURRENT frozen spec, so a raster
    carrying wrong (but present) provenance values would pass reuse."""
    expected: dict[str, str] = {
        "orography_source": str(spec.source),
        "orography_product_id": spec.product_id,
        "orography_product_version": spec.product_version,
        "orography_vertical_reference": str(spec.vertical_reference),
        "orography_aggregation_rule": spec.aggregation_rule_id,
        "orography_schema_version": OROGRAPHY_SCHEMA_VERSION,
    }
    mismatched = {
        k: (ds.attrs.get(k), v) for k, v in expected.items() if ds.attrs.get(k) != v
    }
    if mismatched:
        raise Era5OrographyError(
            "orography raster attrs disagree with the current OrographySpec: "
            f"{mismatched} (M-8)"
        )


def assert_orography_raster_schema(
    ds: xr.Dataset,
    *,
    spec: OrographySpec | None = None,
    expected_lat: np.ndarray | None = None,
    expected_lon: np.ndarray | None = None,
) -> None:
    """M-8/D7 — the ONE frozen-schema validator, run both after write
    (`write_orography_raster`) and on every reuse
    (`verify_orography_materialisation`): required variables, exact
    dims/order, dtypes, the mask/no-data invariant (a NaN elevation cell
    must be flagged), and the required provenance attrs. A schema
    divergence here is exactly the drift `orography_identity` claims to
    cover but that nothing previously checked on reopen.

    `spec`/`expected_lat`/`expected_lon` are optional so direct structural
    tests can call this without a full spec+grid in hand; every REAL
    production call site (write and reuse) supplies all three, so the
    encoding, provenance-value and coordinate-vector checks below always run
    in practice (2026-08-17 review — a rehashed raster with uncompressed
    encoding, shifted coordinates or wrong provenance values used to pass
    reuse despite the identity claiming otherwise)."""
    variable_checks = (
        (OROGRAPHY_RASTER_SCHEMA["elevation_variable"], "elevation_dtype"),
        (
            OROGRAPHY_RASTER_SCHEMA["no_data_fraction_variable"],
            "no_data_fraction_dtype",
        ),
        (OROGRAPHY_RASTER_SCHEMA["mask_variable"], "mask_dtype"),
    )
    expected_dims = tuple(OROGRAPHY_RASTER_SCHEMA["dims"])  # type: ignore[arg-type]
    for var_name, dtype_key in variable_checks:
        if var_name not in ds:
            raise Era5OrographyError(
                f"orography raster missing required variable {var_name!r} (M-8)"
            )
        var = ds[var_name]
        if tuple(var.dims) != expected_dims:
            raise Era5OrographyError(
                f"orography raster variable {var_name!r} has dims "
                f"{tuple(var.dims)}, expected {expected_dims} (M-8)"
            )
        expected_dtype = OROGRAPHY_RASTER_SCHEMA[dtype_key]
        if str(var.dtype) != expected_dtype:
            raise Era5OrographyError(
                f"orography raster variable {var_name!r} has dtype "
                f"{var.dtype}, expected {expected_dtype} (M-8)"
            )

    required_attrs: list[str] = OROGRAPHY_RASTER_SCHEMA["required_attrs"]  # type: ignore[assignment]
    missing_attrs = [a for a in required_attrs if a not in ds.attrs]
    if missing_attrs:
        raise Era5OrographyError(
            f"orography raster is missing required attrs {missing_attrs} (M-8)"
        )

    elevation_var = OROGRAPHY_RASTER_SCHEMA["elevation_variable"]
    mask_var = OROGRAPHY_RASTER_SCHEMA["mask_variable"]
    # P7 (MAJOR, 2026-08-17 review) — `elevation_units` is hashed into
    # `orography_identity` (`raster_schema`) but was never checked on
    # reopen: a rehashed raster written under a different declared unit
    # still passed both write-time and reuse validation.
    actual_units = ds[elevation_var].attrs.get("units")
    expected_units = OROGRAPHY_RASTER_SCHEMA["elevation_units"]
    if actual_units != expected_units:
        raise Era5OrographyError(
            f"orography raster variable {elevation_var!r} has units "
            f"{actual_units!r} on reopen, expected {expected_units!r} (M-8's "
            "frozen raster schema)"
        )
    elev = np.asarray(ds[elevation_var].values)
    flag = np.asarray(ds[mask_var].values).astype(bool)
    nan_but_unflagged = np.isnan(elev) & ~flag
    if bool(nan_but_unflagged.any()):
        raise Era5OrographyError(
            "orography raster has NaN elevation cell(s) not marked in "
            "'no_data_flag' — the mask/no-data invariant is violated (M-8)"
        )

    if spec is not None:
        _assert_orography_encoding(ds)
        _assert_orography_provenance_matches_spec(ds, spec=spec)
    if expected_lat is not None and expected_lon is not None:
        assert_grid_matches(
            np.asarray(ds["latitude"].values),
            np.asarray(ds["longitude"].values),
            expected_lat=expected_lat,
            expected_lon=expected_lon,
        )


def orography_route_identity(spec: OrographySpec) -> str:
    """D7 — sha256(canonical-JSON of every `OrographySpec` field, plus the
    orography schema/code versions). A version bump alone forces
    regeneration, mirroring `era5_manifest.transform_identity`."""
    payload: dict[str, object] = {
        "source": str(spec.source),
        "product_id": spec.product_id,
        "product_version": spec.product_version,
        "download_url": spec.download_url,
        "licence_name": spec.licence_name,
        "licence_version": spec.licence_version,
        "licence_url": spec.licence_url,
        "source_crs": spec.source_crs,
        "vertical_reference": str(spec.vertical_reference),
        "units": spec.units,
        "no_data_sentinel": spec.no_data_sentinel,
        "aggregation_rule_id": spec.aggregation_rule_id,
        "conversion_rule": str(spec.conversion_rule),
        "probe_date": spec.probe_date.isoformat(),
        "rejected_candidates": [
            {"product_id": c.product_id, "reason": c.reason}
            for c in spec.rejected_candidates
        ],
        "orography_schema_version": OROGRAPHY_SCHEMA_VERSION,
        "orography_code_version": _OROGRAPHY_CODE_VERSION,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def orography_identity(
    *,
    route_identity: str,
    source_file_sha256s: Sequence[str],
    raster_sha256: str,
) -> str:
    """D7 (CORRECTED 2026-08-16, blocker B2) — the composite identity
    `extraction_identity` consumes: the route identity PLUS every
    `OrographySourceRecord` file sha256, the derived raster's own sha256 and
    the frozen raster schema (M-8). A route-only identity let a source file
    whose bytes changed at the same URL keep its identity, so the stale
    raster was silently reused."""
    payload: dict[str, object] = {
        "orography_route_identity": route_identity,
        "source_file_sha256s": sorted(source_file_sha256s),
        "raster_sha256": raster_sha256,
        "raster_schema": OROGRAPHY_RASTER_SCHEMA,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- OrographySourceRecord (1b's deliverable — what was materialised) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class DownloadedFileRecord:
    path: str
    """Relative to the data root."""
    sha256: str
    size_bytes: int


@dataclass(frozen=True, kw_only=True, slots=True)
class OrographySourceRecord:
    """D3a/D7 — the record of what was actually MATERIALISED.

    It is written TWICE, because its two halves become knowable at different
    moments and the plan requires the first half to be durable on its own
    (1b: "a first fetch, with no prior record, succeeds and writes one"):

    1. `fetch_orography_source` writes the FETCH half — the route identity,
       every downloaded file's sha256/size, and the injected-clock fetch
       time. The derived raster does not exist yet, so the composite
       identity fields are `None`.
    2. `materialise_orography` rewrites the same record with the derived
       raster's path/sha256/schema version and the composite
       `orography_identity` (D7), which is the one `extraction_identity`
       consumes.

    Persisting after step 1 is what makes the re-fetch guard real: an
    unexplained change in a "static" source is caught even when a previous
    run died between the download and the aggregation.
    """

    orography_route_identity: str
    downloaded_files: tuple[DownloadedFileRecord, ...]
    fetched_at: datetime
    orography_identity: str | None = None
    raster_path: str | None = None
    """Relative to the data root."""
    raster_sha256: str | None = None
    raster_schema_version: str | None = None


def orography_dir(data_root: Path) -> Path:
    return data_root / "era5_land" / "orography"


def orography_source_record_path(data_root: Path) -> Path:
    return orography_dir(data_root) / "orography_source_record.json"


def orography_raster_path(data_root: Path, *, route_identity: str) -> Path:
    """Keyed by the ROUTE identity, not the composite one — the composite
    identity covers the raster's own sha256 (D7), so it cannot also name the
    file that produces it."""
    return orography_dir(data_root) / f"{route_identity}.nc"


class _DownloadedFileRecordModel(BaseModel):
    path: str
    sha256: str
    size_bytes: int


class _OrographySourceRecordModel(BaseModel):
    orography_route_identity: str
    downloaded_files: list[_DownloadedFileRecordModel]
    fetched_at: datetime
    orography_identity: str | None = None
    raster_path: str | None = None
    raster_sha256: str | None = None
    raster_schema_version: str | None = None


def write_orography_source_record(record: OrographySourceRecord, path: Path) -> None:
    model = _OrographySourceRecordModel(
        orography_route_identity=record.orography_route_identity,
        orography_identity=record.orography_identity,
        downloaded_files=[
            _DownloadedFileRecordModel(**asdict(f)) for f in record.downloaded_files
        ],
        raster_path=record.raster_path,
        raster_sha256=record.raster_sha256,
        raster_schema_version=record.raster_schema_version,
        fetched_at=record.fetched_at,
    )
    tmp = tmp_path_for(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(model.model_dump_json(indent=2))
        os.replace(tmp, path)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to write orography source record at {path}: {exc}"
        ) from exc


def read_orography_source_record(path: Path) -> OrographySourceRecord | None:
    try:
        exists = path.exists()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to stat orography source record at {path}: {exc}"
        ) from exc
    if not exists:
        return None
    try:
        text = path.read_text()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to read orography source record at {path}: {exc}"
        ) from exc
    try:
        model = _OrographySourceRecordModel.model_validate_json(text)
    except ValueError as exc:
        raise Era5StorageError(
            f"orography source record at {path} is unreadable: {exc}"
        ) from exc
    return OrographySourceRecord(
        orography_route_identity=model.orography_route_identity,
        downloaded_files=tuple(
            DownloadedFileRecord(**f.model_dump()) for f in model.downloaded_files
        ),
        fetched_at=model.fetched_at,
        orography_identity=model.orography_identity,
        raster_path=model.raster_path,
        raster_sha256=model.raster_sha256,
        raster_schema_version=model.raster_schema_version,
    )


# --- fetch (1b) ---


@runtime_checkable
class OrographyDownloader(Protocol):
    """The single injected fetch seam (mirrors `era5_acquire.CdsClient`) — a
    fake implementation writes deterministic bytes for tests; a real
    implementation (CDS for Branch A, plain HTTP for Branch B) is an
    operator-run step, untested against the live service here (constraint
    5's precedent: `era5_acquire.RealCdsClient` is untested for the same
    reason)."""

    def download(self, *, spec: OrographySpec, dest_dir: Path) -> tuple[Path, ...]:
        """Materialise every file the spec's route implies into `dest_dir`,
        returning their paths."""
        ...


def fetch_orography_source(
    spec: OrographySpec,
    *,
    downloader: OrographyDownloader,
    data_root: Path,
    clock: Callable[[], datetime],
) -> OrographySourceRecord:
    """1b — fetch per the frozen spec, then WRITE the fetch half of the
    `OrographySourceRecord` (D3a). A re-run verifies observed hashes against
    the EXISTING record (an unexplained change in a "static" source is a
    typed failure) rather than assuming a prior fetch was good; that guard
    only binds because the first fetch persists a record even though the
    derived raster does not exist yet."""
    route_identity = orography_route_identity(spec)
    existing = read_orography_source_record(orography_source_record_path(data_root))

    dest_dir = orography_dir(data_root) / "raw"
    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths = downloader.download(spec=spec, dest_dir=dest_dir)
    if not downloaded_paths:
        raise Era5OrographyError("downloader returned no files")

    files: list[DownloadedFileRecord] = []
    for path in downloaded_paths:
        # Checksum before the payload is decoded (m-2): this reads raw
        # bytes only, never opens the file as a raster.
        sha256 = checksum_file(path)
        files.append(
            DownloadedFileRecord(
                path=str(path.relative_to(data_root)),
                sha256=sha256,
                size_bytes=path.stat().st_size,
            )
        )

    if existing is not None and existing.orography_route_identity == route_identity:
        # MAJOR (2026-08-17 review) — this used to compare hashes only for
        # paths present in BOTH records: an ADDED file was never checked
        # against anything, and a REMOVED file was never checked at all
        # (the loop only ever walked the NEW `files`). Compare the complete
        # {path: (sha256, size)} mapping for equality so an added, removed
        # or renamed file is caught too, not only a changed one.
        existing_by_path = {
            f.path: (f.sha256, f.size_bytes) for f in existing.downloaded_files
        }
        new_by_path = {f.path: (f.sha256, f.size_bytes) for f in files}
        if existing_by_path != new_by_path:
            added = sorted(set(new_by_path) - set(existing_by_path))
            removed = sorted(set(existing_by_path) - set(new_by_path))
            changed = sorted(
                p
                for p in set(existing_by_path) & set(new_by_path)
                if existing_by_path[p] != new_by_path[p]
            )
            raise Era5OrographyError(
                "re-fetch of the orography source disagrees with the "
                "existing OrographySourceRecord under the SAME route — "
                f"added={added} removed={removed} changed={changed} (an "
                "unexplained change in a static source)"
            )

    record = OrographySourceRecord(
        orography_route_identity=route_identity,
        downloaded_files=tuple(files),
        fetched_at=clock(),
    )
    write_orography_source_record(record, orography_source_record_path(data_root))
    log.info(
        "era5.orography.fetched",
        orography_route_identity=route_identity,
        n_files=len(files),
    )
    return record


# --- convert (D3a) ---


def classify_field_units(field_max: float) -> OrographyConversionRule:
    """D3a (CORRECTED 2026-08-16) — discriminate on the MAXIMUM. Everest is
    8,849 m but 86,779 m2 s-2, so a max above ~1e4 can only be geopotential;
    the field MINIMUM carries no signal at all (the Terai is ~60 m / ~588
    m2 s-2, small under both units), which is what made the original
    min-bound raise on every real field."""
    if field_max > _GEOPOTENTIAL_MAX_THRESHOLD:
        return OrographyConversionRule.GEOPOTENTIAL_G0
    return OrographyConversionRule.IDENTITY


def convert_field(raw_values: np.ndarray, *, spec: OrographySpec) -> np.ndarray:
    """D3a — apply the frozen conversion rule, with the unit classification
    checked BEFORE the conversion is trusted: a retrieved field whose
    magnitude classifies as the other unit than the one declared is a typed
    failure, never a silent divide."""
    finite = raw_values[np.isfinite(raw_values)]
    if finite.size == 0:
        raise Era5OrographyError("source raster has no finite values to convert")
    field_min, field_max = float(np.min(finite)), float(np.max(finite))

    observed = classify_field_units(field_max)
    if observed != spec.conversion_rule:
        raise Era5OrographyError(
            f"source field maximum {field_max} classifies the field as "
            f"{observed.value!r}, but the frozen OrographySpec declares "
            f"{spec.conversion_rule.value!r} — the declared conversion rule "
            "must agree with the observed magnitude (D3a); refusing to "
            "convert"
        )

    if spec.conversion_rule == OrographyConversionRule.GEOPOTENTIAL_G0:
        if field_min < _MIN_PLAUSIBLE_GEOPOTENTIAL:
            raise Era5OrographyError(
                f"source field minimum {field_min} m2 s-2 is physically "
                f"impossible for surface geopotential (< "
                f"{_MIN_PLAUSIBLE_GEOPOTENTIAL}) — an unmasked no-data "
                "sentinel is the likely cause (D3a)"
            )
        return raw_values / G0_M_PER_S2

    # OrographyConversionRule.IDENTITY
    if field_min < _MIN_PLAUSIBLE_METRES:
        raise Era5OrographyError(
            f"source field minimum {field_min} m is physically impossible "
            f"for a surface elevation (< {_MIN_PLAUSIBLE_METRES}) — an "
            "unmasked no-data sentinel is the likely cause (D3a)"
        )
    return raw_values


# --- aggregate (D3a) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class AggregationResult:
    values: np.ndarray
    """(n_target_lat, n_target_lon) — arithmetic mean of the valid source
    cells contained by each target cell, or NaN per the no-data policy."""
    no_data_fraction: np.ndarray
    """Same shape — fraction of contributing source cells that were NaN."""
    flagged: np.ndarray
    """Same shape, bool — True whenever any contributing source cell was
    NaN (including the NaN-emitting case)."""


def aggregate_to_grid(
    source_values: np.ndarray,
    *,
    source_lat: np.ndarray,
    source_lon: np.ndarray,
    target_lat: np.ndarray,
    target_lon: np.ndarray,
    target_spacing_deg: float,
    no_data_threshold_fraction: float = DEFAULT_NO_DATA_THRESHOLD_FRACTION,
) -> AggregationResult:
    """D3a — `mean_of_contained_cells`: the UNWEIGHTED arithmetic mean of
    the source cells whose centres fall inside the target 0.1 deg cell.

    Named for what it is (corrected 2026-08-16). The rule was previously
    called "area-weighted", which the implementation never was — and the
    plan's accepted resolution is to correct the CLAIM rather than
    implement weighting, because `cos(lat)` varies ~0.17 % across one 0.1
    deg cell, negligible against hundreds of metres of intra-cell relief.
    The manifest and the raster attrs must therefore not assert a method
    that was not used.

    No-data policy: any contributing NaN source cell aggregates over the
    valid remainder and sets `flagged`; >`no_data_threshold_fraction`
    no-data, or zero valid source cells, emits NaN (still flagged).
    """
    n_tlat, n_tlon = target_lat.size, target_lon.size
    lat_idx = np.round((source_lat - target_lat[0]) / target_spacing_deg).astype(int)
    lon_idx = np.round((source_lon - target_lon[0]) / target_spacing_deg).astype(int)

    flat_i = np.broadcast_to(lat_idx[:, None], source_values.shape)
    flat_j = np.broadcast_to(lon_idx[None, :], source_values.shape)
    in_range = (flat_i >= 0) & (flat_i < n_tlat) & (flat_j >= 0) & (flat_j < n_tlon)
    flat_idx_full = np.where(in_range, flat_i * n_tlon + flat_j, 0)

    is_nan = np.isnan(source_values)
    n_cells = n_tlat * n_tlon

    total_mask = in_range
    n_total = np.bincount(flat_idx_full[total_mask], minlength=n_cells)

    valid_mask = in_range & ~is_nan
    valid_idx = flat_idx_full[valid_mask]
    n_valid = np.bincount(valid_idx, minlength=n_cells)
    sum_valid = np.bincount(
        valid_idx, weights=source_values[valid_mask], minlength=n_cells
    )

    n_total_f = n_total.astype(float)
    no_data_fraction = np.where(
        n_total > 0, 1.0 - n_valid / np.maximum(n_total_f, 1.0), 1.0
    )
    flagged = no_data_fraction > 0.0

    values = np.full(n_cells, np.nan)
    ok = (n_valid > 0) & (no_data_fraction <= no_data_threshold_fraction)
    values[ok] = sum_valid[ok] / n_valid[ok]

    return AggregationResult(
        values=values.reshape(n_tlat, n_tlon),
        no_data_fraction=no_data_fraction.reshape(n_tlat, n_tlon),
        flagged=flagged.reshape(n_tlat, n_tlon),
    )


def assert_grid_matches(
    actual_lat: np.ndarray,
    actual_lon: np.ndarray,
    *,
    expected_lat: np.ndarray,
    expected_lon: np.ndarray,
) -> None:
    """D3a — the aggregated raster must reopen with the identical lat/lon
    coordinate vectors as a D9-schema product, element-wise to 1e-9 deg."""
    tol = 1e-9
    if actual_lat.shape != expected_lat.shape or not np.allclose(
        actual_lat, expected_lat, atol=tol, rtol=0
    ):
        raise Era5OrographyError(
            "aggregated orography latitude vector does not match the "
            f"expected D9 product grid within {tol} deg"
        )
    if actual_lon.shape != expected_lon.shape or not np.allclose(
        actual_lon, expected_lon, atol=tol, rtol=0
    ):
        raise Era5OrographyError(
            "aggregated orography longitude vector does not match the "
            f"expected D9 product grid within {tol} deg"
        )


# --- write (D5-style atomic publish) ---


def build_orography_dataset(
    aggregation: AggregationResult,
    *,
    target_lat: np.ndarray,
    target_lon: np.ndarray,
    spec: OrographySpec,
) -> xr.Dataset:
    ds = xr.Dataset(
        {
            "orography_elev_m": (
                ["latitude", "longitude"],
                aggregation.values.astype(np.float32),
            ),
            "no_data_fraction": (
                ["latitude", "longitude"],
                aggregation.no_data_fraction.astype(np.float32),
            ),
            "no_data_flag": (["latitude", "longitude"], aggregation.flagged),
        },
        coords={"latitude": target_lat, "longitude": target_lon},
    )
    # P7 (MAJOR, 2026-08-17 review) — `elevation_units` is hashed whole
    # (`OROGRAPHY_RASTER_SCHEMA`, via `orography_identity`'s `raster_schema`
    # field) but used to be a SEPARATE hardcoded `"m"` literal here,
    # independent of the schema's own `elevation_units` entry: a schema
    # change would move the identity without moving a single on-disk byte.
    # Reading it FROM the schema makes the hashed value the one the writer
    # actually uses.
    ds["orography_elev_m"].attrs["units"] = str(
        OROGRAPHY_RASTER_SCHEMA["elevation_units"]
    )
    ds.attrs.update(
        {
            "orography_source": str(spec.source),
            "orography_product_id": spec.product_id,
            "orography_product_version": spec.product_version,
            "orography_vertical_reference": str(spec.vertical_reference),
            "orography_aggregation_rule": spec.aggregation_rule_id,
            "orography_schema_version": OROGRAPHY_SCHEMA_VERSION,
        }
    )
    return ds


def _orography_write_encoding() -> dict[str, dict[str, object]]:
    """M-8 — the on-disk encoding, derived from the ONE frozen
    `OROGRAPHY_RASTER_SCHEMA` rather than duplicated as separate literals at
    the write call site."""
    schema_encoding: dict[str, dict[str, object]] = OROGRAPHY_RASTER_SCHEMA[  # type: ignore[assignment]
        "encoding"
    ]
    encoding: dict[str, dict[str, object]] = {}
    for var_name, spec in schema_encoding.items():
        entry = dict(spec)
        if entry.get("_FillValue") == "NaN":
            entry["_FillValue"] = float("nan")
        encoding[var_name] = entry
    return encoding


def write_orography_raster(
    ds: xr.Dataset,
    *,
    data_root: Path,
    route_identity: str,
    spec: OrographySpec | None = None,
) -> str:
    """Atomic tmp -> reopen-and-validate -> `os.replace`; returns the sha256
    of the published file.

    MAJOR (2026-08-17) — this used to write with NO explicit encoding at
    all (no compression, no declared fill value) and validate only that
    `orography_elev_m` was present on reopen. It now writes from the single
    frozen `OROGRAPHY_RASTER_SCHEMA` encoding and runs the SAME full-schema
    validator (`assert_orography_raster_schema`) that reuse now also
    runs — one validator, never two that can drift. `spec`, when supplied
    (every real caller does — `materialise_orography` below), also validates
    the written encoding and provenance attrs match the CURRENT frozen spec,
    not merely their own presence."""
    final_path = orography_raster_path(data_root, route_identity=route_identity)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_path_for(final_path)
    tmp_path.unlink(missing_ok=True)
    # Only encode variables actually present: an encoding entry for a
    # variable the dataset lacks makes `to_netcdf` raise a raw `KeyError`
    # before the reopen-and-validate step below ever runs, and a missing
    # variable is exactly the kind of schema violation
    # `assert_orography_raster_schema` exists to report as a typed error.
    encoding = {k: v for k, v in _orography_write_encoding().items() if k in ds}
    ds.to_netcdf(tmp_path, engine="h5netcdf", encoding=encoding)
    try:
        with xr.open_dataset(tmp_path, engine="h5netcdf") as reopened:
            loaded = reopened.load()
            assert_orography_raster_schema(loaded, spec=spec)
        sha256 = checksum_file(tmp_path)
        publish_atomic(tmp_path, final_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return sha256


def materialise_orography(
    spec: OrographySpec,
    *,
    downloader: OrographyDownloader,
    raw_reader: Callable[[Path], tuple[np.ndarray, np.ndarray, np.ndarray]],
    data_root: Path,
    expected_lat: np.ndarray,
    expected_lon: np.ndarray,
    target_spacing_deg: float,
    clock: Callable[[], datetime],
    no_data_threshold_fraction: float = DEFAULT_NO_DATA_THRESHOLD_FRACTION,
) -> OrographySourceRecord:
    """1b's full driver: fetch -> convert -> aggregate -> write -> reopen ->
    revalidate against the D9 product's own grid, then write the
    `OrographySourceRecord` carrying BOTH D7 identities. `raw_reader`
    returns (values, lat, lon) for the first downloaded file — injected so
    tests never need a real GRIB/GeoTIFF/NetCDF parser for a specific
    format."""
    fetched = fetch_orography_source(
        spec, downloader=downloader, data_root=data_root, clock=clock
    )
    route_identity = fetched.orography_route_identity
    raw_path = data_root / fetched.downloaded_files[0].path
    values, src_lat, src_lon = raw_reader(raw_path)
    converted = convert_field(values, spec=spec)
    aggregation = aggregate_to_grid(
        converted,
        source_lat=src_lat,
        source_lon=src_lon,
        target_lat=expected_lat,
        target_lon=expected_lon,
        target_spacing_deg=target_spacing_deg,
        no_data_threshold_fraction=no_data_threshold_fraction,
    )
    ds = build_orography_dataset(
        aggregation, target_lat=expected_lat, target_lon=expected_lon, spec=spec
    )
    raster_sha256 = write_orography_raster(
        ds, data_root=data_root, route_identity=route_identity, spec=spec
    )
    final_path = orography_raster_path(data_root, route_identity=route_identity)
    with xr.open_dataset(final_path, engine="h5netcdf") as reopened:
        reopened.load()
        assert_grid_matches(
            reopened["latitude"].values,
            reopened["longitude"].values,
            expected_lat=expected_lat,
            expected_lon=expected_lon,
        )
    record = replace(
        fetched,
        orography_identity=orography_identity(
            route_identity=route_identity,
            source_file_sha256s=[f.sha256 for f in fetched.downloaded_files],
            raster_sha256=raster_sha256,
        ),
        raster_path=str(final_path.relative_to(data_root)),
        raster_sha256=raster_sha256,
        raster_schema_version=OROGRAPHY_SCHEMA_VERSION,
    )
    write_orography_source_record(record, orography_source_record_path(data_root))
    return record


def verify_orography_materialisation(
    record: OrographySourceRecord,
    *,
    data_root: Path,
    spec: OrographySpec,
    expected_lat: np.ndarray,
    expected_lon: np.ndarray,
) -> str:
    """D7 (CORRECTED 2026-08-16, blocker B2) — "a materialised raster is
    never trusted because a file of the right name exists". On EVERY run,
    re-verify every source file's sha256 against the record, the raster's
    own sha256 against the record, and that the composite identity
    recomputes from those bytes. Any mismatch is a typed failure, never a
    silent reuse. Returns the VERIFIED composite `orography_identity`, so a
    caller cannot reach it without having verified it.

    D7 (BLOCKER, 2026-08-17) — the route identity is recomputed from the
    CURRENT frozen `spec`, never read back from the record: recomputing from
    the stored value only re-confirms a stale one. A record materialised
    under a different route cannot be verified here; the caller must
    re-materialise (a changed spec is a legitimate event, so the CLI treats
    it as one — but reaching this function with a stale record is a bug, and
    is typed as such).

    MAJOR (2026-08-17) — reuse used to trust `raster_schema_version` and
    the raster's byte-for-byte SHAPE without ever reopening it: a record
    claiming the wrong schema version, or a consistently rehashed but
    schema-broken raster, was silently trusted. Every reuse now (a) checks
    the record's claimed `raster_schema_version` against the CURRENT frozen
    schema, and (b) reopens the raster and runs the SAME
    `assert_orography_raster_schema` validator write-time uses.

    MAJOR (2026-08-17 review) — that reopen used to skip the encoding,
    provenance-attr-value and coordinate-vector checks entirely (it called
    `assert_orography_raster_schema` with no `spec`/grid), so a rehashed
    raster with uncompressed encoding, shifted coordinates, or provenance
    attrs that disagree with the CURRENT spec still passed reuse. `spec` was
    already required here; `expected_lat`/`expected_lon` are now required
    too, and every check `assert_orography_raster_schema` can run is run."""
    current_route_identity = orography_route_identity(spec)
    if record.orography_route_identity != current_route_identity:
        raise Era5OrographyError(
            f"OrographySourceRecord was materialised under route "
            f"{record.orography_route_identity}, but the CURRENT frozen "
            f"OrographySpec resolves to {current_route_identity} — the route "
            "changed, so the recorded raster does not carry the provenance "
            "the spec now states; re-materialise (D7)"
        )

    if (
        record.orography_identity is None
        or record.raster_path is None
        or record.raster_sha256 is None
    ):
        raise Era5OrographyError(
            "OrographySourceRecord carries only the fetch half (no derived "
            "raster) — the orography was downloaded but never materialised; "
            "re-run `materialise_orography` (D3a/1b)"
        )

    for downloaded in record.downloaded_files:
        path = data_root / downloaded.path
        if not path.exists():
            raise Era5OrographyError(
                f"orography source file {downloaded.path!r} recorded in the "
                "OrographySourceRecord is missing from disk"
            )
        actual = checksum_file(path)
        if actual != downloaded.sha256:
            raise Era5OrographyError(
                f"orography source file {downloaded.path!r} has sha256 "
                f"{actual}, but the OrographySourceRecord says "
                f"{downloaded.sha256} — the materialised source changed "
                "under us (D7)"
            )

    raster_path = data_root / record.raster_path
    if not raster_path.exists():
        raise Era5OrographyError(
            f"derived orography raster {record.raster_path!r} recorded in the "
            "OrographySourceRecord is missing from disk"
        )
    raster_sha256 = checksum_file(raster_path)
    if raster_sha256 != record.raster_sha256:
        raise Era5OrographyError(
            f"derived orography raster {record.raster_path!r} has sha256 "
            f"{raster_sha256}, but the OrographySourceRecord says "
            f"{record.raster_sha256} — refusing to reuse a stale raster (D7)"
        )

    if record.raster_schema_version != OROGRAPHY_SCHEMA_VERSION:
        raise Era5OrographyError(
            f"derived orography raster {record.raster_path!r} was recorded "
            f"under raster_schema_version={record.raster_schema_version!r}, "
            f"but the CURRENT frozen schema is "
            f"{OROGRAPHY_SCHEMA_VERSION!r} — refusing to reuse a raster "
            "written to a stale schema (M-8)"
        )
    with xr.open_dataset(raster_path, engine="h5netcdf") as reopened:
        assert_orography_raster_schema(
            reopened.load(),
            spec=spec,
            expected_lat=expected_lat,
            expected_lon=expected_lon,
        )

    recomputed = orography_identity(
        # From the CURRENT spec (equal to the record's, asserted above) —
        # never from the identity stored in the record.
        route_identity=current_route_identity,
        source_file_sha256s=[f.sha256 for f in record.downloaded_files],
        raster_sha256=raster_sha256,
    )
    if recomputed != record.orography_identity:
        raise Era5OrographyError(
            f"orography_identity does not recompute from the materialised "
            f"bytes: recorded {record.orography_identity}, recomputed "
            f"{recomputed} (D7)"
        )
    return recomputed
