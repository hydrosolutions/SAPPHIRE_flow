"""Plan 174 (M-A5) task 1b — fetch, convert, aggregate and validate the
orography raster per the frozen `OrographySpec` (1a).

D3a: `OrographySpec` (1a) describes the ROUTE; this module describes what
was actually MATERIALISED (`OrographySourceRecord`) and performs the
conversion + area-weighted aggregation to the ERA5-Land 0.1 deg grid, with
the no-data policy and the exact-grid-vector post-condition.

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
from dataclasses import asdict, dataclass
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
    from collections.abc import Callable
    from pathlib import Path

log = structlog.get_logger(__name__)

_GRID_TOLERANCE_DEG = 1e-6
DEFAULT_NO_DATA_THRESHOLD_FRACTION = 0.05
# D3a Branch A magnitude sanity, per the plan's own stated bands ("Nepal
# box: metres ⇒ tens to thousands; geopotential ⇒ 10⁴-10⁵"). Residual risk
# (recorded, not silently fixed): Nepal's measured elevation range (67-3700
# m, "Measured facts" table) implies REAL geopotential as low as ~657
# (67 * g0), below this band's 1e4 floor — a genuine low-elevation station
# cell could trip this as a false positive. Best-effort defense-in-depth
# per D3a's own framing as a "sanity check", not a guaranteed classifier;
# flagged for the real-data operator run (4b) to watch for.
_METRES_PLAUSIBLE_RANGE = (0.0, 9999.0)
_GEOPOTENTIAL_PLAUSIBLE_RANGE = (10000.0, 100000.0)

_OROGRAPHY_CODE_VERSION = "1"


def orography_identity(spec: OrographySpec) -> str:
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


# --- OrographySourceRecord (1b's deliverable — what was materialised) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class DownloadedFileRecord:
    path: str
    """Relative to the data root."""
    sha256: str
    size_bytes: int


@dataclass(frozen=True, kw_only=True, slots=True)
class OrographySourceRecord:
    orography_identity: str
    downloaded_files: tuple[DownloadedFileRecord, ...]
    fetched_at: datetime


def orography_dir(data_root: Path) -> Path:
    return data_root / "era5_land" / "orography"


def orography_source_record_path(data_root: Path) -> Path:
    return orography_dir(data_root) / "orography_source_record.json"


def orography_raster_path(data_root: Path, *, identity: str) -> Path:
    return orography_dir(data_root) / f"{identity}.nc"


class _DownloadedFileRecordModel(BaseModel):
    path: str
    sha256: str
    size_bytes: int


class _OrographySourceRecordModel(BaseModel):
    orography_identity: str
    downloaded_files: list[_DownloadedFileRecordModel]
    fetched_at: datetime


def write_orography_source_record(record: OrographySourceRecord, path: Path) -> None:
    model = _OrographySourceRecordModel(
        orography_identity=record.orography_identity,
        downloaded_files=[
            _DownloadedFileRecordModel(**asdict(f)) for f in record.downloaded_files
        ],
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
        orography_identity=model.orography_identity,
        downloaded_files=tuple(
            DownloadedFileRecord(**f.model_dump()) for f in model.downloaded_files
        ),
        fetched_at=model.fetched_at,
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
    """1b — fetch per the frozen spec, then write the record. A re-run
    verifies observed hashes against the EXISTING record (an unexplained
    change in a "static" source is a typed failure) rather than assuming a
    prior fetch was good."""
    identity = orography_identity(spec)
    record_path = orography_source_record_path(data_root)
    existing = read_orography_source_record(record_path)

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

    if existing is not None and existing.orography_identity == identity:
        existing_by_path = {f.path: f for f in existing.downloaded_files}
        for new_file in files:
            prior = existing_by_path.get(new_file.path)
            if prior is not None and prior.sha256 != new_file.sha256:
                raise Era5OrographyError(
                    f"re-fetch of {new_file.path!r} disagrees with the "
                    f"existing OrographySourceRecord ({new_file.sha256} != "
                    f"{prior.sha256}) — an unexplained change in a static "
                    "source"
                )

    record = OrographySourceRecord(
        orography_identity=identity,
        downloaded_files=tuple(files),
        fetched_at=clock(),
    )
    write_orography_source_record(record, record_path)
    log.info("era5.orography.fetched", orography_identity=identity, n_files=len(files))
    return record


# --- convert (D3a) ---


def convert_field(raw_values: np.ndarray, *, spec: OrographySpec) -> np.ndarray:
    """D3a — apply the frozen conversion rule, with the magnitude sanity
    check performed BEFORE the conversion is trusted: a retrieved field
    whose magnitude is inconsistent with the declared unit is a typed
    failure, never a silent divide."""
    finite = raw_values[np.isfinite(raw_values)]
    if finite.size == 0:
        raise Era5OrographyError("source raster has no finite values to convert")
    lo, hi = float(np.min(finite)), float(np.max(finite))
    if spec.conversion_rule == OrographyConversionRule.GEOPOTENTIAL_G0:
        plo, phi = _GEOPOTENTIAL_PLAUSIBLE_RANGE
        if not (plo <= lo and hi <= phi):
            raise Era5OrographyError(
                f"source field magnitude [{lo}, {hi}] is inconsistent with "
                f"the declared geopotential_g0 conversion rule (expected "
                f"roughly [{plo}, {phi}]) — refusing to silently divide by g0"
            )
        return raw_values / G0_M_PER_S2
    # OrographyConversionRule.IDENTITY
    plo, phi = _METRES_PLAUSIBLE_RANGE
    if not (plo <= lo and hi <= phi):
        raise Era5OrographyError(
            f"source field magnitude [{lo}, {hi}] is inconsistent with the "
            f"declared identity (already-metres) conversion rule (expected "
            f"roughly [{plo}, {phi}])"
        )
    return raw_values


# --- aggregate (D3a) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class AggregationResult:
    values: np.ndarray
    """(n_target_lat, n_target_lon) — area-weighted mean of valid source
    cells, or NaN per the no-data policy."""
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
    """D3a — area-weighted arithmetic mean of source cells whose centres
    fall inside the target 0.1 deg cell (source cells are uniform area, so
    an unweighted mean of the valid ones IS the area-weighted mean).

    No-data policy: any contributing NaN source cell aggregates over the
    valid remainder and sets `flagged`; >`no_data_threshold_fraction`
    no-data by area, or zero valid source cells, emits NaN (still flagged).
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
    ds["orography_elev_m"].attrs["units"] = "m"
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


