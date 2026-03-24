from __future__ import annotations

import math

import numpy as np
import pytest

from sapphire_flow.services.skill.metrics import (
    compute_bss,
    compute_contingency,
    compute_crps,
    compute_kge,
    compute_mae,
    compute_nse,
    compute_pbias,
    compute_peak_timing_error,
    compute_sharpness,
)


class TestComputeCrps:
    def test_crps_perfect_forecast(self) -> None:
        observed = 5.0
        ensemble = np.full(10, observed)
        assert compute_crps(ensemble, observed) == pytest.approx(0.0)

    def test_crps_known_value(self) -> None:
        # ensemble=[1,2,3], observed=2.0
        # abs_diff = mean(|[1,2,3]-2|) = mean([1,0,1]) = 2/3
        # sorted=[1,2,3]
        # pairwise abs diffs: |1-1|+|1-2|+|1-3| + |2-1|+|2-2|+|2-3| + |3-1|+|3-2|+|3-3|
        #                   = 0+1+2 + 1+0+1 + 2+1+0 = 8
        # spread = 8 / 9
        # crps = 2/3 - 0.5 * 8/9 = 2/3 - 4/9 = 6/9 - 4/9 = 2/9
        ensemble = np.array([1.0, 2.0, 3.0])
        result = compute_crps(ensemble, 2.0)
        assert result == pytest.approx(2.0 / 9.0, abs=1e-10)

    def test_crps_nonnegative(self) -> None:
        rng = np.random.default_rng(0)
        ensemble = rng.uniform(0, 100, 50)
        observed = 42.0
        assert compute_crps(ensemble, observed) >= 0.0


class TestComputeNse:
    def test_nse_perfect(self) -> None:
        obs = np.array([1.0, 2.0, 3.0, 4.0])
        assert compute_nse(obs.copy(), obs) == pytest.approx(1.0)

    def test_nse_mean_prediction(self) -> None:
        obs = np.array([1.0, 2.0, 3.0, 4.0])
        predicted = np.full_like(obs, np.mean(obs))
        assert compute_nse(predicted, obs) == pytest.approx(0.0)

    def test_nse_constant_observed(self) -> None:
        obs = np.full(5, 3.0)
        pred = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = compute_nse(pred, obs)
        assert math.isnan(result)


class TestComputeKge:
    def test_kge_perfect(self) -> None:
        obs = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        assert compute_kge(obs.copy(), obs) == pytest.approx(1.0)

    def test_kge_range(self) -> None:
        rng = np.random.default_rng(1)
        obs = rng.uniform(1, 100, 50)
        pred = rng.uniform(1, 100, 50)
        result = compute_kge(pred, obs)
        assert not math.isnan(result)

    def test_kge_single_element(self) -> None:
        obs = np.array([5.0])
        pred = np.array([5.0])
        result = compute_kge(pred, obs)
        assert math.isnan(result)


class TestComputePbias:
    def test_pbias_no_bias(self) -> None:
        obs = np.array([1.0, 2.0, 3.0, 4.0])
        assert compute_pbias(obs.copy(), obs) == pytest.approx(0.0)

    def test_pbias_positive(self) -> None:
        obs = np.array([10.0, 20.0])
        pred = np.array([12.0, 24.0])
        # (2 + 4) / 30 * 100 = 20%
        assert compute_pbias(pred, obs) == pytest.approx(20.0)

    def test_pbias_zero_denominator(self) -> None:
        obs = np.zeros(3)
        pred = np.array([1.0, 2.0, 3.0])
        assert math.isnan(compute_pbias(pred, obs))


class TestComputeMae:
    def test_mae_known(self) -> None:
        obs = np.array([1.0, 2.0, 3.0])
        pred = np.array([2.0, 2.0, 2.0])
        # |1|+|0|+|1| / 3 = 2/3
        assert compute_mae(pred, obs) == pytest.approx(2.0 / 3.0)

    def test_mae_perfect(self) -> None:
        obs = np.array([5.0, 10.0, 15.0])
        assert compute_mae(obs.copy(), obs) == pytest.approx(0.0)


