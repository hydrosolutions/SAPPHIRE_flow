"""Reference-comparison GO/NO-GO gate (Plan 115b3 §4A-§4D).

Runs *after* the 1981-present backfill (Plan 115b2) and *before* the reader
flip (Plan 115b4). It changes no production data or behaviour — it only
reads ``historical_forcing`` and reports.

Two independent comparisons, both explained in Plan 115b §8:

- **4A/4B — whole-pipeline reference comparison** of our self-derived
  MeteoSwiss basin means (``meteoswiss_rhiresd`` / ``meteoswiss_tabsd``)
  against CAMELS-CH's own (``camels-ch``) over the same basins, 1981-2020.
  This is **not** a clean attributable control (named confounds: grid
  vintage/resolution, reprocessing, possibly-different aggregation masks) —
  it gates on the LONG-RUN TOTAL (precipitation) and per-basin absolute error
  in °C (temperature), because those wash out the per-event grid-resolution
  scatter that the confounds legitimately explain.
- **4C/4D — the live-tail residual**, ``RprelimD`` vs ``RhiresD`` over their
  STAC availability overlap: same pipeline, same polygons, same grid, same
  vintage. No confounds — the one genuinely attributable number here.

Tolerance gates (owner-locked, Plan 115b §8 / 115b3):

```
PRECIPITATION (1981-2020 TOTAL, per basin, signed):
    rel_bias = (sum(ours) - sum(camels)) / sum(camels)
    |rel_bias| <=  5%  -> PASS
    |rel_bias| >   5%  -> FLAG
    |rel_bias| >  20%  -> ESCALATE (never an automatic stop)

TEMPERATURE (per basin, absolute error in degC):
    mean_bias = mean(ours - camels)      rmse = sqrt(mean((ours-camels)**2))
    PASS      <=> |mean_bias| <= 0.5  AND  rmse <= 1.0
    FLAG      <=> |mean_bias| >  0.5  OR   rmse >  1.0
    ESCALATE  <=> |mean_bias| >  1.0  OR   rmse >  2.0
```

A basin/date present on one side but not the other is a coverage gap, not a
silently-inner-joined comparison — it forces ``DATA_QUALITY_ESCALATE``
regardless of what the computed bias would otherwise have been (Plan 115b3
§4A). A non-positive CAMELS total (degenerate denominator) does the same,
rather than dividing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from math import sqrt
from typing import TYPE_CHECKING, Protocol

import structlog

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.forcing_sources import ForcingSource

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import HistoricalForcingStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import (
        HistoricalForcingRecord,
        RawHistoricalForcing,
    )
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig, StationWeatherSource

log = structlog.get_logger(__name__)

#: The comparison window (Plan 115b3 §4A) — matches CAMELS-CH's own 1981-2020
#: coverage exactly (half-open [start, end)).
COMPARISON_START: UtcDatetime = ensure_utc(datetime(1981, 1, 1, tzinfo=UTC))
COMPARISON_END: UtcDatetime = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))

_PRECIP_FLAG_THRESHOLD = 0.05
_PRECIP_ESCALATE_THRESHOLD = 0.20
_TEMP_BIAS_FLAG_C = 0.5
_TEMP_BIAS_ESCALATE_C = 1.0
_TEMP_RMSE_FLAG_C = 1.0
_TEMP_RMSE_ESCALATE_C = 2.0

_SEASON_BY_MONTH: dict[int, str] = {
    12: "DJF",
    1: "DJF",
    2: "DJF",
    3: "MAM",
    4: "MAM",
    5: "MAM",
    6: "JJA",
    7: "JJA",
    8: "JJA",
    9: "SON",
    10: "SON",
    11: "SON",
}


class GateVerdict(StrEnum):
    PASS = "pass"
    FLAG = "flag"
    ESCALATE = "escalate"
    #: Coverage gap or a degenerate denominator (e.g. CAMELS total <= 0) —
    #: a data-quality problem, distinct from an unfavourable-but-computable
    #: bias. Never silently treated as a pass.
    DATA_QUALITY_ESCALATE = "data_quality_escalate"


def classify_precip_rel_bias(rel_bias: float) -> GateVerdict:
    """Plan 115b §8 precipitation gate: |rel_bias| <=5% pass, >5% flag,
    >20% escalate — on the SIGNED relative bias of the long-run total."""
    magnitude = abs(rel_bias)
    if magnitude > _PRECIP_ESCALATE_THRESHOLD:
        return GateVerdict.ESCALATE
    if magnitude > _PRECIP_FLAG_THRESHOLD:
        return GateVerdict.FLAG
    return GateVerdict.PASS


def classify_temperature(mean_bias: float, rmse: float) -> GateVerdict:
    """Plan 115b §8 temperature gate: BOTH mean_bias and rmse are
    thresholded in degC (percent is meaningless near 0degC)."""
    abs_bias = abs(mean_bias)
    if abs_bias > _TEMP_BIAS_ESCALATE_C or rmse > _TEMP_RMSE_ESCALATE_C:
        return GateVerdict.ESCALATE
    if abs_bias > _TEMP_BIAS_FLAG_C or rmse > _TEMP_RMSE_FLAG_C:
        return GateVerdict.FLAG
    return GateVerdict.PASS


def _daily_coverage(
    ours: dict[date, float], camels: dict[date, float]
) -> tuple[set[date], set[date], set[date]]:
    """Returns ``(common_dates, missing_in_ours, missing_in_camels)``."""
    ours_dates = set(ours)
    camels_dates = set(camels)
    return (
        ours_dates & camels_dates,
        camels_dates - ours_dates,
        ours_dates - camels_dates,
    )


def _season_totals(series: dict[date, float]) -> dict[str, float]:
    totals: dict[str, float] = {"DJF": 0.0, "MAM": 0.0, "JJA": 0.0, "SON": 0.0}
    for day, value in series.items():
        totals[_SEASON_BY_MONTH[day.month]] += value
    return totals


def _wet_day_rmse(
    ours: dict[date, float], camels: dict[date, float], common: set[date]
) -> float | None:
    """Non-gating diagnostic (Plan 115b §8): RMSE restricted to days CAMELS
    reports as wet (value > 0) — where the 2km-vs-1km grid effect legitimately
    concentrates. Reported only, never thresholded."""
    wet_days = [d for d in common if camels[d] > 0]
    if not wet_days:
        return None
    diffs = [ours[d] - camels[d] for d in wet_days]
    return sqrt(sum(d * d for d in diffs) / len(diffs))


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinPrecipResult:
    station_id: StationId
    code: str
    ours_total_mm: float
    camels_total_mm: float
    rel_bias: float | None
    n_days_ours: int
    n_days_camels: int
    n_missing_in_ours: int
    n_missing_in_camels: int
    verdict: GateVerdict
    # Non-gating diagnostics (Plan 115b §8) — reported, never thresholded.
    season_totals_ours: dict[str, float]
    season_totals_camels: dict[str, float]
    event_max_ours: float | None
    event_max_camels: float | None
    wet_day_rmse: float | None


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinTemperatureResult:
    station_id: StationId
    code: str
    mean_bias: float | None
    rmse: float | None
    n_days_common: int
    n_missing_in_ours: int
    n_missing_in_camels: int
    verdict: GateVerdict


def evaluate_precip_basin(
    station_id: StationId,
    code: str,
    ours: dict[date, float],
    camels: dict[date, float],
) -> BasinPrecipResult:
    """Plan 115b3 §4B — one basin's precipitation gate evaluation."""
    common, missing_in_ours, missing_in_camels = _daily_coverage(ours, camels)
    ours_total = sum(ours.values())
    camels_total = sum(camels.values())
    has_gap = bool(missing_in_ours or missing_in_camels)
    degenerate = camels_total <= 0

    rel_bias: float | None
    if has_gap or degenerate:
        rel_bias = None
        verdict = GateVerdict.DATA_QUALITY_ESCALATE
    else:
        rel_bias = (ours_total - camels_total) / camels_total
        verdict = classify_precip_rel_bias(rel_bias)

    return BasinPrecipResult(
        station_id=station_id,
        code=code,
        ours_total_mm=ours_total,
        camels_total_mm=camels_total,
        rel_bias=rel_bias,
        n_days_ours=len(ours),
        n_days_camels=len(camels),
        n_missing_in_ours=len(missing_in_ours),
        n_missing_in_camels=len(missing_in_camels),
        verdict=verdict,
        season_totals_ours=_season_totals(ours),
        season_totals_camels=_season_totals(camels),
        event_max_ours=max(ours.values()) if ours else None,
        event_max_camels=max(camels.values()) if camels else None,
        wet_day_rmse=_wet_day_rmse(ours, camels, common),
    )


