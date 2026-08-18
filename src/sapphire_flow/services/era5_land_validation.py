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

**``frac_snow`` is Caravan's CALENDAR-MONTH CLIMATOLOGY, not a per-day
classification (fixer round, 2026-08-18).** Caravan's published attribute is
``frac_snow`` — the OLDER, coarser sibling of Addor et al.'s ``frac_snow_daily``
— which classifies a whole CALENDAR MONTH (Jan..Dec, aggregated over every
year in the series) as "snow" when that month's CLIMATOLOGICAL MEAN
temperature is below 0 degC, then divides the summed climatological mean
precipitation of snow months by the summed climatological mean precipitation
of all twelve months. A per-day formula (summing precipitation on individual
sub-zero DAYS) disagrees with this whenever a month has some sub-zero and
some above-zero days but a non-negative monthly mean — e.g. four same-month
days at [-1, -1, 5, 5] degC give 0.5 per-day but 0.0 under Caravan's own
formula, since that month's mean temperature (2.0 degC) never drops below
zero.

**Coverage is data, never silently narrowed (fixer round, 2026-08-18).**
``validate_era5_land_against_caravan`` used to ``continue`` past a station
missing a basin, Caravan attributes, or overlapping precipitation/temperature
records with no record of having done so — a fleet where most basins were
skipped, or a basin compared on two days out of the nominal 14,610-day
window and one index out of four, returned the exact same all-``agreements``
shape as a full 40-year, four-index, 296-basin pass. It now returns a
:class:`CaravanValidationResult` carrying every skip (station, reason), the
expected/compared day count and coverage fraction per basin, and an explicit
:attr:`CaravanValidationResult.is_full_parity_pass` that is ``True`` only when
there were no skips, every basin met ``min_coverage_fraction`` of the
requested window, every basin compared all four :data:`INDEX_ORDER` indices,
and every comparison was within tolerance. An empty or partially-skipped
result can never evaluate as a pass — a caller checking only "did this
return agreements" can no longer mistake a narrow comparison for a broad one.
"""

from __future__ import annotations

from collections import defaultdict
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

# Fixer round (B1): a basin whose compared days fall short of this fraction of
# the requested window is still reported (agreements + coverage are data),
# but cannot count toward CaravanValidationResult.is_full_parity_pass.
DEFAULT_MIN_COVERAGE_FRACTION: Final[float] = 0.9

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
    precipitation_mm_day: Sequence[float],
    temperature_degc: Sequence[float],
    dates: Sequence[date],
) -> float:
    """Fraction of precipitation falling as snow — Caravan's published
    ``frac_snow`` (Addor et al. 2017), NOT the daily variant
    (``frac_snow_daily``). Caravan's ``frac_snow`` is a CALENDAR-MONTH
    CLIMATOLOGY: group every day into its calendar month (Jan..Dec, pooled
    across every year in ``dates``), take each month's MEAN precipitation and
    MEAN temperature, classify a month as snow when its mean temperature is
    below 0 degC, and divide the summed mean precipitation of snow months by
    the summed mean precipitation of every month PRESENT in ``dates`` (a
    calendar month absent from the series is omitted from both sums rather
    than counted as zero — over a full multi-year window all twelve are
    present, but a short or seasonal series must not be silently diluted).
    A day is never
    classified on its own — a handful of sub-zero days inside an otherwise
    mild month do not count as snow unless that month's own climatological
    mean drops below zero."""
    if len(precipitation_mm_day) != len(temperature_degc) or len(
        precipitation_mm_day
    ) != len(dates):
        raise ValueError(
            "frac_snow: precipitation/temperature/dates series length mismatch "
            f"({len(precipitation_mm_day)} != {len(temperature_degc)} != "
            f"{len(dates)})"
        )
    if not precipitation_mm_day:
        raise ValueError("frac_snow: empty precipitation series")

    monthly_precip: dict[int, list[float]] = defaultdict(list)
    monthly_temp: dict[int, list[float]] = defaultdict(list)
    for p, t, d in zip(precipitation_mm_day, temperature_degc, dates, strict=True):
        monthly_precip[d.month].append(p)
        monthly_temp[d.month].append(t)

    month_mean_precip = {
        month: sum(values) / len(values) for month, values in monthly_precip.items()
    }
    month_mean_temp = {
        month: sum(values) / len(values) for month, values in monthly_temp.items()
    }

    total = sum(month_mean_precip.values())
    if total <= 0:
        raise ValueError("frac_snow: zero or negative total precipitation")

    snow = sum(
        mean_p
        for month, mean_p in month_mean_precip.items()
        if month_mean_temp[month] < _SNOW_TEMP_THRESHOLD_C
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
    precipitation_mm_day: Sequence[float],
    temperature_degc: Sequence[float],
    dates: Sequence[date],
) -> dict[str, float]:
    return {
        "p_mean": p_mean(precipitation_mm_day),
        "frac_snow": frac_snow(precipitation_mm_day, temperature_degc, dates),
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


@dataclass(frozen=True, kw_only=True, slots=True)
class ValidationSkip:
    """A station T3 could not compare at all, and WHY — never silent."""

    station_id: StationId
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinCoverage:
    """How much of the requested window a compared basin actually covered —
    the fact a caller needs to tell "2 days out of 14,610" apart from a real
    40-year comparison."""

    station_id: StationId
    expected_days: int
    compared_days: int
    coverage_fraction: float
    indices_compared: tuple[str, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class CaravanValidationResult:
    """Everything T3 produced: agreements are data, not a verdict — read
    ``is_full_parity_pass`` (or the coverage/skip lists) for the verdict."""

    agreements: list[ClimateIndexAgreement]
    skips: list[ValidationSkip]
    coverage: list[BasinCoverage]
    min_coverage_fraction: float

    @property
    def basins_below_coverage_floor(self) -> list[BasinCoverage]:
        return [
            c for c in self.coverage if c.coverage_fraction < self.min_coverage_fraction
        ]

    @property
    def basins_missing_indices(self) -> list[BasinCoverage]:
        return [c for c in self.coverage if len(c.indices_compared) < len(INDEX_ORDER)]

    @property
    def is_full_parity_pass(self) -> bool:
        """``True`` only when EVERY station was compared (no skips), every
        compared basin met ``min_coverage_fraction`` of the requested window,
        every compared basin covered all four :data:`INDEX_ORDER` indices, and
        every agreement was within tolerance. An empty result (no basins
        compared at all) or a result with any skip NEVER counts as a pass —
        the whole point is that a caller cannot mistake a narrow comparison
        for a broad one just because ``agreements`` looks all-green."""
        return (
            bool(self.coverage)
            and not self.skips
            and not self.basins_below_coverage_floor
            and not self.basins_missing_indices
            and all(a.within_tolerance for a in self.agreements)
        )


def validate_era5_land_against_caravan(
    *,
    forcing_store: HistoricalForcingStore,
    basin_store: BasinStore,
    stations: Sequence[StationConfig],
    source: ForcingSource = ForcingSource.ERA5_LAND,
    window_start: date = CARAVAN_WINDOW_START,
    window_end: date = CARAVAN_WINDOW_END,
    tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    min_coverage_fraction: float = DEFAULT_MIN_COVERAGE_FRACTION,
) -> CaravanValidationResult:
    """Recompute the four Caravan climate indices from OUR extraction over
    Caravan's own window and compare per basin.

    Stations without a basin, a basin without imported Caravan attributes, or
    without overlapping precipitation/temperature coverage over the window
    are NOT silently dropped — each is recorded in
    ``CaravanValidationResult.skips`` with a reason (a partial fleet during
    backfill is expected; being unable to see WHICH stations and WHY is not).
    Every basin that WAS compared gets a ``BasinCoverage`` entry recording how
    many of the expected window's days it actually covered, and how many of
    the four reference indices were present to compare — so a narrow
    comparison (few days, few indices) is visible as data, not indistinguishable
    from a full one.
    """
    start = ensure_utc(datetime.combine(window_start, datetime.min.time(), tzinfo=UTC))
    end = ensure_utc(
        datetime.combine(window_end, datetime.min.time(), tzinfo=UTC)
        + timedelta(days=1)
    )
    expected_days = (window_end - window_start).days + 1

    agreements: list[ClimateIndexAgreement] = []
    skips: list[ValidationSkip] = []
    coverage: list[BasinCoverage] = []

    for station in stations:
        if station.basin_id is None:
            skips.append(ValidationSkip(station_id=station.id, reason="no_basin_id"))
            continue
        basin = basin_store.fetch_basin(station.basin_id)
        if basin is None:
            skips.append(
                ValidationSkip(station_id=station.id, reason="basin_not_found")
            )
            continue
        if basin.attributes is None:
            skips.append(
                ValidationSkip(station_id=station.id, reason="no_basin_attributes")
            )
            continue
        reference = _caravan_reference(basin.attributes)
        if not reference:
            skips.append(
                ValidationSkip(
                    station_id=station.id, reason="no_caravan_reference_indices"
                )
            )
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
            skips.append(
                ValidationSkip(station_id=station.id, reason="missing_forcing_coverage")
            )
            continue

        precip_by_day = {r.valid_time.date(): r.value for r in precip_records}
        temp_by_day = {r.valid_time.date(): r.value for r in temp_records}
        common_days = sorted(set(precip_by_day) & set(temp_by_day))
        if not common_days:
            skips.append(
                ValidationSkip(station_id=station.id, reason="no_overlapping_days")
            )
            continue

        precip_series = [precip_by_day[d] for d in common_days]
        temp_series = [temp_by_day[d] for d in common_days]
        computed = compute_climate_indices(precip_series, temp_series, common_days)

        basin_agreements = compare_climate_indices(
            station_id=station.id,
            computed=computed,
            reference=reference,
            tolerance=tolerance,
        )
        agreements.extend(basin_agreements)
        coverage.append(
            BasinCoverage(
                station_id=station.id,
                expected_days=expected_days,
                compared_days=len(common_days),
                coverage_fraction=len(common_days) / expected_days,
                indices_compared=tuple(a.index_name for a in basin_agreements),
            )
        )

    return CaravanValidationResult(
        agreements=agreements,
        skips=skips,
        coverage=coverage,
        min_coverage_fraction=min_coverage_fraction,
    )