class TestComputeBss:
    def test_bss_perfect(self) -> None:
        # Half obs exceed threshold, forecast prob exactly matches (1 or 0)
        # This gives BS = 0 and BSS = 1
        obs = np.array([10.0] * 10 + [1.0] * 10)
        threshold = 5.0
        # Perfect deterministic forecast: all members above threshold for first half,
        # all below for second half
        ensemble = np.concatenate(
            [
                np.full((10, 5), 8.0),
                np.full((10, 5), 2.0),
            ]
        )
        result = compute_bss(ensemble, obs, threshold)
        assert result == pytest.approx(1.0)

    def test_bss_no_events(self) -> None:
        # No obs exceed threshold → climatology = 0 → bs_ref = 0 → nan
        n = 10
        obs = np.zeros(n)
        ensemble = np.zeros((n, 5))
        threshold = 1.0
        assert math.isnan(compute_bss(ensemble, obs, threshold))

    def test_bss_range(self) -> None:
        rng = np.random.default_rng(2)
        obs = rng.uniform(0, 20, 30)
        ensemble = rng.uniform(0, 20, (30, 10))
        result = compute_bss(ensemble, obs, threshold=10.0)
        assert not math.isnan(result)


class TestComputeContingency:
    def test_contingency_all_hits(self) -> None:
        # All obs and all forecasts exceed threshold
        n = 10
        obs = np.full(n, 20.0)
        ensemble = np.full((n, 5), 25.0)
        threshold = 10.0
        decision_prob = 0.5
        pod, far, csi = compute_contingency(ensemble, obs, threshold, decision_prob)
        assert pod == pytest.approx(1.0)
        assert far == pytest.approx(0.0)
        assert csi == pytest.approx(1.0)

    def test_contingency_all_misses(self) -> None:
        # Obs all exceed threshold but forecast prob < decision_probability
        n = 10
        obs = np.full(n, 20.0)
        ensemble = np.zeros((n, 5))
        threshold = 10.0
        decision_prob = 0.5
        pod, far, csi = compute_contingency(ensemble, obs, threshold, decision_prob)
        assert pod == pytest.approx(0.0)
        assert math.isnan(far)  # no forecasts issued → far undefined

    def test_contingency_all_false_alarms(self) -> None:
        # No obs exceed threshold but forecast always issues
        n = 10
        obs = np.zeros(n)
        ensemble = np.full((n, 5), 20.0)
        threshold = 10.0
        decision_prob = 0.5
        pod, far, csi = compute_contingency(ensemble, obs, threshold, decision_prob)
        assert math.isnan(pod)
        assert far == pytest.approx(1.0)
        assert csi == pytest.approx(0.0)


class TestComputePeakTimingError:
    def test_no_peaks_in_obs(self) -> None:
        obs = np.zeros(20)
        pred = np.array([0.0] * 10 + [10.0] * 10)
        assert compute_peak_timing_error(pred, obs, peak_threshold=5.0) is None

    def test_no_peaks_in_pred(self) -> None:
        obs = np.array([0.0] * 10 + [10.0] * 10)
        pred = np.zeros(20)
        assert compute_peak_timing_error(pred, obs, peak_threshold=5.0) is None

    def test_perfect_peak_timing(self) -> None:
        n = 20
        obs = np.zeros(n)
        pred = np.zeros(n)
        obs[10] = 100.0
        pred[10] = 100.0
        result = compute_peak_timing_error(pred, obs, peak_threshold=50.0)
        assert result == pytest.approx(0.0)

    def test_timing_error_known(self) -> None:
        n = 30
        obs = np.zeros(n)
        pred = np.zeros(n)
        obs[5] = 100.0
        pred[8] = 100.0  # 3 steps late
        result = compute_peak_timing_error(pred, obs, peak_threshold=50.0)
        assert result == pytest.approx(3.0)


class TestComputeSharpness:
    def test_sharpness_tight_ensemble(self) -> None:
        n_times, n_members = 10, 21
        # All members identical → zero spread
        ensemble = np.full((n_times, n_members), 5.0)
        p10_p90, p25_p75, ens_range = compute_sharpness(ensemble)
        assert p10_p90 == pytest.approx(0.0)
        assert p25_p75 == pytest.approx(0.0)
        assert ens_range == pytest.approx(0.0)

    def test_sharpness_wide_ensemble(self) -> None:
        n_times = 5
        # members range [0, 100]
        ensemble = np.tile(np.linspace(0, 100, 101), (n_times, 1))
        p10_p90, p25_p75, ens_range = compute_sharpness(ensemble)
        assert p10_p90 == pytest.approx(80.0, abs=1.0)
        assert p25_p75 == pytest.approx(50.0, abs=1.0)
        assert ens_range == pytest.approx(100.0, abs=0.1)
