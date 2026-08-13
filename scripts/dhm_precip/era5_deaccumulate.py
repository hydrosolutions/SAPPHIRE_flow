"""M-A4 (Plan 171) task 3a — D6/D7/D8/D9 as pure functions over an
`xarray.Dataset`. This is the plan's central correctness risk: ERA5-Land's
accumulator resets at 01 UTC (not at calendar midnight), so a plain
`pad().diff("valid_time")` (correct for MeteoSwiss's per-cycle accumulation,
`src/sapphire_flow/adapters/meteoswiss_nwp.py:157`) silently corrupts every
`23 -> 00 -> 01` seam. See `tests/unit/scripts/test_era5_deaccumulate.py` for
the red-first proof that a naive global diff violates the post-conditions
asserted here.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it.
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import structlog
import xarray as xr

from scripts.dhm_precip.era5_errors import (
    Era5ConservationError,
    Era5MissingBoundaryContextError,
    Era5PackingPostConditionError,
    Era5SchemaValidationError,
    Era5TransformFailedError,
    Era5UnitsMismatchError,
)
from scripts.dhm_precip.era5_manifest import PackingAccounting

log = structlog.get_logger(__name__)

_HOUR = np.timedelta64(1, "h")
_DAY_START_HOUR = 1  # D6: the accumulation day is 01 UTC of D ... 00 UTC of D+1.
ACCUMULATION_RULE_ID = "era5_land_01_00_accumulation_day_v1"
OUTPUT_SCHEMA_VERSION = "1"

DEFAULT_PACKING_TOLERANCE_MM = 1e-4
DEFAULT_CONSERVATION_TOLERANCE_M = 1e-5

_METRES_TO_MM = 1000.0
_METRES_UNIT_ALIASES = frozenset({"m", "metres", "meters"})


@dataclass(frozen=True, kw_only=True, slots=True)
class DeaccumulationResult:
    dataset: xr.Dataset
    """Var `tp` (still metres — D8 converts separately), full input time
    range including any boundary-context stamps the caller supplied."""
    packing: PackingAccounting
    """D7 — mm-denominated, regardless of `dataset`'s own (metres) units."""
    accumulation_days_checked: int
    """Number of fully-contained accumulation days whose conservation
    post-condition (D6 #1) was actually checked."""


@dataclass(frozen=True, kw_only=True, slots=True)
class AccumulationDiagnostic:
    """3a — reports the *observed* accumulation convention from a real
    sample (2b), confirming or correcting D6's stated rule."""

    reset_hour: int
    terminal_hour: int
    monotone_within_day: bool
    sample_size_days: int


@dataclass(frozen=True, kw_only=True, slots=True)
class SchemaValidationResult:
    non_finite_cell_count: int


def _require_hourly_grid(valid_time: np.ndarray) -> None:
    if valid_time.size == 0:
        raise Era5TransformFailedError("empty valid_time axis")
    if valid_time.size > 1 and not np.all(np.diff(valid_time) == _HOUR):
        raise Era5TransformFailedError(
            "input valid_time is not a contiguous, strictly hourly grid"
        )


def _hours_of(valid_time: np.ndarray) -> np.ndarray:
    days = valid_time.astype("datetime64[D]")
    return ((valid_time - days) / _HOUR).astype(int)


def _accumulation_day(valid_time: np.ndarray) -> np.ndarray:
    """D6 — hour 0 belongs to the PRECEDING calendar day's accumulation
    day; every other hour belongs to its own calendar day."""
    days = valid_time.astype("datetime64[D]")
    hours = _hours_of(valid_time)
    return np.where(hours == 0, days - np.timedelta64(1, "D"), days)


