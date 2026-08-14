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
from scripts.dhm_precip.era5_request import GRID_SPACING_DEG, expected_grid_shape

log = structlog.get_logger(__name__)

# D9: required top-level attrs on the final product (period-ending
# convention D10, the accumulation rule, both version identifiers and the
# source dataset). A missing/blank one means the file cannot be traced back
# to what produced it.
_REQUIRED_OUTPUT_ATTRS = (
    "period_ending_convention",
    "accumulation_rule",
    "transform_version",
    "output_schema_version",
    "source_dataset",
)
_OUTPUT_DTYPE = "float32"
_OUTPUT_DIMS = ("valid_time", "latitude", "longitude")
_GRID_TOLERANCE_DEG = 1e-6

_HOUR = np.timedelta64(1, "h")
DAY_START_HOUR = 1  # D6: the accumulation day is 01 UTC of D ... 00 UTC of D+1.
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


def _assert_post_clamp_accounting(
    *,
    full_days: np.ndarray,
    acc_day: np.ndarray,
    valid_time: np.ndarray,
    pre_clamp_increment: xr.DataArray,
    post_clamp_increment: xr.DataArray,
    clamp_mask: xr.DataArray,
    terminal_accumulator: xr.DataArray,
    tolerance_mm: float,
) -> None:
    """D6 post-condition 1b, asserted in code, not just tested — a review
    finding noted the prior code only totalled `packing_corrected_cells`/
    `max_correction_mm`/`mass_adjustment_mm` globally, without ever checking
    the equation those numbers are supposed to satisfy: for every
    accumulation day fully contained in the file, per grid cell, the
    published (post-clamp) sum equals

        1000 x original_terminal_accumulator_m + mass_adjustment_mm(day, cell)

    (both sides mm). Checked independently of post-condition 1 (which only
    covers the PRE-clamp sum, since clamping necessarily breaks exact
    telescoping) — a broken accounting factor (crediting the wrong day's
    mass, double-counting a correction, an off-by-one in which cells were
    clamped) would otherwise pass unnoticed as long as the YEAR-level totals
    happened to look positive."""
    for day in full_days:
        idx = np.where(acc_day == day)[0]
        terminal_idx = int(idx[np.argmax(valid_time[idx])])
        terminal_mm = terminal_accumulator.isel(valid_time=terminal_idx) * _METRES_TO_MM
        published_sum_mm = (
            post_clamp_increment.isel(valid_time=idx).sum(dim="valid_time")
            * _METRES_TO_MM
        )
        day_clamp_mask = clamp_mask.isel(valid_time=idx)
        mass_adjustment_mm = (
            (-pre_clamp_increment.isel(valid_time=idx))
            .where(day_clamp_mask)
            .fillna(0.0)
            .sum(dim="valid_time")
        ) * _METRES_TO_MM
        residual = np.abs(published_sum_mm - (terminal_mm + mass_adjustment_mm))
        if bool((residual > tolerance_mm).any()):
            raise Era5ConservationError(
                f"D6 post-condition 1b violated for accumulation day {day}: "
                "post-clamp sum != terminal + mass_adjustment (max residual "
                f"{float(residual.max())} mm exceeds tolerance {tolerance_mm} mm)"
            )


