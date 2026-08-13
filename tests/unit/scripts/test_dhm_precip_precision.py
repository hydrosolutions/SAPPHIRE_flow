"""Task 2c — reporting precision and intensity.

Includes the KEY plan-named regression test (task 2c verification): "must
not use Pearson correlation between quantile vectors (vision rule 2) — a
regression test asserts the transferability statistic separates an
exponential from a Pareto sample."
"""

from __future__ import annotations

import random
from datetime import datetime

import numpy as np
import polars as pl
import pytest

from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.stats_precision import (
    infer_group_membership,
    leave_one_out_tail_prediction_error,
    per_station_intensity_quantiles,
    per_station_modal_intensity,
    per_station_subthreshold_mass_fraction,
    per_station_wet_hour_fraction,
    shape_ratio,
    shape_ratio_cv,
)


class TestRule2QuantileVectorCorrelationTrap:
    """The KEY criterion: our transferability statistic must separate an
    exponential sample from a Pareto sample, where naive Pearson correlation
    between their quantile vectors famously cannot (vision rule 2, verified
    there: exponential vs Pareto r = 0.943 — near 1, useless for detection)."""

    def test_shape_ratio_separates_exponential_from_pareto(self) -> None:
        rng = np.random.default_rng(158)
        exponential = rng.exponential(scale=1.0, size=20_000)
        # A heavy-tailed Pareto (small shape parameter) — same rng, independent draw.
        pareto = rng.pareto(a=1.16, size=20_000) + 1.0

        exp_ratio = shape_ratio(
            exponential, numerator_quantile=0.99, denominator_quantile=0.5
        )
        pareto_ratio = shape_ratio(
            pareto, numerator_quantile=0.99, denominator_quantile=0.5
        )

        # Demonstrate the trap first: quantile-vector Pearson r is near 1 for
        # BOTH pairs, so it cannot discriminate (this is what rule 2 forbids
        # relying on).
        grid = np.linspace(0.5, 0.99, 20)
        exp_quantiles = np.quantile(exponential, grid)
        pareto_quantiles = np.quantile(pareto, grid)
        quantile_vector_r = np.corrcoef(exp_quantiles, pareto_quantiles)[0, 1]
        assert quantile_vector_r > 0.85, (
            "the trap: quantile-vector r is near 1 regardless of shape"
        )

        # The statistic we actually use DOES separate them — a heavy Pareto
        # tail gives a materially larger q99/q50 ratio than an exponential's.
        assert pareto_ratio > exp_ratio * 1.5

    def test_shape_ratio_cv_is_near_zero_for_the_same_family(self) -> None:
        rng = np.random.default_rng(158)
        ratios = [
            shape_ratio(
                rng.exponential(scale=1.0, size=5_000),
                numerator_quantile=0.99,
                denominator_quantile=0.5,
            )
            for _ in range(8)
        ]
        assert shape_ratio_cv(ratios) < 0.25


class TestShapeRatio:
    def test_returns_nan_for_empty_input(self) -> None:
        assert np.isnan(
            shape_ratio([], numerator_quantile=0.99, denominator_quantile=0.5)
        )

    def test_computes_the_expected_ratio_on_a_known_sample(self) -> None:
        values = list(range(1, 101))  # 1..100
        ratio = shape_ratio(values, numerator_quantile=0.5, denominator_quantile=0.25)
        median = np.quantile(values, 0.5)
        q25 = np.quantile(values, 0.25)
        assert ratio == pytest.approx(median / q25)


class TestLeaveOneOutTailPredictionError:
    def test_perfect_pooled_ratio_gives_zero_error(self) -> None:
        # Every station shares q99 = 10 * median exactly -> LOO prediction is exact.
        medians = {"A": 1.0, "B": 2.0, "C": 3.0}
        q99s = {s: v * 10 for s, v in medians.items()}
        result = leave_one_out_tail_prediction_error(medians, q99s)
        assert result["median_abs_error"] == pytest.approx(0.0)
        assert result["within_25pct_fraction"] == pytest.approx(1.0)

    def test_a_divergent_station_produces_a_large_error(self) -> None:
        medians = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}
        q99s = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 100.0}  # D is an outlier tail
        result = leave_one_out_tail_prediction_error(medians, q99s)
        # D's own held-out prediction (pooled ratio from A/B/C only) badly
        # undershoots D's actual q99 — the minimum (most negative) error.
        assert result["min_error"] < -0.5
        # A/B/C's predictions are inflated by pooling in D's outlier ratio.
        assert result["max_error"] > 0.5


def _on_grid_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