def diagnose_accumulation_convention(ds: xr.Dataset) -> AccumulationDiagnostic:
    """3a — an empirical diagnostic: for each candidate reset hour, count
    naive-diff monotonicity violations everywhere except that hour, and pick
    the hour that minimises them. Run against a real 2b sample to confirm
    (or correct) D6's stated `_DAY_START_HOUR = 1`.
    """
    valid_time = ds["valid_time"].values
    _require_hourly_grid(valid_time)
    tp = ds["tp"]
    if valid_time.size < 2:
        raise Era5TransformFailedError("need >= 2 timesteps to diagnose a reset")
    diff = tp.diff("valid_time").values
    diff_hour = _hours_of(valid_time)[1:]  # hour the diff step BELONGS to
    violations_by_hour: dict[int, int] = {}
    for candidate in range(24):
        mask = diff_hour != candidate
        violations_by_hour[candidate] = int(np.sum(diff[mask] < -1e-6))
    reset_hour = min(violations_by_hour, key=lambda h: violations_by_hour[h])
    return AccumulationDiagnostic(
        reset_hour=reset_hour,
        terminal_hour=(reset_hour - 1) % 24,
        monotone_within_day=violations_by_hour[reset_hour] == 0,
        sample_size_days=int(valid_time.size // 24),
    )


def deaccumulate_precipitation(
    ds: xr.Dataset,
    *,
    tolerance_mm: float = DEFAULT_PACKING_TOLERANCE_MM,
    conservation_tolerance_m: float = DEFAULT_CONSERVATION_TOLERANCE_M,
    required_range: tuple[np.datetime64, np.datetime64] | None = None,
) -> DeaccumulationResult:
    """D6/D7 — accumulation-day-aware deaccumulation with post-conditions
    asserted in code (not just tested).

    `required_range` (inclusive) names the stamps the CALLER actually needs
    correctly computed; any of them lacking a resolvable predecessor raises
    `Era5MissingBoundaryContextError` rather than emitting NaN silently. Any
    stamp outside `required_range` (i.e. boundary context the caller
    supplied only to compute derivatives, not to keep) is exempt.
    """
    if "tp" not in ds:
        raise Era5UnitsMismatchError(
            "expected raw accumulator variable 'tp'; not found (already "
            "converted, or wrong input)"
        )
    tp = ds["tp"]
    valid_time = ds["valid_time"].values
    _require_hourly_grid(valid_time)

    hours = _hours_of(valid_time)
    is_day_start = hours == _DAY_START_HOUR
    is_day_start_da = xr.DataArray(
        is_day_start, dims="valid_time", coords={"valid_time": ds["valid_time"]}
    )

    n = valid_time.size
    if n > 1:
        diff_full = tp.diff("valid_time")
        tail_is_start = is_day_start_da.isel(valid_time=slice(1, None))
        tail_self = tp.isel(valid_time=slice(1, None)).where(tail_is_start)
        tail_diff = diff_full.where(~tail_is_start)
        tail_increment = tail_diff.fillna(tail_self)
        head_increment = tp.isel(valid_time=[0]).where(
            is_day_start_da.isel(valid_time=[0])
        )
        increment = xr.concat([head_increment, tail_increment], dim="valid_time")
        increment = increment.assign_coords(valid_time=ds["valid_time"])
    else:
        increment = tp.where(is_day_start_da)

    if required_range is None:
        required_start, required_end = valid_time[0], valid_time[-1]
    else:
        required_start, required_end = required_range
    in_range = (valid_time >= required_start) & (valid_time <= required_end)
    required_slice = increment.isel(valid_time=np.where(in_range)[0])
    if bool(np.isnan(required_slice.values).any()):
        raise Era5MissingBoundaryContextError(
            "cannot compute hourly increments for the requested range without "
            "boundary context from the adjacent acquisition window"
        )

    # --- D6 post-condition 1: conservation, on the UNCLAMPED increments ---
    acc_day = _accumulation_day(valid_time)
    unique_days, counts = np.unique(acc_day, return_counts=True)
    full_days = unique_days[counts == 24]
    checked = 0
    for day in full_days:
        idx = np.where(acc_day == day)[0]
        day_sum = increment.isel(valid_time=idx).sum(dim="valid_time")
        terminal_idx = int(idx[np.argmax(valid_time[idx])])
        terminal = tp.isel(valid_time=terminal_idx)
        residual = np.abs(day_sum - terminal)
        if bool((residual > conservation_tolerance_m).any()):
            raise Era5ConservationError(
                f"conservation violated for accumulation day {day}: "
                f"max residual {float(residual.max())} m exceeds tolerance "
                f"{conservation_tolerance_m} m"
            )
        checked += 1

    # --- D7: packing-error policy, applied AFTER conservation is checked ---
    tolerance_m = tolerance_mm / _METRES_TO_MM
    material_mask = increment < -tolerance_m
    if bool(material_mask.fillna(False).any()):
        count = int(material_mask.fillna(False).values.sum())
        raise Era5PackingPostConditionError(
            f"{count} material negative increment(s) beyond tolerance "
            f"{tolerance_mm} mm — the accumulation-day assumption is wrong"
        )
    clamp_mask = (increment < 0).fillna(False)
    corrected_cells = int(clamp_mask.values.sum())
    if corrected_cells:
        max_correction_mm = float((-increment).where(clamp_mask).max()) * _METRES_TO_MM
        mass_adjustment_mm = float((-increment).where(clamp_mask).sum()) * _METRES_TO_MM
    else:
        max_correction_mm = 0.0
        mass_adjustment_mm = 0.0
    increment = increment.where(~clamp_mask, 0.0)

    # D6 post-condition 2: non-negativity, AFTER the packing clamp.
    if bool((increment.fillna(0.0) < 0).any()):
        raise Era5TransformFailedError(
            "non-negativity violated after packing clamp — internal error"
        )

    result_ds = ds.assign(tp=increment)
    packing = PackingAccounting(
        packing_corrected_cells=corrected_cells,
        max_correction_mm=max_correction_mm,
        mass_adjustment_mm=mass_adjustment_mm,
    )
    log.info(
        "era5.deaccumulate.complete",
        accumulation_days_checked=checked,
        packing_corrected_cells=corrected_cells,
        max_correction_mm=max_correction_mm,
    )
    return DeaccumulationResult(
        dataset=result_ds, packing=packing, accumulation_days_checked=checked
    )


def convert_units(ds: xr.Dataset) -> xr.Dataset:
    """D8 — convert the raw accumulator variable `tp` (metres) to
    `precipitation` (mm), guarded so a second pass over an already-converted
    file fails loudly rather than multiplying by 1000 twice."""
    if "tp" not in ds:
        raise Era5UnitsMismatchError(
            "expected raw accumulator variable 'tp'; not found (already "
            "converted, or wrong input)"
        )
    units = str(ds["tp"].attrs.get("units", "")).strip().lower()
    if units not in _METRES_UNIT_ALIASES:
        raise Era5UnitsMismatchError(
            f"'tp' units attribute is {units!r}, expected metres"
        )
    precipitation = ds["tp"] * _METRES_TO_MM
    precipitation.attrs = {"units": "mm"}
    ds = ds.assign(precipitation=precipitation)
    return ds.drop_vars(["tp"])


def validate_output_schema(
    ds: xr.Dataset, *, expected_year: int, expected_area: tuple[int, int, int, int]
) -> SchemaValidationResult:
    """D9 — the final file's declared schema, checked on a live `Dataset`
    (the transform driver calls this both before writing and after
    reopening what it wrote)."""
    if "precipitation" not in ds:
        raise Era5SchemaValidationError("missing 'precipitation' data variable")
    var = ds["precipitation"]
    if str(var.attrs.get("units", "")) != "mm":
        raise Era5SchemaValidationError(
            f"'precipitation' units {var.attrs.get('units')!r} != 'mm'"
        )
    for coord in ("valid_time", "latitude", "longitude"):
        if coord not in ds.coords and coord not in ds.dims:
            raise Era5SchemaValidationError(f"missing coordinate {coord!r}")

    valid_time = ds["valid_time"].values
    if valid_time.size == 0:
        raise Era5SchemaValidationError("empty valid_time axis")
    if len(set(valid_time.tolist())) != valid_time.size:
        raise Era5SchemaValidationError("valid_time contains duplicate stamps")
    if valid_time.size > 1 and not np.all(np.diff(valid_time) == _HOUR):
        raise Era5SchemaValidationError(
            "valid_time is not strictly increasing and exactly hourly"
        )

    expected_start = np.datetime64(f"{expected_year:04d}-01-01T00:00:00")
    expected_end = np.datetime64(f"{expected_year:04d}-12-31T23:00:00")
    if valid_time[0] != expected_start or valid_time[-1] != expected_end:
        raise Era5SchemaValidationError(
            f"valid_time range {valid_time[0]}..{valid_time[-1]} != expected "
            f"{expected_start}..{expected_end}"
        )
    expected_count = int((expected_end - expected_start) / _HOUR) + 1
    if valid_time.size != expected_count:
        raise Era5SchemaValidationError(
            f"valid_time has {valid_time.size} stamps, expected {expected_count} "
            f"for calendar year {expected_year}"
        )

    north, west, south, east = expected_area
    lat = ds["latitude"].values
    lon = ds["longitude"].values
    tol = 0.1 + 1e-6
    if lat.max() > north + tol or lat.min() < south - tol:
        raise Era5SchemaValidationError("latitude outside requested box")
    if lon.max() > east + tol or lon.min() < west - tol:
        raise Era5SchemaValidationError("longitude outside requested box")

    values = var.values
    finite = np.isfinite(values)
    non_finite = int((~finite).sum())
    if not bool(finite.any()):
        raise Era5SchemaValidationError("'precipitation' field is entirely non-finite")
    return SchemaValidationResult(non_finite_cell_count=non_finite)