def diagnose_accumulation_convention(
    ds: xr.Dataset,
    *,
    tolerance_m: float = DEFAULT_PACKING_TOLERANCE_MM / _METRES_TO_MM,
) -> AccumulationDiagnostic:
    """3a — an empirical diagnostic: for each candidate reset hour, count
    naive-diff monotonicity violations everywhere except that hour, and pick
    the hour that minimises them. Run against a real 2b sample to confirm
    (or correct) D6's stated `DAY_START_HOUR = 1`.

    `tolerance_m` defaults to D7's own packing tolerance (1e-7 m) — a review
    finding noted this diagnostic previously hardcoded a threshold ten times
    looser (1e-6 m), which is a different number than the one the actual
    transform enforces and could mask violations the real pipeline would
    catch.
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
        violations_by_hour[candidate] = int(np.sum(diff[mask] < -tolerance_m))
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
    is_day_start = hours == DAY_START_HOUR
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

    # D9's missing-value policy explicitly permits per-cell NaN (e.g. an
    # ERA5-Land sea/non-land mask cell that is NaN at every timestep) — that
    # must never be conflated with a genuinely MISSING predecessor stamp.
    # The only stamp that structurally lacks a predecessor in `ds` is index 0
    # of the combined series, and only when it is not itself an
    # accumulation-day start (which is taken as itself, needing no
    # predecessor at all). Every other index's predecessor is the
    # immediately preceding array position, which `_require_hourly_grid`
    # already guarantees is present — so a NaN there is a source value
    # (legitimately masked), not a structural gap, and must be allowed to
    # propagate through to `convert_units`/`validate_output_schema`, where
    # D9's non-finite-cell-count / fully-NaN-field checks already handle it.
    missing_predecessor = np.zeros(n, dtype=bool)
    missing_predecessor[0] = not bool(is_day_start[0])
    missing_and_required = missing_predecessor & in_range
    if bool(missing_and_required.any()):
        # State the OBSERVED FACT, not a presumed cause. A review finding: the
        # previous message asserted "the adjacent acquisition window" was
        # missing, which is only one of several ways to reach here — and a
        # wrong cause in an error message sends the reader to the wrong place.
        raise Era5MissingBoundaryContextError(
            f"the first stamp of the supplied series ({valid_time[0]}) is "
            "inside the required range but has no predecessor and is not "
            "itself an accumulation-day start (01 UTC), so its hourly "
            "increment is undefined. Candidate causes: the neighbouring "
            "acquisition window was never acquired; the series was truncated "
            "before being passed in; or the raw artifact itself is short. "
            "Check which raw windows exist before treating this as a data "
            "defect."
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

    pre_clamp_increment = increment
    clamped_increment = increment.where(~clamp_mask, 0.0)

    # --- D6 post-condition 1b: post-clamp accounting, per day AND cell,
    # asserted BEFORE the totals above are trusted for the manifest ---
    _assert_post_clamp_accounting(
        full_days=full_days,
        acc_day=acc_day,
        valid_time=valid_time,
        pre_clamp_increment=pre_clamp_increment,
        post_clamp_increment=clamped_increment,
        clamp_mask=clamp_mask,
        terminal_accumulator=tp,
        tolerance_mm=conservation_tolerance_m * _METRES_TO_MM,
    )
    increment = clamped_increment

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
    if tuple(var.dims) != _OUTPUT_DIMS:
        raise Era5SchemaValidationError(
            f"'precipitation' dims {tuple(var.dims)} != {_OUTPUT_DIMS}"
        )
    if str(var.dtype) != _OUTPUT_DTYPE:
        raise Era5SchemaValidationError(
            f"'precipitation' dtype {var.dtype} != {_OUTPUT_DTYPE!r}"
        )
    missing_attrs = [a for a in _REQUIRED_OUTPUT_ATTRS if not str(ds.attrs.get(a, ""))]
    if missing_attrs:
        raise Era5SchemaValidationError(
            f"missing or blank required attrs: {missing_attrs}"
        )

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

    expected_lat_count, expected_lon_count = expected_grid_shape(expected_area)
    if lat.size != expected_lat_count:
        raise Era5SchemaValidationError(
            f"latitude has {lat.size} points, expected exactly "
            f"{expected_lat_count} at {GRID_SPACING_DEG} deg spacing for box "
            f"{expected_area}"
        )
    if lon.size != expected_lon_count:
        raise Era5SchemaValidationError(
            f"longitude has {lon.size} points, expected exactly "
            f"{expected_lon_count} at {GRID_SPACING_DEG} deg spacing for box "
            f"{expected_area}"
        )
    if lat.size > 1 and not np.allclose(
        np.abs(np.diff(np.sort(lat))), GRID_SPACING_DEG, atol=_GRID_TOLERANCE_DEG
    ):
        raise Era5SchemaValidationError(
            f"latitude spacing is not uniformly {GRID_SPACING_DEG} deg"
        )
    if lon.size > 1 and not np.allclose(
        np.abs(np.diff(np.sort(lon))), GRID_SPACING_DEG, atol=_GRID_TOLERANCE_DEG
    ):
        raise Era5SchemaValidationError(
            f"longitude spacing is not uniformly {GRID_SPACING_DEG} deg"
        )
    if (
        lat.max() > north + _GRID_TOLERANCE_DEG
        or lat.min() < south - _GRID_TOLERANCE_DEG
    ):
        raise Era5SchemaValidationError("latitude outside requested box")
    if lon.max() > east + _GRID_TOLERANCE_DEG or lon.min() < west - _GRID_TOLERANCE_DEG:
        raise Era5SchemaValidationError("longitude outside requested box")

    values = var.values
    if bool(np.isinf(values).any()):
        raise Era5SchemaValidationError(
            "'precipitation' contains infinite value(s); D9 permits finite or NaN only"
        )
    non_finite = int(np.isnan(values).sum())
    if non_finite == values.size:
        raise Era5SchemaValidationError("'precipitation' field is entirely non-finite")
    return SchemaValidationResult(non_finite_cell_count=non_finite)
