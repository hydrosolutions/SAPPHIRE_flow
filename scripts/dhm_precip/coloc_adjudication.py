"""Plan 182 (M-A10) — composes the building blocks (`stats_coloc`,
`coloc_bootstrap`, `coloc_verdict`) into one station's adjudication.

**D11 — the FULL RECORD is the adjudicated comparison; the overlap is
CORROBORATION.** D5 already said the climatological comparison is the
primary one, and the real overlap windows (Lukla 2021-2023, Syangboche
2020-2023) are below D5's 5-season adequacy floor, so gating the verdict on
the overlap made a decisive verdict structurally unreachable. The verdict is
therefore adjudicated on DHM's full JJAS record (2020-2025, 6 season-years)
against Pyramid's full JJAS record (Lukla 2005-2023, Namche 2002-2023);
the overlap window is computed and reported alongside, carrying its own
small-sample caveat, and an overlap-vs-full-record disagreement is itself
reportable, never an error.

**D3 applies to the window that CAN be paired.** The overlap window is
compared on common retained timestamps (and is the only window where a
wet-hour fraction is defined — "a wet-hour fraction over differently-selected
populations is not a comparison"). The full record cannot be paired: the two
records intersect only in the overlap, so pairing them would collapse the
climatological comparison back into the thin window D11 exists to escape.
That is precisely why the full-record comparison is non-contemporaneous, and
why D12's stationarity check is what licenses it.

**D12 — stationarity is checked on PYRAMID.** DHM has no pre-2020 data, so
a DHM pre/post split can never see the Pyramid phase shift that would
invalidate the non-contemporaneous comparison. The DHM split is computed and
reported as ADDITIONAL evidence only, never as a substitute.

Callers supply each side's OWN retained frame — DHM's M-A3-masked, on-grid,
JJAS rows in UTC (`qc_mask`); Pyramid's physical-range-checked JJAS rows in
NPT, already restricted to the window (D3, `pyramid_loader`) — this module
performs no QC of its own, only composition. The D2 UTC->NPT conversion IS
this module's job (never the caller's): every DHM frame is shifted before it
touches anything else here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.circular import circ_dist_hours
from scripts.dhm_precip.coloc_bootstrap import (
    BootstrapPeakSpread,
    EmptyBootstrapResultError,
    NoSeasonYearsError,
    bootstrap_peak_hour_spread,
    per_season_hourly_means,
)
from scripts.dhm_precip.coloc_verdict import (
    EvidenceFailure,
    StationVerdict,
    StationVerdictInputs,
    evaluate_station_verdict,
)
from scripts.dhm_precip.stats_coloc import (
    EmptyPairedPopulationError,
    NonPositiveGrandMeanError,
    NoProfileRowsError,
    PairedWetHourFraction,
    common_retained_frame,
    dhm_utc_to_npt,
    normalised_diurnal_profile,
    paired_wet_hour_fraction,
    peak_hour,
    zero_below_threshold,
)

if TYPE_CHECKING:
    import random

    from scripts.dhm_precip.domain_types import Station
    from scripts.dhm_precip.params import DhmPrecipParams


class ColocWindow(StrEnum):
    FULL_RECORD = "full_record"
    """D11 — the ADJUDICATED window (climatological, non-contemporaneous)."""
    OVERLAP = "overlap"
    """D11/D5a — CORROBORATION only; never gates the verdict."""


class Network(StrEnum):
    DHM = "dhm"
    PYRAMID = "pyramid"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationarityCheck:
    """D12 — one network's disjoint-period (pre/post-split) peak-hour
    comparison. Only the PYRAMID one gates the verdict."""

    network: Network
    split_year: int
    pre_peak_hour: int | None
    post_peak_hour: int | None
    data_sufficient: bool
    peak_diff_hours: float
    """0.0 (a placeholder, not a measurement) when `data_sufficient` is
    `False` — the gate that consumes this fires on insufficiency first."""


@dataclass(frozen=True, kw_only=True, slots=True)
class WindowEvidence:
    """One window's computed evidence. Every peak in here comes from the
    SAME population the window's bootstrap resamples — the uncertainty
    always describes the phase it is attached to."""

    window: ColocWindow
    paired: bool
    n_dhm_retained: int
    """This WINDOW's DHM retention, never the whole file's."""
    n_pyramid_retained: int
    """This WINDOW's Pyramid retention, never the whole file's."""
    n_common_retained: int | None
    """`None` for the unpaired full-record window."""
    threshold_ladder_profiles: dict[float, pl.DataFrame]
    """D7 — the FULL normalised-profile table `(station, hour,
    mean_value_mm, n, normalised_value)` at each ladder rung, not just the
    peak: the Exit deliverable is the ladder with `n` beside every hour."""
    threshold_ladder_peaks: dict[float, int]
    pyramid_profile: pl.DataFrame
    pyramid_peak_hour: int
    wet_hour_fraction: PairedWetHourFraction | None
    """D3 — defined ONLY on a paired population; `None` for the unpaired
    full-record window (reporting one there would compare
    differently-selected populations)."""
    bootstrap: BootstrapPeakSpread
    season_year_count: int
    """The number of season-years BOTH sides contribute in this window (the
    smaller of the two) — D11's 'both sides clear the 5-season threshold'."""

    def dhm_peak(self, threshold_mm: float) -> int:
        return self.threshold_ladder_peaks[threshold_mm]


