from __future__ import annotations

import numpy as np


def compute_crps(ensemble: np.ndarray, observed: float) -> float:
    n = len(ensemble)
    abs_diff = np.abs(ensemble - observed).mean()
    sorted_ens = np.sort(ensemble)
    spread = np.sum(np.abs(sorted_ens[:, None] - sorted_ens[None, :])) / (n * n)
    return float(abs_diff - 0.5 * spread)


def compute_crpss(crps: float, reference_crps: float) -> float:
    if reference_crps == 0:
        return 0.0
    return 1.0 - crps / reference_crps


def compute_nse(predicted: np.ndarray, observed: np.ndarray) -> float:
    numerator = np.sum((observed - predicted) ** 2)
    denominator = np.sum((observed - np.mean(observed)) ** 2)
    if denominator == 0:
        return float("nan")
    return float(1.0 - numerator / denominator)


def compute_kge(predicted: np.ndarray, observed: np.ndarray) -> float:
    if len(predicted) < 2:
        return float("nan")
    r = np.corrcoef(predicted, observed)[0, 1]
    std_obs = np.std(observed)
    mean_obs = np.mean(observed)
    alpha = np.std(predicted) / std_obs if std_obs > 0 else float("nan")
    beta = np.mean(predicted) / mean_obs if mean_obs > 0 else float("nan")
    return float(1.0 - np.sqrt((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2))


def compute_pbias(predicted: np.ndarray, observed: np.ndarray) -> float:
    denom = np.sum(observed)
    if denom == 0:
        return float("nan")
    return float(100.0 * np.sum(predicted - observed) / denom)


def compute_mae(predicted: np.ndarray, observed: np.ndarray) -> float:
    return float(np.mean(np.abs(predicted - observed)))


def compute_bss(ensemble: np.ndarray, observed: np.ndarray, threshold: float) -> float:
    # ensemble: 2D (n_times, n_members), observed: 1D (n_times)
    forecast_prob = np.mean(ensemble > threshold, axis=1)
    observed_binary = (observed > threshold).astype(float)
    bs = np.mean((forecast_prob - observed_binary) ** 2)
    climatology = np.mean(observed_binary)
    bs_ref = climatology * (1 - climatology)
    if bs_ref == 0:
        return float("nan")
    return float(1.0 - bs / bs_ref)


def compute_contingency(
    ensemble: np.ndarray,
    observed: np.ndarray,
    threshold: float,
    decision_probability: float,
) -> tuple[float, float, float]:
    # Returns (POD, FAR, CSI)
    forecast_prob = np.mean(ensemble > threshold, axis=1)
    forecast_yes = forecast_prob >= decision_probability
    observed_yes = observed > threshold
    hits = np.sum(forecast_yes & observed_yes)
    misses = np.sum(~forecast_yes & observed_yes)
    false_alarms = np.sum(forecast_yes & ~observed_yes)
    pod = float(hits / (hits + misses)) if (hits + misses) > 0 else float("nan")
    far = (
        float(false_alarms / (hits + false_alarms))
        if (hits + false_alarms) > 0
        else float("nan")
    )
    csi = (
        float(hits / (hits + misses + false_alarms))
        if (hits + misses + false_alarms) > 0
        else float("nan")
    )
    return pod, far, csi


def compute_peak_timing_error(
    predicted_median: np.ndarray,
    observed: np.ndarray,
    peak_threshold: float,
) -> float | None:
    obs_peaks = np.where(observed > peak_threshold)[0]
    if len(obs_peaks) == 0:
        return None
    pred_peaks = np.where(predicted_median > peak_threshold)[0]
    if len(pred_peaks) == 0:
        return None
    errors = [
        abs(int(pred_peaks[np.argmin(np.abs(pred_peaks - op))]) - int(op))
        for op in obs_peaks
    ]
    return float(np.mean(errors))


def compute_sharpness(ensemble: np.ndarray) -> tuple[float, float, float]:
    # ensemble: 2D (n_times, n_members)
    # Returns (mean_p10_p90_width, mean_p25_p75_width, mean_ensemble_range)
    p10 = np.percentile(ensemble, 10, axis=1)
    p90 = np.percentile(ensemble, 90, axis=1)
    p25 = np.percentile(ensemble, 25, axis=1)
    p75 = np.percentile(ensemble, 75, axis=1)
    return (
        float(np.mean(p90 - p10)),
        float(np.mean(p75 - p25)),
        float(np.mean(np.max(ensemble, axis=1) - np.min(ensemble, axis=1))),
    )
