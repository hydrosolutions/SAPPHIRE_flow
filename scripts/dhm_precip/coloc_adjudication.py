"""Plan 182 (M-A10) — composes the building blocks (`stats_coloc`,
`coloc_bootstrap`, `coloc_verdict`) into one station's adjudication.

This is the "read the two together" step the plan's D7 redesign calls for:
the D2 UTC->NPT reconciliation, the threshold ladder, the matched-resolution
comparison, the D3 paired wet-hour fraction, the D5 bootstrap+adequacy check
and the D9 ordered verdict gates, run over one station's overlap-window data
(with the full record supplying D5's disjoint-period stationarity check).

Callers supply each side's OWN retained frame — DHM's M-A3-masked, on-grid,
JJAS rows in UTC (`qc_mask`); Pyramid's physical-range-checked rows in NPT
(D3, `pyramid_loader`) — this module performs no QC of its own, only
composition. The D2 UTC->NPT conversion IS this module's job (never the
caller's): every DHM frame is shifted before it touches anything else here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.circular import circ_dist_hours
from scripts.dhm_precip.coloc_bootstrap import (
    BootstrapPeakSpread,
    bootstrap_peak_hour_spread,
    per_season_hourly_means,
)
from scripts.dhm_precip.coloc_verdict import (
    StationVerdict,
    StationVerdictInputs,
    evaluate_station_verdict,
)
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.numeric import as_str
from scripts.dhm_precip.stats_coloc import (
    NoProfileRowsError,
    PairedRetention,
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

    from scripts.dhm_precip.params import DhmPrecipParams


@dataclass(frozen=True, kw_only=True, slots=True)
class StationAdjudication:
    station_verdict: StationVerdict
    threshold_ladder_peaks: dict[float, int]
    """D7 — the primary result: peak hour at each ladder threshold, computed
    from the D3 common-retained (paired) population — never independently
    from each side's own retained population (that would let hour-dependent
    missingness/masking manufacture a phase difference the gates then
    "see")."""
    threshold_ladder_profiles: dict[float, pl.DataFrame]
    """The FULL normalised-profile table `(station, hour, mean_value_mm, n,
    normalised_value)` at each ladder rung, not just the peak — the Exit
    deliverable is the ladder with `n` beside every hour, not a bare int."""
    pyramid_peak_hour: int
    pyramid_profile: pl.DataFrame
    """The Pyramid normalised profile over the SAME paired population the
    matched-resolution ladder rung uses."""
    dhm_full_record_peak_hour: int | None
    """D5b — the climatological (whole-JJAS-history) peak hour, `None` only
    when the station has zero retained full-record profile rows."""
    dhm_full_record_profile: pl.DataFrame
    """D5b — the standalone climatological DHM profile (NPT, whole JJAS
    history) — reported separately from the paired overlap ladder, per the
    "report standalone climatological profiles separately" correction."""
    pairing: PairedRetention
    """D3 — the common-retained-timestamp pairing, with each side's own
    retention count carried alongside (pairing loss stays visible)."""
    wet_hour_fraction: PairedWetHourFraction
    bootstrap: BootstrapPeakSpread
    disjoint_period_data_sufficient: bool
    """D5 — whether the full record actually straddles
    `params.coloc_dhm_stationarity_split_year` for this station (see
    `StationVerdictInputs.disjoint_period_data_sufficient`)."""
    disjoint_period_peak_diff_hours: float
    """D5 — the full-record pre/post-split stationarity check feeding the
    adequacy gate. Meaningless (arbitrary placeholder) when
    `disjoint_period_data_sufficient` is `False`."""


def _peak_hour_or_none(frame: pl.DataFrame, *, station: Station) -> int | None:
    """`peak_hour`, but `None` (never a raised exception) when the station
    has zero retained profile rows in `frame` — the caller decides what
    "insufficient data" means for its gate, this just refuses to crash."""
    try:
        return peak_hour(normalised_diurnal_profile(frame), station=station)
    except NoProfileRowsError:
        return None


def adjudicate_station(
    *,
    dhm_station: Station,
    dhm_overlap_retained: pl.DataFrame,
    dhm_full_record_retained: pl.DataFrame,
    pyramid_retained: pl.DataFrame,
    rng: random.Random,
    params: DhmPrecipParams,
) -> StationAdjudication:
    # --- D2: UTC -> NPT, once, before anything else touches DHM data. ---
    dhm_overlap_npt = dhm_utc_to_npt(
        dhm_overlap_retained,
        hour_offset=params.coloc_dhm_utc_to_npt_hour_offset,
        jjas_months=params.jjas_months,
    )
    dhm_full_record_npt = dhm_utc_to_npt(
        dhm_full_record_retained,
        hour_offset=params.coloc_dhm_utc_to_npt_hour_offset,
        jjas_months=params.jjas_months,
    )

    pyramid_station = Station(as_str(pyramid_retained["station"][0]))

    # --- D3/D9: pair FIRST on the common-retained NPT timestamps, then
    # derive the matched-resolution DHM/Pyramid peaks from the SAME paired
    # population — never independently-selected populations feeding gate 1
    # (D3's own fix, applied consistently to the phase comparison too). ---
    pairing = common_retained_frame(dhm_overlap_npt, pyramid_retained)
    paired_dhm = pairing.paired.select(
        pl.lit(str(dhm_station)).alias("station"),
        pl.col("timestamp"),
        pl.col("dhm_value_mm").alias("value_mm"),
    )
    paired_pyramid = pairing.paired.select(
        pl.lit(str(pyramid_station)).alias("station"),
        pl.col("timestamp"),
        pl.col("pyramid_value_mm").alias("value_mm"),
    )
    wet_fraction = paired_wet_hour_fraction(
        pairing.paired, wet_threshold_mm=params.wet_threshold_mm_per_h
    )

    ladder_profiles = {
        threshold: normalised_diurnal_profile(
            zero_below_threshold(paired_dhm, threshold)
        )
        for threshold in params.coloc_threshold_ladder_mm
    }
    ladder_peaks = {
        threshold: peak_hour(profile, station=dhm_station)
        for threshold, profile in ladder_profiles.items()
    }

    pyramid_profile = normalised_diurnal_profile(paired_pyramid)
    pyramid_peak = peak_hour(pyramid_profile, station=pyramid_station)

    # --- D5: the bootstrap operates on the same NPT overlap population. ---
    per_season = per_season_hourly_means(dhm_overlap_npt)
    bootstrap = bootstrap_peak_hour_spread(
        per_season,
        rng=rng,
        n_resamples=params.coloc_bootstrap_resamples,
        min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
    )

    # --- D5: disjoint-period (full record) stationarity check. The real
    # DHM source record spans only 2020-2025 in its entirety, so either
    # side of the split can legitimately be empty — never let `peak_hour`
    # raise uncaught; map insufficiency to gate 0 instead. ---
    split = params.coloc_dhm_stationarity_split_year
    pre = dhm_full_record_npt.filter(pl.col("timestamp").dt.year() < split)
    post = dhm_full_record_npt.filter(pl.col("timestamp").dt.year() >= split)
    pre_peak = _peak_hour_or_none(pre, station=dhm_station)
    post_peak = _peak_hour_or_none(post, station=dhm_station)
    disjoint_sufficient = pre_peak is not None and post_peak is not None
    disjoint_diff = 0.0
    if pre_peak is not None and post_peak is not None:
        disjoint_diff = circ_dist_hours(float(pre_peak), float(post_peak))

    dhm_full_record_profile = normalised_diurnal_profile(dhm_full_record_npt)
    try:
        dhm_full_record_peak: int | None = peak_hour(
            dhm_full_record_profile, station=dhm_station
        )
    except NoProfileRowsError:
        dhm_full_record_peak = None

    inputs = StationVerdictInputs(
        station=dhm_station,
        season_year_count=bootstrap.n_season_years,
        disjoint_period_data_sufficient=disjoint_sufficient,
        disjoint_period_peak_diff_hours=disjoint_diff,
        bootstrap_spread_hours=bootstrap.spread_hours,
        dhm_peak_all_hour=float(ladder_peaks[params.coloc_threshold_ladder_mm[0]]),
        dhm_peak_matched_resolution_hour=float(
            ladder_peaks[params.coloc_matched_resolution_threshold_mm]
        ),
        pyramid_peak_hour=float(pyramid_peak),
    )
    verdict = evaluate_station_verdict(inputs, params)

    return StationAdjudication(
        station_verdict=verdict,
        threshold_ladder_peaks=ladder_peaks,
        threshold_ladder_profiles=ladder_profiles,
        pyramid_peak_hour=pyramid_peak,
        pyramid_profile=pyramid_profile,
        dhm_full_record_peak_hour=dhm_full_record_peak,
        dhm_full_record_profile=dhm_full_record_profile,
        pairing=pairing,
        wet_hour_fraction=wet_fraction,
        bootstrap=bootstrap,
        disjoint_period_data_sufficient=disjoint_sufficient,
        disjoint_period_peak_diff_hours=disjoint_diff,
    )