@dataclass(frozen=True, kw_only=True, slots=True)
class WindowUnavailable:
    """A window whose evidence could not be computed at all — an all-zero
    ladder rung, an empty population, a bootstrap with nothing to resample.
    These are DATA states, not bugs: they become INDETERMINATE (with a
    reason) when the window is the adjudicated one, and a reported gap in
    corroboration when it is not. They must never escape as an exception."""

    window: ColocWindow
    failure: EvidenceFailure
    detail: str
    n_dhm_retained: int
    n_pyramid_retained: int
    n_common_retained: int | None


WindowResult = WindowEvidence | WindowUnavailable


@dataclass(frozen=True, kw_only=True, slots=True)
class StationAdjudication:
    dhm_station: Station
    pyramid_station: Station
    station_verdict: StationVerdict
    full_record: WindowResult
    """D11 — the ADJUDICATED window: the verdict above is derived from
    this one, and gate 0 applies to it."""
    overlap: WindowResult
    """D11 — CORROBORATION, carrying its own small-sample caveat. Never
    gates the verdict."""
    pyramid_stationarity: StationarityCheck
    """D12 — the check that gates the verdict (pre-2020 vs 2020+)."""
    dhm_stationarity: StationarityCheck
    """D12 — additional evidence ONLY. DHM has no pre-2020 data, so this
    can never substitute for the Pyramid check."""
    overlap_vs_full_record_peak_diff_hours: float | None
    """D11 — 'a disagreement between overlap and full record is itself
    reportable'. Circular distance between the two windows'
    matched-resolution DHM peaks; `None` when either window is
    unavailable."""


def _peak_hour_or_none(frame: pl.DataFrame, *, station: Station) -> int | None:
    """`peak_hour`, but `None` (never a raised exception) when the station
    has zero retained profile rows in `frame`, or no usable signal at all
    (an all-zero partition) — the caller decides what "insufficient data"
    means for its gate, this just refuses to crash."""
    try:
        return peak_hour(normalised_diurnal_profile(frame), station=station)
    except (NoProfileRowsError, NonPositiveGrandMeanError):
        return None


def _as_population(
    frame: pl.DataFrame, *, station: Station, value_column: str = "value_mm"
) -> pl.DataFrame:
    return frame.select(
        pl.lit(str(station)).alias("station"),
        pl.col("timestamp"),
        pl.col(value_column).alias("value_mm"),
    )


def _season_year_count(frame: pl.DataFrame) -> int:
    if frame.height == 0:
        return 0
    return int(frame["timestamp"].dt.year().n_unique())


def _stationarity(
    frame: pl.DataFrame, *, station: Station, network: Network, split_year: int
) -> StationarityCheck:
    """D12 — the disjoint-period check for ONE network. Either side of the
    split can legitimately be empty (a station whose record does not
    straddle the split), which is reported as `data_sufficient=False`, never
    raised."""
    population = _as_population(frame, station=station)
    pre = population.filter(pl.col("timestamp").dt.year() < split_year)
    post = population.filter(pl.col("timestamp").dt.year() >= split_year)
    pre_peak = _peak_hour_or_none(pre, station=station)
    post_peak = _peak_hour_or_none(post, station=station)
    sufficient = pre_peak is not None and post_peak is not None
    diff = (
        circ_dist_hours(float(pre_peak), float(post_peak))
        if pre_peak is not None and post_peak is not None
        else 0.0
    )
    return StationarityCheck(
        network=network,
        split_year=split_year,
        pre_peak_hour=pre_peak,
        post_peak_hour=post_peak,
        data_sufficient=sufficient,
        peak_diff_hours=diff,
    )


