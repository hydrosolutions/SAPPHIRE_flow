"""ERA5-Land vs Caravan climate-index parity check (Plan 183 T3).

Even reading the SAME store aquacast's data plane reads, our catchment
averaging is still ours — cell weighting, boundary handling and the polygon
itself all differ from Caravan's own extraction. This module recomputes
Caravan's own climate-index formulas (Addor et al. 2017 hydrologic
signatures — the basis for Caravan's published ``p_mean``/``frac_snow``/
``high_prec_freq``/``low_prec_dur``) from OUR extracted ERA5-Land series and
compares them against the ``caravan:``-namespaced basin static attributes
Plan 155 imported.

**What a mismatch would prove (and would NOT prove):** Caravan's indices
derive from Caravan's OWN ERA5-Land archive, not ``sloth-dynamic`` — Plan 117
explicitly left archive identity open. A disagreement here could be OUR
averaging or a STORE difference; this module alone cannot separate them. A
MATCH is still strong end-to-end evidence the whole chain reproduces the
training lineage — budget a mismatch as the start of an investigation, not a
localisation to our code.

Ordered so each failure localises (``INDEX_ORDER``): ``p_mean`` first
(systematic bias), then ``frac_snow`` (exercises the temperature path), then
the ``freq``/``dur`` pair (daily-boundary convention).

Tolerance (D1, owner 2026-08-18) is set BEFORE the comparison: 5% relative,
per basin — deliberately tight, and expected to be revised once real numbers
are seen, so ``compare_climate_indices`` always records the observed
``relative_diff`` (not just pass/fail).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.forcing_sources import ForcingSource

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.protocols.stores import BasinStore, HistoricalForcingStore
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig

# D1: deliberately tight — the point is to detect an averaging discrepancy,
# and a loose bound would pass a systematically biased extraction.
DEFAULT_RELATIVE_TOLERANCE: Final[float] = 0.05

# Caravan's published training window (Plan 183 T3).
CARAVAN_WINDOW_START: Final[date] = date(1981, 1, 1)
CARAVAN_WINDOW_END: Final[date] = date(2020, 12, 31)

CARAVAN_PREFIX: Final[str] = "caravan:"

INDEX_ORDER: Final[tuple[str, ...]] = (
    "p_mean",
    "frac_snow",
    "high_prec_freq",
    "low_prec_dur",
)

_HIGH_PREC_MULTIPLE: Final[float] = 5.0
_DRY_DAY_THRESHOLD_MM: Final[float] = 1.0
_SNOW_TEMP_THRESHOLD_C: Final[float] = 0.0


def p_mean(precipitation_mm_day: Sequence[float]) -> float:
    """Mean daily precipitation (mm/day) — Addor et al. (2017)."""
    if not precipitation_mm_day:
        raise ValueError("p_mean: empty precipitation series")
    return sum(precipitation_mm_day) / len(precipitation_mm_day)


def frac_snow(
    precipitation_mm_day: Sequence[float], temperature_degc: Sequence[float]
) -> float:
    """Fraction of precipitation falling as snow: precipitation on days with
    mean temperature < 0 degC, divided by total precipitation (Addor et al.
    2017)."""
    if len(precipitation_mm_day) != len(temperature_degc):
        raise ValueError(
            "frac_snow: precipitation/temperature series length mismatch "
            f"({len(precipitation_mm_day)} != {len(temperature_degc)})"
        )
    total = sum(precipitation_mm_day)
    if total <= 0:
        raise ValueError("frac_snow: zero or negative total precipitation")
    snow = sum(
        p
        for p, t in zip(precipitation_mm_day, temperature_degc, strict=True)
        if t < _SNOW_TEMP_THRESHOLD_C
    )
    return snow / total


def high_prec_freq(precipitation_mm_day: Sequence[float]) -> float:
    """Frequency of high-precipitation days: days with precipitation >= 5x
    the series mean, as a fraction of all days (Addor et al. 2017)."""
    if not precipitation_mm_day:
        raise ValueError("high_prec_freq: empty precipitation series")
    threshold = _HIGH_PREC_MULTIPLE * p_mean(precipitation_mm_day)
    high_days = sum(1 for p in precipitation_mm_day if p >= threshold)
    return high_days / len(precipitation_mm_day)


def low_prec_dur(precipitation_mm_day: Sequence[float]) -> float:
    """Mean duration (days) of dry spells: consecutive-day runs with
    precipitation < 1 mm/day (Addor et al. 2017). ``0.0`` when there are no
    dry days at all."""
    if not precipitation_mm_day:
        raise ValueError("low_prec_dur: empty precipitation series")
    run_lengths: list[int] = []
    current = 0
    for p in precipitation_mm_day:
        if p < _DRY_DAY_THRESHOLD_MM:
            current += 1
        elif current:
            run_lengths.append(current)
            current = 0
    if current:
        run_lengths.append(current)
    if not run_lengths:
        return 0.0
    return sum(run_lengths) / len(run_lengths)


def compute_climate_indices(
    precipitation_mm_day: Sequence[float], temperature_degc: Sequence[float]
) -> dict[str, float]:
    return {
        "p_mean": p_mean(precipitation_mm_day),
        "frac_snow": frac_snow(precipitation_mm_day, temperature_degc),
        "high_prec_freq": high_prec_freq(precipitation_mm_day),
        "low_prec_dur": low_prec_dur(precipitation_mm_day),
    }


@dataclass(frozen=True, kw_only=True, slots=True)
class ClimateIndexAgreement:
    station_id: StationId
    index_name: str
    computed: float
    reference: float
    relative_diff: float
    within_tolerance: bool


def _relative_diff(computed: float, reference: float) -> float:
    if reference == 0.0:
        # No basin-relative scale to divide by; fall back to the absolute
        # gap rather than raising or silently reporting 0% agreement.
        return abs(computed - reference)
    return abs(computed - reference) / abs(reference)


def compare_climate_indices(
    *,
    station_id: StationId,
    computed: dict[str, float],
    reference: dict[str, float],
    tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
) -> list[ClimateIndexAgreement]:
    """Ordered per ``INDEX_ORDER`` so a failure localises: only indices
    present in BOTH ``computed`` and ``reference`` are compared."""
    agreements: list[ClimateIndexAgreement] = []
    for name in INDEX_ORDER:
        if name not in computed or name not in reference:
            continue
        diff = _relative_diff(computed[name], reference[name])
        agreements.append(
            ClimateIndexAgreement(
                station_id=station_id,
                index_name=name,
                computed=computed[name],
                reference=reference[name],
                relative_diff=diff,
                within_tolerance=diff <= tolerance,
            )
        )
    return agreements


def _caravan_reference(basin_attributes: dict[str, object]) -> dict[str, float]:
    reference: dict[str, float] = {}
    for name in INDEX_ORDER:
        value = basin_attributes.get(f"{CARAVAN_PREFIX}{name}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            reference[name] = float(value)
    return reference


def validate_era5_land_against_caravan(
    *,
    forcing_store: HistoricalForcingStore,
    basin_store: BasinStore,
    stations: Sequence[StationConfig],
    source: ForcingSource = ForcingSource.ERA5_LAND,
    window_start: date = CARAVAN_WINDOW_START,
    window_end: date = CARAVAN_WINDOW_END,
    tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
) -> list[ClimateIndexAgreement]:
    """Recompute the four Caravan climate indices from OUR extraction over
    Caravan's own window and compare per basin. Stations without a basin, a
    basin without imported Caravan attributes, or without both
    precipitation and temperature coverage over the window are skipped
    (not an error — a partial fleet during backfill is expected)."""
    start = ensure_utc(datetime.combine(window_start, datetime.min.time(), tzinfo=UTC))
    end = ensure_utc(
        datetime.combine(window_end, datetime.min.time(), tzinfo=UTC)
        + timedelta(days=1)
    )

    results: list[ClimateIndexAgreement] = []
    for station in stations:
        if station.basin_id is None:
            continue
        basin = basin_store.fetch_basin(station.basin_id)
        if basin is None or basin.attributes is None:
            continue
        reference = _caravan_reference(basin.attributes)
        if not reference:
            continue

        precip_records = forcing_store.fetch_forcing(
            station_id=station.id,
            source=source.value,
            start=start,
            end=end,
            parameters=["precipitation"],
        )
        temp_records = forcing_store.fetch_forcing(
            station_id=station.id,
            source=source.value,
            start=start,
            end=end,
            parameters=["temperature"],
        )
        if not precip_records or not temp_records:
            continue

        precip_by_day = {r.valid_time.date(): r.value for r in precip_records}
        temp_by_day = {r.valid_time.date(): r.value for r in temp_records}
        common_days = sorted(set(precip_by_day) & set(temp_by_day))
        if not common_days:
            continue

        precip_series = [precip_by_day[d] for d in common_days]
        temp_series = [temp_by_day[d] for d in common_days]
        computed = compute_climate_indices(precip_series, temp_series)

        results.extend(
            compare_climate_indices(
                station_id=station.id,
                computed=computed,
                reference=reference,
                tolerance=tolerance,
            )
        )
    return results