def write_orography_raster(ds: xr.Dataset, *, data_root: Path, identity: str) -> str:
    """Atomic tmp -> reopen-and-validate -> `os.replace`; returns the sha256
    of the published file."""
    final_path = orography_raster_path(data_root, identity=identity)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_path_for(final_path)
    tmp_path.unlink(missing_ok=True)
    ds.to_netcdf(tmp_path, engine="h5netcdf")
    try:
        with xr.open_dataset(tmp_path, engine="h5netcdf") as reopened:
            reopened.load()
            if "orography_elev_m" not in reopened:
                raise Era5OrographyError(
                    "reopened orography raster missing 'orography_elev_m'"
                )
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
) -> tuple[OrographySourceRecord, str]:
    """1b's full driver: fetch -> convert -> aggregate -> write -> reopen ->
    revalidate against the D9 product's own grid. `raw_reader` returns
    (values, lat, lon) for the first downloaded file — injected so tests
    never need a real GRIB/GeoTIFF/NetCDF parser for a specific format."""
    source_record = fetch_orography_source(
        spec, downloader=downloader, data_root=data_root, clock=clock
    )
    raw_path = data_root / source_record.downloaded_files[0].path
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
    sha256 = write_orography_raster(
        ds, data_root=data_root, identity=source_record.orography_identity
    )
    final_path = orography_raster_path(
        data_root, identity=source_record.orography_identity
    )
    with xr.open_dataset(final_path, engine="h5netcdf") as reopened:
        reopened.load()
        assert_grid_matches(
            reopened["latitude"].values,
            reopened["longitude"].values,
            expected_lat=expected_lat,
            expected_lon=expected_lon,
        )
    return source_record, sha256
