"""Plan 182 (M-A10) — composes the building blocks (`stats_coloc`,
`coloc_bootstrap`, `coloc_verdict`) into one station's adjudication.

This is the "read the two together" step the plan's D7 redesign calls for:
the threshold ladder, the matched-resolution comparison, the D3 paired
wet-hour fraction, the D5 bootstrap+adequacy check and the D9 ordered
verdict gates, run over one station's overlap-window data (with the full
record supplying D5's disjoint-period stationarity check).

Callers supply each side's OWN retained frame — DHM's M-A3-masked, on-grid,
JJAS rows (`qc_mask`); Pyramid's physical-range-checked rows (D3) — this
module performs no QC of its own, only composition.
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
    PairedRetention,
    PairedWetHourFraction,
    common_retained_frame,
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
    """D7 — the primary result: peak hour at each ladder threshold."""
    pyramid_peak_hour: int
    pairing: PairedRetention
    """D3 — the common-retained-timestamp pairing, with each side's own
    retention count carried alongside (pairing loss stays visible)."""
    wet_hour_fraction: PairedWetHourFraction
    bootstrap: BootstrapPeakSpread
    disjoint_period_peak_diff_hours: float
    """D5 — the full-record pre/post-split stationarity check feeding the
    adequacy gate."""


def adjudicate_station(
    *,
    dhm_station: Station,
    dhm_overlap_retained: pl.DataFrame,
    dhm_full_record_retained: pl.DataFrame,
    pyramid_retained: pl.DataFrame,
    rng: random.Random,
    params: DhmPrecipParams,
) -> StationAdjudication:
    ladder = {
        threshold: peak_hour(
            normalised_diurnal_profile(
                zero_below_threshold(dhm_overlap_retained, threshold)
            ),
            station=dhm_station,
        )
        for threshold in params.coloc_threshold_ladder_mm
    }

    pyramid_station = Station(as_str(pyramid_retained["station"][0]))
    pyramid_peak = peak_hour(
        normalised_diurnal_profile(pyramid_retained), station=pyramid_station
    )

    pairing = common_retained_frame(dhm_overlap_retained, pyramid_retained)
    wet_fraction = paired_wet_hour_fraction(
        pairing.paired, wet_threshold_mm=params.wet_threshold_mm_per_h
    )

    per_season = per_season_hourly_means(dhm_overlap_retained)
    bootstrap = bootstrap_peak_hour_spread(
        per_season,
        rng=rng,
        n_resamples=params.coloc_bootstrap_resamples,
        min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
    )

    split = params.coloc_full_record_split_year
    pre = dhm_full_record_retained.filter(pl.col("timestamp").dt.year() < split)
    post = dhm_full_record_retained.filter(pl.col("timestamp").dt.year() >= split)
    disjoint_diff = circ_dist_hours(
        float(peak_hour(normalised_diurnal_profile(pre), station=dhm_station)),
        float(peak_hour(normalised_diurnal_profile(post), station=dhm_station)),
    )

    inputs = StationVerdictInputs(
        station=dhm_station,
        season_year_count=bootstrap.n_season_years,
        disjoint_period_peak_diff_hours=disjoint_diff,
        dhm_peak_all_hour=float(ladder[params.coloc_threshold_ladder_mm[0]]),
        dhm_peak_matched_resolution_hour=float(
            ladder[params.coloc_matched_resolution_threshold_mm]
        ),
        pyramid_peak_hour=float(pyramid_peak),
    )
    verdict = evaluate_station_verdict(inputs, params)

    return StationAdjudication(
        station_verdict=verdict,
        threshold_ladder_peaks=ladder,
        pyramid_peak_hour=pyramid_peak,
        pairing=pairing,
        wet_hour_fraction=wet_fraction,
        bootstrap=bootstrap,
        disjoint_period_peak_diff_hours=disjoint_diff,
    )