def evaluate_temperature_basin(
    station_id: StationId,
    code: str,
    ours: dict[date, float],
    camels: dict[date, float],
) -> BasinTemperatureResult:
    """Plan 115b3 §4B — one basin's temperature gate evaluation."""
    common, missing_in_ours, missing_in_camels = _daily_coverage(ours, camels)
    has_gap = bool(missing_in_ours or missing_in_camels)

    if has_gap or not common:
        return BasinTemperatureResult(
            station_id=station_id,
            code=code,
            mean_bias=None,
            rmse=None,
            n_days_common=len(common),
            n_missing_in_ours=len(missing_in_ours),
            n_missing_in_camels=len(missing_in_camels),
            verdict=GateVerdict.DATA_QUALITY_ESCALATE,
        )

    diffs = [ours[d] - camels[d] for d in common]
    mean_bias = sum(diffs) / len(diffs)
    rmse = sqrt(sum(d * d for d in diffs) / len(diffs))
    return BasinTemperatureResult(
        station_id=station_id,
        code=code,
        mean_bias=mean_bias,
        rmse=rmse,
        n_days_common=len(common),
        n_missing_in_ours=0,
        n_missing_in_camels=0,
        verdict=classify_temperature(mean_bias, rmse),
    )


def _records_to_daily(records: list[HistoricalForcingRecord]) -> dict[date, float]:
    return {r.valid_time.date(): r.value for r in records}