def _window_result(
    *,
    window: ColocWindow,
    dhm_npt: pl.DataFrame,
    pyramid: pl.DataFrame,
    dhm_station: Station,
    pyramid_station: Station,
    pair_on_common_timestamps: bool,
    rng: random.Random,
    params: DhmPrecipParams,
) -> WindowResult:
    dhm_population = _as_population(dhm_npt, station=dhm_station)
    pyramid_population = _as_population(pyramid, station=pyramid_station)
    n_dhm = dhm_population.height
    n_pyramid = pyramid_population.height
    n_common: int | None = None
    paired_frame: pl.DataFrame | None = None

    if pair_on_common_timestamps:
        pairing = common_retained_frame(dhm_population, pyramid_population)
        n_common = pairing.n_common_retained
        if n_common == 0:
            return WindowUnavailable(
                window=window,
                failure=EvidenceFailure.INSUFFICIENT_COMMON_DATA,
                detail=(
                    f"no common retained timestamps ({n_dhm} DHM, {n_pyramid} Pyramid)"
                ),
                n_dhm_retained=n_dhm,
                n_pyramid_retained=n_pyramid,
                n_common_retained=0,
            )
        paired_frame = pairing.paired
        dhm_population = _as_population(
            paired_frame, station=dhm_station, value_column="dhm_value_mm"
        )
        pyramid_population = _as_population(
            paired_frame, station=pyramid_station, value_column="pyramid_value_mm"
        )

    if dhm_population.height == 0 or pyramid_population.height == 0:
        return WindowUnavailable(
            window=window,
            failure=EvidenceFailure.INSUFFICIENT_SIGNAL,
            detail=(
                f"empty population ({dhm_population.height} DHM rows, "
                f"{pyramid_population.height} Pyramid rows)"
            ),
            n_dhm_retained=n_dhm,
            n_pyramid_retained=n_pyramid,
            n_common_retained=n_common,
        )

    try:
        wet_fraction = (
            paired_wet_hour_fraction(
                paired_frame, wet_threshold_mm=params.wet_threshold_mm_per_h
            )
            if paired_frame is not None
            else None
        )
        ladder_profiles = {
            threshold: normalised_diurnal_profile(
                zero_below_threshold(dhm_population, threshold)
            )
            for threshold in params.coloc_threshold_ladder_mm
        }
        ladder_peaks = {
            threshold: peak_hour(profile, station=dhm_station)
            for threshold, profile in ladder_profiles.items()
        }
        pyramid_profile = normalised_diurnal_profile(pyramid_population)
        pyramid_peak = peak_hour(pyramid_profile, station=pyramid_station)
        # D5/MAJOR — the bootstrap resamples the SAME population the peaks
        # above came from. Bootstrapping a different (e.g. unpaired)
        # population can certify a narrow interval around a peak that is
        # not the adjudicated one.
        bootstrap = bootstrap_peak_hour_spread(
            per_season_hourly_means(dhm_population),
            rng=rng,
            n_resamples=params.coloc_bootstrap_resamples,
            min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
        )
    except EmptyPairedPopulationError as exc:
        return WindowUnavailable(
            window=window,
            failure=EvidenceFailure.INSUFFICIENT_COMMON_DATA,
            detail=str(exc),
            n_dhm_retained=n_dhm,
            n_pyramid_retained=n_pyramid,
            n_common_retained=n_common,
        )
    except (
        NonPositiveGrandMeanError,
        NoProfileRowsError,
        NoSeasonYearsError,
        EmptyBootstrapResultError,
    ) as exc:
        return WindowUnavailable(
            window=window,
            failure=EvidenceFailure.INSUFFICIENT_SIGNAL,
            detail=str(exc),
            n_dhm_retained=n_dhm,
            n_pyramid_retained=n_pyramid,
            n_common_retained=n_common,
        )

    return WindowEvidence(
        window=window,
        paired=pair_on_common_timestamps,
        n_dhm_retained=n_dhm,
        n_pyramid_retained=n_pyramid,
        n_common_retained=n_common,
        threshold_ladder_profiles=ladder_profiles,
        threshold_ladder_peaks=ladder_peaks,
        pyramid_profile=pyramid_profile,
        pyramid_peak_hour=pyramid_peak,
        wet_hour_fraction=wet_fraction,
        bootstrap=bootstrap,
        season_year_count=min(
            _season_year_count(dhm_population), _season_year_count(pyramid_population)
        ),
    )


