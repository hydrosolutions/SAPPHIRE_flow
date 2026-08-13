"""Task 2c — reporting precision and intensity.

Rule 2 (`docs/design/dhm-precipitation-milestones.md`): comparing
distributions via Pearson correlation between quantile vectors gives r≈1 by
construction and must not be used to claim transferability. This module's
`shape_ratio` / `shape_ratio_cv` / `leave_one_out_tail_prediction_error` are
the scale-normalised alternatives; `tests/unit/scripts/test_dhm_precip_precision.py`
carries the regression test proving they separate an exponential from a
Pareto sample where quantile-vector Pearson r cannot (vision rule 2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from scripts.dhm_precip.domain_types import AxisStatus, View
from scripts.dhm_precip.resolution import infer_reporting_resolution

if TYPE_CHECKING:
    from collections.abc import Sequence

    from scripts.dhm_precip.params import DhmPrecipParams


def _jjas(frame: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    return frame.filter(pl.col("timestamp").dt.month().is_in(params.jjas_months))


def _wet_mask(params: DhmPrecipParams) -> pl.Expr:
    threshold = pl.lit(params.wet_threshold_mm_per_h)
    if params.wet_threshold_side == ">=":
        return pl.col("value_mm") >= threshold
    return pl.col("value_mm") > threshold


def _tag(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.RAW_PROVISIONAL.value).alias("axis_status"),
    )


def per_station_wet_hour_fraction(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    jjas = _jjas(on_grid, params).filter(pl.col("value_mm").is_not_null())
    per_station = jjas.group_by("station").agg(
        _wet_mask(params).sum().alias("wet_count"),
        pl.len().alias("total_count"),
    )
    return _tag(
        per_station.with_columns(
            (pl.col("wet_count") / pl.col("total_count")).alias("wet_hour_fraction")
        )
    )


def per_station_intensity_quantiles(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """Wet-hour (JJAS) intensity quantiles per station, over `params.quantile_grid`."""
    jjas_wet = _jjas(on_grid, params).filter(
        pl.col("value_mm").is_not_null() & _wet_mask(params)
    )
    aggs = [
        pl.col("value_mm").quantile(q, interpolation="linear").alias(f"q{q}")
        for q in params.quantile_grid
    ]
    return _tag(jjas_wet.group_by("station").agg(*aggs))


def per_station_subthreshold_mass_fraction(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """Sub-0.1mm recorded mass as a fraction of total recorded (non-zero)
    mass, per station — "bounds the contribution of the noise floor to the
    recorded total" (vision). Population is all non-zero recorded values,
    NOT the (mutually exclusive) `wet_threshold_mm_per_h` population — a
    value below 0.1 mm is by definition below the 0.2 mm/h wet threshold, so
    restricting to wet hours first would make the numerator always zero."""
    jjas_recorded = _jjas(on_grid, params).filter(
        pl.col("value_mm").is_not_null() & (pl.col("value_mm") > 0.0)
    )
    per_station = jjas_recorded.group_by("station").agg(
        pl.col("value_mm")
        .filter(pl.col("value_mm") < 0.1)
        .sum()
        .alias("subthreshold_mass_mm"),
        pl.col("value_mm").sum().alias("total_mass_mm"),
    )
    return _tag(
        per_station.with_columns(
            (pl.col("subthreshold_mass_mm") / pl.col("total_mass_mm")).alias(
                "subthreshold_mass_fraction"
            )
        )
    )


def per_station_modal_intensity(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """Method D8b `modal_binning` — per-station modal non-zero value, JJAS
    (vision: "Group A's modal **non-zero** value" — deliberately NOT
    restricted to the wet threshold; the vision's own quoted range,
    0.03-0.06 mm/h, sits below the 0.2 mm/h wet floor, so a wet-restricted
    population could never reproduce it). Bin non-zero JJAS values into
    fixed `modal_bin_width_mm`-wide bins and report the bin (left edge)
    with the most observations. Ties broken by the smallest bin
    (deterministic)."""
    jjas_nonzero = _jjas(on_grid, params).filter(
        pl.col("value_mm").is_not_null() & (pl.col("value_mm") > 0.0)
    )
    # `+ resolution_epsilon_mm / modal_bin_width_mm`: a value that is an
    # exact multiple of the bin width (e.g. 0.3 with a 0.1 mm bin) can land
    # a hair below the intended integer ratio in float64 (0.3 / 0.1 ==
    # 2.9999999999999996), which `.floor()` would silently misbin one step
    # low. The nudge is far smaller than any real reading difference.
    binned = jjas_nonzero.with_columns(
        (
            pl.col("value_mm") / params.modal_bin_width_mm
            + params.resolution_epsilon_mm / params.modal_bin_width_mm
        )
        .floor()
        .mul(params.modal_bin_width_mm)
        .alias("_bin")
    )
    counts = binned.group_by(["station", "_bin"]).agg(pl.len().alias("_count"))
    modal = (
        counts.sort(["_count", "_bin"], descending=[True, False])
        .group_by("station", maintain_order=True)
        .agg(pl.col("_bin").first().alias("modal_value_mm"))
    )
    return _tag(modal)


def shape_ratio(
    values: Sequence[float] | np.ndarray,
    *,
    numerator_quantile: float,
    denominator_quantile: float,
) -> float:
    """Rule 2 — a scale-normalised transferability statistic, NOT a
    quantile-vector correlation. `q_numerator / q_denominator` over one sample."""
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return float("nan")
    lo = float(np.quantile(arr, denominator_quantile))
    hi = float(np.quantile(arr, numerator_quantile))
    if lo == 0.0:
        return float("nan")
    return hi / lo


def shape_ratio_cv(per_station_ratios: Sequence[float]) -> float:
    """Coefficient of variation of the per-station shape ratio — Rule 2."""
    arr = np.asarray(
        [r for r in per_station_ratios if not np.isnan(r)], dtype=np.float64
    )
    if arr.size == 0 or arr.mean() == 0:
        return float("nan")
    return float(arr.std(ddof=0) / arr.mean())


def leave_one_out_tail_prediction_error(
    per_station_median: dict[str, float], per_station_q99: dict[str, float]
) -> dict[str, float]:
    """Rule 2 — predict each held-out station's q99 as
    `median_i * pooled_ratio(excluding i)`; return relative errors.

    Returns `{"median_abs_error", "min_error", "max_error", "within_25pct_fraction"}`.
    """
    stations = sorted(set(per_station_median) & set(per_station_q99))
    ratios = {
        s: per_station_q99[s] / per_station_median[s]
        for s in stations
        if per_station_median[s]
    }
    errors: list[float] = []
    for held_out in stations:
        others = [ratios[s] for s in stations if s != held_out and s in ratios]
        if (
            not others
            or per_station_median[held_out] == 0
            or per_station_q99[held_out] == 0
        ):
            continue
        pooled_ratio = float(np.mean(others))
        predicted = per_station_median[held_out] * pooled_ratio
        actual = per_station_q99[held_out]
        errors.append((predicted - actual) / actual)
    if not errors:
        return {
            "median_abs_error": float("nan"),
            "min_error": float("nan"),
            "max_error": float("nan"),
            "within_25pct_fraction": float("nan"),
        }
    arr = np.asarray(errors, dtype=np.float64)
    return {
        "median_abs_error": float(np.median(np.abs(arr))),
        "min_error": float(arr.min()),
        "max_error": float(arr.max()),
        "within_25pct_fraction": float(np.mean(np.abs(arr) <= 0.25)),
    }


def infer_group_membership(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """`RAW_PROVISIONAL` — per-station inferred reporting resolution (D7: this,
    unlike group elevation-overlap, is NOT AXIS_INDEPENDENT — it is computed
    after the ON_GRID selection and M-A2 may move formerly off-grid
    observations into that population)."""
    return _tag(infer_reporting_resolution(on_grid, params))