def fetch_basin_daily_series(
    store: HistoricalForcingStore,
    station_id: StationId,
    source: ForcingSource,
    parameter: str,
    start: UtcDatetime,
    end: UtcDatetime,
) -> dict[date, float]:
    """One basin's daily series for ``source``/``parameter`` over
    ``[start, end)`` — the store already collapses to the latest version per
    logical key, so at most one value per date."""
    records = store.fetch_forcing(
        station_id, source.value, start, end, parameters=[parameter]
    )
    return _records_to_daily(records)


@dataclass(frozen=True, kw_only=True, slots=True)
class ReferenceComparisonReport:
    precipitation: list[BasinPrecipResult]
    temperature: list[BasinTemperatureResult]


def run_reference_comparison(
    store: HistoricalForcingStore,
    stations: list[StationConfig],
) -> ReferenceComparisonReport:
    """Plan 115b3 §4A/§4B — the whole-pipeline reference comparison, our
    self-derived MeteoSwiss series vs CAMELS-CH, per basin, over
    ``[1981-01-01, 2021-01-01)``."""
    precip: list[BasinPrecipResult] = []
    temperature: list[BasinTemperatureResult] = []

    for station in stations:
        ours_precip = fetch_basin_daily_series(
            store,
            station.id,
            ForcingSource.METEOSWISS_RHIRESD,
            "precipitation",
            COMPARISON_START,
            COMPARISON_END,
        )
        camels_precip = fetch_basin_daily_series(
            store,
            station.id,
            ForcingSource.CAMELS_CH,
            "precipitation",
            COMPARISON_START,
            COMPARISON_END,
        )
        precip.append(
            evaluate_precip_basin(station.id, station.code, ours_precip, camels_precip)
        )

        ours_temp = fetch_basin_daily_series(
            store,
            station.id,
            ForcingSource.METEOSWISS_TABSD,
            "temperature",
            COMPARISON_START,
            COMPARISON_END,
        )
        camels_temp = fetch_basin_daily_series(
            store,
            station.id,
            ForcingSource.CAMELS_CH,
            "temperature",
            COMPARISON_START,
            COMPARISON_END,
        )
        temperature.append(
            evaluate_temperature_basin(station.id, station.code, ours_temp, camels_temp)
        )

        log.info(
            "validation_gate.basin_evaluated",
            station_id=str(station.id),
            code=station.code,
            precip_verdict=precip[-1].verdict.value,
            temperature_verdict=temperature[-1].verdict.value,
        )

    return ReferenceComparisonReport(precipitation=precip, temperature=temperature)


# ---------------------------------------------------------------------------
# 4C/4D — the live-tail residual: RprelimD vs RhiresD over their STAC
# availability overlap. Same pipeline/polygons/grid/vintage — no confounds.
# ---------------------------------------------------------------------------


