"""Plan 182 (M-A10) — normalised diurnal profiles, the D7.2 zeroing
ablation, and the D3 common-retained-timestamp pairing.

Pure functions over `(station, timestamp, value_mm)` frames, no I/O (D4,
matching `stats_coherence.py`/`stats_climatology.py`). Callers are
responsible for restricting the input to the population under study: the
DHM side to its M-A3-masked, JJAS, on-grid retained rows; the Pyramid side
to its physical-range-checked retained rows (D3 — "applying our
defect-specific rules to another network's instrument remains
unjustified"). Nothing here re-derives or re-applies either QC policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.numeric import as_int

if TYPE_CHECKING:
    from scripts.dhm_precip.domain_types import Station


class NoProfileRowsError(ValueError):
    """`peak_hour` was asked for a station with zero retained profile rows."""


class NonPositiveGrandMeanError(ValueError):
    """`normalised_diurnal_profile` found a station whose retained values
    sum to a non-positive or non-finite grand mean (e.g. every retained hour
    is exactly zero, or every value is null/NaN after upstream QC). Dividing
    by it would silently produce an infinite, NaN or meaningless
    `normalised_value` — `peak_hour`'s sort would then pick an arbitrary
    hour rather than report "no usable signal". Raised instead, so a caller
    can map it to INDETERMINATE rather than trust a garbage peak."""


def zero_below_threshold(frame: pl.DataFrame, threshold_mm: float) -> pl.DataFrame:
    """D7.2 — the ablation ZEROES values below `threshold_mm`; it never
    DROPS the row. Zeroing (not dropping) is what makes the ablation
    diagnostic sound: a threshold applied uniformly across every hour is a
    common scalar shift and cannot, by itself, move WHICH hour has the
    highest mean — only a threshold that removes a genuinely
    hour-concentrated contribution can do that (see
    `stats_coloc.normalised_diurnal_profile` + `peak_hour` used together in
    the threshold ladder). Nulls are left as null (missing, not "below
    threshold")."""
    return frame.with_columns(
        pl.when(pl.col("value_mm").is_not_null() & (pl.col("value_mm") < threshold_mm))
        .then(0.0)
        .otherwise(pl.col("value_mm"))
        .alias("value_mm")
    )


def normalised_diurnal_profile(frame: pl.DataFrame) -> pl.DataFrame:
    """D1 — each hour's mean value divided by the station's own grand mean
    across every retained hour in `frame` (never a magnitude comparison
    across stations/networks). Returns `(station, hour, mean_value_mm, n,
    normalised_value)`. `frame` must already be restricted to the
    population under study — this drops null `value_mm` rows (a retained
    frame should not contain any) but performs no season/mask filtering of
    its own."""
    with_hour = frame.filter(pl.col("value_mm").is_not_null()).with_columns(
        pl.col("timestamp").dt.hour().alias("hour")
    )
    hourly = with_hour.group_by(["station", "hour"]).agg(
        pl.col("value_mm").mean().alias("mean_value_mm"),
        pl.len().alias("n"),
    )
    grand_mean = with_hour.group_by("station").agg(
        pl.col("value_mm").mean().alias("_grand_mean")
    )
    bad = grand_mean.filter(
        pl.col("_grand_mean").is_null()
        | pl.col("_grand_mean").is_nan()
        | pl.col("_grand_mean").is_infinite()
        | (pl.col("_grand_mean") <= 0.0)
    )
    if bad.height > 0:
        stations = sorted(str(s) for s in bad["station"].to_list())
        raise NonPositiveGrandMeanError(
            f"non-positive or non-finite grand mean for station(s) {stations} "
            "— cannot normalise (every retained hour is zero, or no usable "
            "signal survived upstream QC)"
        )
    return (
        hourly.join(grand_mean, on="station")
        .with_columns(
            (pl.col("mean_value_mm") / pl.col("_grand_mean")).alias("normalised_value")
        )
        .drop("_grand_mean")
    )


def dhm_utc_to_npt(
    frame: pl.DataFrame, *, hour_offset: int, jjas_months: tuple[int, ...]
) -> pl.DataFrame:
    """D2 — the UTC->NPT reconciliation that makes DHM (UTC, period-ending)
    and Pyramid (NPT wall-clock) comparable on the same clock. Shifts every
    `timestamp` forward by `hour_offset` WHOLE hours (D2's rounded 5h45m
    offset, `params.coloc_dhm_utc_to_npt_hour_offset` — normally 6) and
    RE-APPLIES the JJAS filter on the SHIFTED timestamp: a UTC-JJAS hour can
    cross a calendar-month boundary once shifted into NPT (e.g. 30 Sept
    23:00 UTC -> 05:00 NPT the next day, with the default +6h offset), and
    silently retaining an hour that is no longer JJAS on the timebase
    everything else is compared on would reintroduce exactly the kind of
    unmatched-population artefact D3 exists to prevent.

    Every OTHER function in this module is network-agnostic — this one is
    NOT: it must be called on DHM frames only, never on Pyramid's (which
    are already NPT)."""
    shifted = frame.with_columns(
        (pl.col("timestamp") + pl.duration(hours=hour_offset)).alias("timestamp")
    )
    return shifted.filter(pl.col("timestamp").dt.month().is_in(jjas_months))


def peak_hour(profile: pl.DataFrame, *, station: Station) -> int:
    """The hour-of-day with the highest `normalised_value` for `station`.
    Ties break to the lowest hour (deterministic, never arbitrary row
    order)."""
    rows = profile.filter(pl.col("station") == station)
    if rows.height == 0:
        raise NoProfileRowsError(f"no profile rows for station {station!r}")
    best = rows.sort(["normalised_value", "hour"], descending=[True, False]).row(
        0, named=True
    )
    return as_int(best["hour"])


@dataclass(frozen=True, kw_only=True, slots=True)
class PairedRetention:
    """D3 — the pairing result: the common-retained-timestamp join, plus
    each side's OWN retention count so the pairing loss stays visible."""

    paired: pl.DataFrame
    """Columns `(timestamp, dhm_value_mm, pyramid_value_mm)`."""
    n_dhm_retained: int
    n_pyramid_retained: int
    n_common_retained: int


def common_retained_frame(
    dhm_retained: pl.DataFrame, pyramid_retained: pl.DataFrame
) -> PairedRetention:
    """D3 — 'Compare on COMMON RETAINED TIMESTAMPS.' Pairs the two already
    QC-filtered series hour-by-hour on `timestamp` and keeps only hours
    retained on BOTH sides. Performs no QC of its own — `dhm_retained` and
    `pyramid_retained` must already be each side's own retained population."""
    dhm = dhm_retained.select(
        pl.col("timestamp"), pl.col("value_mm").alias("dhm_value_mm")
    )
    pyramid = pyramid_retained.select(
        pl.col("timestamp"), pl.col("value_mm").alias("pyramid_value_mm")
    )
    paired = dhm.join(pyramid, on="timestamp", how="inner")
    return PairedRetention(
        paired=paired,
        n_dhm_retained=dhm.height,
        n_pyramid_retained=pyramid.height,
        n_common_retained=paired.height,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class PairedWetHourFraction:
    n_common_retained: int
    dhm_wet_fraction: float
    pyramid_wet_fraction: float


class EmptyPairedPopulationError(ValueError):
    """`paired_wet_hour_fraction` was asked to compute over zero common-
    retained timestamps — no comparison is possible."""


def paired_wet_hour_fraction(
    paired: pl.DataFrame, *, wet_threshold_mm: float
) -> PairedWetHourFraction:
    """D3 — 'The wet-hour fraction is reported ONLY on the paired
    population.' `wet_threshold_mm` must be the SAME on both sides (D7.1's
    residual note: an unmatched threshold measures instrument resolution,
    not weather) — this function applies exactly one threshold to both
    columns, by construction."""
    if paired.height == 0:
        raise EmptyPairedPopulationError(
            "paired frame is empty — no common retained timestamps"
        )
    dhm_wet = paired.filter(pl.col("dhm_value_mm") >= wet_threshold_mm).height
    pyramid_wet = paired.filter(pl.col("pyramid_value_mm") >= wet_threshold_mm).height
    return PairedWetHourFraction(
        n_common_retained=paired.height,
        dhm_wet_fraction=dhm_wet / paired.height,
        pyramid_wet_fraction=pyramid_wet / paired.height,
    )