class TestInferGroupMembership:
    def test_classifies_by_reporting_resolution(self) -> None:
        rng = random.Random(1)
        rows = []
        for hour in range(50):
            rows.append(
                {
                    "station": "FineStation",
                    "timestamp": datetime(2024, 6, 1, hour % 24),
                    "value_mm": round(rng.uniform(0, 1), 2),
                }
            )
            rows.append(
                {
                    "station": "CoarseStation",
                    "timestamp": datetime(2024, 6, 1, hour % 24),
                    "value_mm": round(rng.choice([0.0, 0.2, 0.4, 0.6, 0.8]), 2),
                }
            )
        frame = _on_grid_frame(rows)
        result = infer_group_membership(frame, DEFAULT_PARAMS)
        groups = dict(
            zip(result["station"].to_list(), result["group"].to_list(), strict=True)
        )
        assert groups["CoarseStation"] == "B"


class TestWetHourFractionAndQuantiles:
    def test_wet_hour_fraction_is_jjas_scoped(self) -> None:
        rows = [
            {
                "station": "A",
                "timestamp": datetime(2024, 6, 1, 0),
                "value_mm": 0.5,
            },  # JJAS, wet
            {
                "station": "A",
                "timestamp": datetime(2024, 6, 1, 1),
                "value_mm": 0.0,
            },  # JJAS, dry
            {
                "station": "A",
                "timestamp": datetime(2024, 1, 1, 0),
                "value_mm": 5.0,
            },  # not JJAS
        ]
        frame = _on_grid_frame(rows)
        result = per_station_wet_hour_fraction(frame, DEFAULT_PARAMS)
        assert result["wet_hour_fraction"][0] == pytest.approx(0.5)

    def test_intensity_quantiles_exclude_dry_hours(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2024, 6, 1, h), "value_mm": v}
            for h, v in enumerate([0.0, 0.0, 1.0, 2.0, 3.0])
        ]
        frame = _on_grid_frame(rows)
        params = DEFAULT_PARAMS.__class__(quantile_grid=(0.5,))
        result = per_station_intensity_quantiles(frame, params)
        assert result["q0.5"][0] == pytest.approx(2.0)  # median of [1,2,3]


class TestPerStationModalIntensity:
    def test_reports_the_bin_with_the_most_observations(self) -> None:
        from scripts.dhm_precip.params import DhmPrecipParams

        params = DhmPrecipParams(modal_bin_width_mm=0.1)
        # 5 hours at 0.20 mm (its own 0.1-wide bin), 2 hours at 0.50, one
        # dry hour (excluded) and one sentinel-scale value that must not
        # shift the mode. Bin [0.2, 0.3) wins on count.
        values = [0.2, 0.21, 0.24, 0.29, 0.2, 0.5, 0.55, 0.0]
        rows = [
            {"station": "A", "timestamp": datetime(2024, 6, 1, h), "value_mm": v}
            for h, v in enumerate(values)
        ]
        frame = _on_grid_frame(rows)
        result = per_station_modal_intensity(frame, params)
        assert result["modal_value_mm"][0] == pytest.approx(0.2)

    def test_excludes_dry_and_null_hours(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2024, 6, 1, 0), "value_mm": 0.0},
            {"station": "A", "timestamp": datetime(2024, 6, 1, 1), "value_mm": None},
            {"station": "A", "timestamp": datetime(2024, 6, 1, 2), "value_mm": 0.3},
        ]
        frame = _on_grid_frame(rows)
        result = per_station_modal_intensity(frame, DEFAULT_PARAMS)
        assert result.height == 1
        assert result["modal_value_mm"][0] == pytest.approx(0.3)


class TestSubthresholdMassFraction:
    def test_numerator_is_never_forced_to_zero_by_the_wet_filter(self) -> None:
        # This is the documented bug this function once had: filtering to
        # the wet population (>= 0.2) BEFORE computing sub-0.1mm mass makes
        # the numerator always zero, since sub-0.1 values are never wet.
        rows = [
            {"station": "A", "timestamp": datetime(2024, 6, 1, 0), "value_mm": 0.03},
            {"station": "A", "timestamp": datetime(2024, 6, 1, 1), "value_mm": 0.05},
            {"station": "A", "timestamp": datetime(2024, 6, 1, 2), "value_mm": 1.0},
        ]
        frame = _on_grid_frame(rows)
        result = per_station_subthreshold_mass_fraction(frame, DEFAULT_PARAMS)
        assert result["subthreshold_mass_fraction"][0] == pytest.approx(0.08 / 1.08)