def _verdict_inputs(
    *,
    dhm_station: Station,
    adjudicated: WindowResult,
    pyramid_stationarity: StationarityCheck,
    params: DhmPrecipParams,
) -> StationVerdictInputs:
    if isinstance(adjudicated, WindowUnavailable):
        return StationVerdictInputs(
            station=dhm_station,
            evidence_failure=adjudicated.failure,
            season_year_count=0,
            pyramid_disjoint_period_data_sufficient=(
                pyramid_stationarity.data_sufficient
            ),
            pyramid_disjoint_period_peak_diff_hours=(
                pyramid_stationarity.peak_diff_hours
            ),
            bootstrap_spread_hours=0.0,
        )
    return StationVerdictInputs(
        station=dhm_station,
        season_year_count=adjudicated.season_year_count,
        pyramid_disjoint_period_data_sufficient=pyramid_stationarity.data_sufficient,
        pyramid_disjoint_period_peak_diff_hours=pyramid_stationarity.peak_diff_hours,
        bootstrap_spread_hours=adjudicated.bootstrap.spread_hours,
        dhm_peak_all_hour=float(
            adjudicated.dhm_peak(params.coloc_threshold_ladder_mm[0])
        ),
        dhm_peak_matched_resolution_hour=float(
            adjudicated.dhm_peak(params.coloc_matched_resolution_threshold_mm)
        ),
        pyramid_peak_hour=float(adjudicated.pyramid_peak_hour),
    )


def adjudicate_station(
    *,
    dhm_station: Station,
    pyramid_station: Station,
    dhm_full_record_retained: pl.DataFrame,
    dhm_overlap_retained: pl.DataFrame,
    pyramid_full_record_retained: pl.DataFrame,
    pyramid_overlap_retained: pl.DataFrame,
    rng: random.Random,
    params: DhmPrecipParams,
) -> StationAdjudication:
    """D11 — adjudicates the FULL RECORD and reports the overlap as
    corroboration. Both Pyramid frames must already be JJAS-restricted and
    window-scoped by the caller (the retention counts reported per window
    are only honest if they are)."""
    # --- D2: UTC -> NPT, once, before anything else touches DHM data. ---
    dhm_full_npt = dhm_utc_to_npt(
        dhm_full_record_retained,
        hour_offset=params.coloc_dhm_utc_to_npt_hour_offset,
        jjas_months=params.jjas_months,
    )
    dhm_overlap_npt = dhm_utc_to_npt(
        dhm_overlap_retained,
        hour_offset=params.coloc_dhm_utc_to_npt_hour_offset,
        jjas_months=params.jjas_months,
    )

    full_record = _window_result(
        window=ColocWindow.FULL_RECORD,
        dhm_npt=dhm_full_npt,
        pyramid=pyramid_full_record_retained,
        dhm_station=dhm_station,
        pyramid_station=pyramid_station,
        pair_on_common_timestamps=False,
        rng=rng,
        params=params,
    )
    overlap = _window_result(
        window=ColocWindow.OVERLAP,
        dhm_npt=dhm_overlap_npt,
        pyramid=pyramid_overlap_retained,
        dhm_station=dhm_station,
        pyramid_station=pyramid_station,
        pair_on_common_timestamps=True,
        rng=rng,
        params=params,
    )

    pyramid_stationarity = _stationarity(
        pyramid_full_record_retained,
        station=pyramid_station,
        network=Network.PYRAMID,
        split_year=params.coloc_pyramid_stationarity_split_year,
    )
    dhm_stationarity = _stationarity(
        dhm_full_npt,
        station=dhm_station,
        network=Network.DHM,
        split_year=params.coloc_dhm_stationarity_split_year,
    )

    verdict = evaluate_station_verdict(
        _verdict_inputs(
            dhm_station=dhm_station,
            adjudicated=full_record,
            pyramid_stationarity=pyramid_stationarity,
            params=params,
        ),
        params,
    )

    matched = params.coloc_matched_resolution_threshold_mm
    window_diff: float | None = None
    if isinstance(full_record, WindowEvidence) and isinstance(overlap, WindowEvidence):
        window_diff = circ_dist_hours(
            float(full_record.dhm_peak(matched)), float(overlap.dhm_peak(matched))
        )

    return StationAdjudication(
        dhm_station=dhm_station,
        pyramid_station=pyramid_station,
        station_verdict=verdict,
        full_record=full_record,
        overlap=overlap,
        pyramid_stationarity=pyramid_stationarity,
        dhm_stationarity=dhm_stationarity,
        overlap_vs_full_record_peak_diff_hours=window_diff,
    )
