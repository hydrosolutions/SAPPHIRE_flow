from __future__ import annotations

import numpy as np

from sapphire_flow.services.skill.diagrams import (
    compute_rank_histogram,
    compute_reliability_diagram,
    compute_roc_curve,
)


class TestComputeReliabilityDiagram:
    def test_reliability_diagram_shape(self) -> None:
        rng = np.random.default_rng(0)
        n_times, n_members = 100, 21
        ensemble = rng.uniform(0, 20, (n_times, n_members))
        observed = rng.uniform(0, 20, n_times)
        result = compute_reliability_diagram(ensemble, observed, threshold=10.0)

        expected_keys = {"bins", "observed_freq", "forecast_freq", "sample_counts"}
        assert set(result.keys()) == expected_keys
        n_bins = 10
        assert len(result["bins"]) == n_bins
        assert len(result["observed_freq"]) == n_bins
        assert len(result["forecast_freq"]) == n_bins
        assert len(result["sample_counts"]) == n_bins

    def test_reliability_diagram_custom_bins(self) -> None:
        rng = np.random.default_rng(1)
        ensemble = rng.uniform(0, 20, (50, 10))
        observed = rng.uniform(0, 20, 50)
        result = compute_reliability_diagram(
            ensemble, observed, threshold=10.0, n_bins=5
        )
        assert len(result["bins"]) == 5

    def test_reliability_sample_counts_sum(self) -> None:
        rng = np.random.default_rng(2)
        n_times = 80
        ensemble = rng.uniform(0, 20, (n_times, 10))
        observed = rng.uniform(0, 20, n_times)
        result = compute_reliability_diagram(ensemble, observed, threshold=10.0)
        # All samples must be assigned to exactly one bin
        assert sum(result["sample_counts"]) == n_times


class TestComputeRocCurve:
    def test_roc_curve_shape(self) -> None:
        rng = np.random.default_rng(3)
        ensemble = rng.uniform(0, 20, (60, 21))
        observed = rng.uniform(0, 20, 60)
        result = compute_roc_curve(ensemble, observed, threshold=10.0)

        assert set(result.keys()) == {"false_alarm_rate", "hit_rate", "thresholds"}
        n = len(result["thresholds"])
        assert len(result["false_alarm_rate"]) == n
        assert len(result["hit_rate"]) == n
        assert n > 1

    def test_roc_thresholds_in_range(self) -> None:
        rng = np.random.default_rng(4)
        ensemble = rng.uniform(0, 20, (40, 5))
        observed = rng.uniform(0, 20, 40)
        result = compute_roc_curve(ensemble, observed, threshold=10.0)
        thresholds = result["thresholds"]
        assert min(thresholds) >= 0.0
        assert max(thresholds) <= 1.0


class TestComputeRankHistogram:
    def test_rank_histogram_shape(self) -> None:
        rng = np.random.default_rng(5)
        n_times, n_members = 50, 21
        ensemble = rng.uniform(0, 20, (n_times, n_members))
        observed = rng.uniform(0, 20, n_times)
        result = compute_rank_histogram(ensemble, observed)

        assert set(result.keys()) == {"ranks", "counts"}
        assert len(result["ranks"]) == n_members + 1
        assert len(result["counts"]) == n_members + 1

    def test_rank_histogram_counts_sum(self) -> None:
        rng = np.random.default_rng(6)
        n_times, n_members = 100, 10
        ensemble = rng.uniform(0, 20, (n_times, n_members))
        observed = rng.uniform(0, 20, n_times)
        result = compute_rank_histogram(ensemble, observed)
        assert sum(result["counts"]) == n_times

    def test_rank_histogram_perfect_ensemble(self) -> None:
        # Uniform ensemble should produce roughly uniform rank histogram
        rng = np.random.default_rng(7)
        n_times, n_members = 1000, 9
        # Draw obs from same distribution as members → should be uniform
        ensemble = rng.uniform(0, 1, (n_times, n_members))
        observed = rng.uniform(0, 1, n_times)
        result = compute_rank_histogram(ensemble, observed)
        counts = np.array(result["counts"])
        expected = n_times / (n_members + 1)
        # Allow ±50% deviation for stochastic test
        assert all(abs(c - expected) < expected * 0.5 for c in counts)
