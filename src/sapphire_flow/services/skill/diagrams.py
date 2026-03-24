from __future__ import annotations

import numpy as np


def compute_reliability_diagram(
    ensemble: np.ndarray,
    observed: np.ndarray,
    threshold: float,
    n_bins: int = 10,
) -> dict:
    # ensemble: 2D (n_times, n_members), observed: 1D (n_times)
    forecast_prob = np.mean(ensemble > threshold, axis=1)
    observed_binary = (observed > threshold).astype(float)

    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])

    observed_freq = np.full(n_bins, float("nan"))
    sample_counts = np.zeros(n_bins, dtype=int)

    for i in range(n_bins):
        mask = (forecast_prob >= bin_edges[i]) & (forecast_prob < bin_edges[i + 1])
        if i == n_bins - 1:
            mask = (forecast_prob >= bin_edges[i]) & (forecast_prob <= bin_edges[i + 1])
        count = int(np.sum(mask))
        sample_counts[i] = count
        if count > 0:
            observed_freq[i] = float(np.mean(observed_binary[mask]))

    return {
        "bins": bin_centers.tolist(),
        "observed_freq": observed_freq.tolist(),
        "forecast_freq": bin_centers.tolist(),
        "sample_counts": sample_counts.tolist(),
    }


def compute_roc_curve(
    ensemble: np.ndarray,
    observed: np.ndarray,
    threshold: float,
) -> dict:
    # ensemble: 2D (n_times, n_members), observed: 1D (n_times)
    forecast_prob = np.mean(ensemble > threshold, axis=1)
    observed_binary = (observed > threshold).astype(bool)

    prob_thresholds = np.linspace(0.0, 1.0, 101)
    hit_rates = np.zeros(len(prob_thresholds))
    false_alarm_rates = np.zeros(len(prob_thresholds))

    n_events = int(np.sum(observed_binary))
    n_non_events = int(np.sum(~observed_binary))

    for i, pt in enumerate(prob_thresholds):
        forecast_yes = forecast_prob >= pt
        hits = int(np.sum(forecast_yes & observed_binary))
        false_alarms = int(np.sum(forecast_yes & ~observed_binary))
        hit_rates[i] = hits / n_events if n_events > 0 else float("nan")
        false_alarm_rates[i] = (
            false_alarms / n_non_events if n_non_events > 0 else float("nan")
        )

    return {
        "false_alarm_rate": false_alarm_rates.tolist(),
        "hit_rate": hit_rates.tolist(),
        "thresholds": prob_thresholds.tolist(),
    }


def compute_rank_histogram(
    ensemble: np.ndarray,
    observed: np.ndarray,
) -> dict:
    # ensemble: 2D (n_times, n_members), observed: 1D (n_times)
    n_members = ensemble.shape[1]
    n_bins = n_members + 1
    counts = np.zeros(n_bins, dtype=int)

    for i in range(len(observed)):
        rank = int(np.sum(ensemble[i] < observed[i]))
        rank = min(rank, n_bins - 1)
        counts[rank] += 1

    return {
        "ranks": list(range(n_bins)),
        "counts": counts.tolist(),
    }