class MeteoSwissBoundaryAdapter(Protocol):
    """Structural view of the adapter capability §4C needs: the writer-side
    product-scoped fetch (Plan 115b1 §1F) and the full availability-range
    discovery (Plan 115b3 §4C, generalising §3A/§3C's high-water-mark-only
    scan)."""

    def fetch_products(
        self,
        products: list[ForcingSource],
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]: ...

    def discover_product_availability_range(
        self, product: ForcingSource
    ) -> tuple[date, date] | None: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class OverlapWindow:
    """Inclusive [start, end] calendar-day window (Plan 115b3 §4C)."""

    start: date
    end: date


def discover_overlap_window(adapter: MeteoSwissBoundaryAdapter) -> OverlapWindow | None:
    """Plan 115b3 §4C — the overlap window is the STAC date INTERSECTION of
    RhiresD and RprelimD availability, both discovered via the adapter's
    availability-range helper. Returns ``None`` when either product has no
    published asset yet, or their ranges do not overlap (never substituted
    with a guess)."""
    rhiresd_range = adapter.discover_product_availability_range(
        ForcingSource.METEOSWISS_RHIRESD
    )
    rprelimd_range = adapter.discover_product_availability_range(
        ForcingSource.METEOSWISS_RPRELIMD
    )
    if rhiresd_range is None or rprelimd_range is None:
        return None

    start = max(rhiresd_range[0], rprelimd_range[0])
    end = min(rhiresd_range[1], rprelimd_range[1])
    if start > end:
        return None
    return OverlapWindow(start=start, end=end)


def fetch_overlap_products(
    adapter: MeteoSwissBoundaryAdapter,
    station_configs: list[StationWeatherSource],
    window: OverlapWindow,
) -> tuple[list[RawHistoricalForcing], list[RawHistoricalForcing]]:
    """Plan 115b3 §4C — fetch RhiresD and RprelimD over the SAME overlap
    window, through our polygons. A one-off measurement fetch — separate from
    the 1981-present archive backfill (115b2), whose RhiresD/RprelimD spans
    are disjoint by construction."""
    start = ensure_utc(datetime.combine(window.start, time.min, tzinfo=UTC))
    end = ensure_utc(
        datetime.combine(window.end + timedelta(days=1), time.min, tzinfo=UTC)
    )
    rhiresd_rows = adapter.fetch_products(
        [ForcingSource.METEOSWISS_RHIRESD],
        station_configs,
        start,
        end,
        ["precipitation"],
    )
    rprelimd_rows = adapter.fetch_products(
        [ForcingSource.METEOSWISS_RPRELIMD],
        station_configs,
        start,
        end,
        ["precipitation"],
    )
    return rhiresd_rows, rprelimd_rows


@dataclass(frozen=True, kw_only=True, slots=True)
class LiveTailResidualResult:
    window_start: date
    window_end: date
    n_paired: int
    n_excluded_rhiresd_only: int
    n_excluded_rprelimd_only: int
    mean_bias: float | None
    rmse: float | None


def compute_live_tail_residual(
    rhiresd_rows: list[RawHistoricalForcing],
    rprelimd_rows: list[RawHistoricalForcing],
    window: OverlapWindow,
) -> LiveTailResidualResult:
    """Plan 115b3 §4D — the one genuinely attributable number: RprelimD vs
    RhiresD over their overlap. Compares ONLY paired (station, date) rows — a
    row present for one product but not the other is excluded, and the
    exclusion count is reported (never silently inner-joined without
    accounting)."""
    rhiresd_by_key = {
        (r.station_id, r.valid_time.date()): r.value for r in rhiresd_rows
    }
    rprelimd_by_key = {
        (r.station_id, r.valid_time.date()): r.value for r in rprelimd_rows
    }
    common_keys = set(rhiresd_by_key) & set(rprelimd_by_key)
    excluded_rhiresd_only = set(rhiresd_by_key) - set(rprelimd_by_key)
    excluded_rprelimd_only = set(rprelimd_by_key) - set(rhiresd_by_key)

    mean_bias: float | None = None
    rmse: float | None = None
    if common_keys:
        diffs = [rprelimd_by_key[k] - rhiresd_by_key[k] for k in common_keys]
        mean_bias = sum(diffs) / len(diffs)
        rmse = sqrt(sum(d * d for d in diffs) / len(diffs))

    return LiveTailResidualResult(
        window_start=window.start,
        window_end=window.end,
        n_paired=len(common_keys),
        n_excluded_rhiresd_only=len(excluded_rhiresd_only),
        n_excluded_rprelimd_only=len(excluded_rprelimd_only),
        mean_bias=mean_bias,
        rmse=rmse,
    )
