"""Deterministic fixture generator for Plan 043 e2e test."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl

OUTPUT_PATH = Path(__file__).parent / "bafu_observations.parquet"

# Station config: code -> (base_flow, is_alpine, a, b)
# a, b are stage-discharge power-law params: h = a * Q^b
STATIONS: dict[str, tuple[float, bool, float | None, float | None]] = {
    "2033": (15.0, True, None, None),  # Andermatt — discharge only
    "2044": (80.0, True, None, None),  # Hagneck — discharge only
    "2004": (120.0, False, 0.5, 0.4),  # Bern Schönau — discharge + water_level
    "2009": (200.0, False, 0.45, 0.38),  # Brugg — discharge + water_level
    "2091": (60.0, True, 0.55, 0.42),  # Brienzwiler — discharge + water_level
    "2159": (900.0, True, 0.3, 0.35),  # Basel Rheinhalle — discharge + water_level
    "2085": (250.0, False, 0.48, 0.39),  # Bellinzona — discharge + water_level
}

DISCHARGE_ONLY = {"2033", "2044"}

# 2-year hourly period
START = "2023-01-01 00:00:00"
END = "2024-12-31 23:00:00"


def generate_discharge(
    rng: np.random.Generator,
    timestamps: np.ndarray,
    base_flow: float,
    is_alpine: bool,
) -> np.ndarray:
    n = len(timestamps)
    # Convert timestamps (ns) to fractional day-of-year and hour
    ts_s = timestamps.astype("datetime64[s]").astype(np.int64)
    seconds_per_year = 365.25 * 24 * 3600
    t_year = ts_s / seconds_per_year  # fractional years since epoch
    t_hour = (ts_s % 86400) / 3600  # hour of day

    # Seasonal: peak May-June (day ~150 of year = fraction ~0.41)
    seasonal_phase = 2 * np.pi * (t_year - 0.41)
    seasonal_amp = 0.5 * base_flow
    seasonal = seasonal_amp * np.sin(seasonal_phase)

    # Diurnal: peak at 14:00 for alpine (snowmelt), muted for non-alpine
    diurnal_amp = (0.10 if is_alpine else 0.05) * base_flow
    diurnal_phase = 2 * np.pi * ((t_hour - 14) / 24)
    diurnal = diurnal_amp * np.sin(diurnal_phase)

    # Log-normal noise
    noise = rng.standard_normal(n) * 0.05
    noise_mult = np.exp(noise)

    q = (base_flow + seasonal + diurnal) * noise_mult
    return np.clip(q, 0.01, None)


def generate_water_level(
    rng: np.random.Generator,
    discharge: np.ndarray,
    a: float,
    b: float,
) -> np.ndarray:
    # Clip to non-negative before power-law to avoid NaN from negative Q;
    # anomalies are re-injected separately after this call.
    q_safe = np.clip(discharge, 0.001, None)
    h = a * np.power(q_safe, b)
    noise = rng.standard_normal(len(discharge)) * 0.01
    return np.clip(h + noise, 0.001, None)


def inject_anomalies(
    values: np.ndarray,
    rng: np.random.Generator,
    n_total: int,
) -> np.ndarray:
    out = values.copy()

    # Spike at ~month 3 (hour ~2160)
    spike_idx = n_total // 12 * 3
    local_mean = float(np.mean(out[max(0, spike_idx - 24) : spike_idx + 24]))
    out[spike_idx] = local_mean * 10.0

    # Frozen sensor at ~month 9 (hour ~6480), 12 consecutive hours
    freeze_idx = n_total // 12 * 9
    freeze_val = out[freeze_idx]
    out[freeze_idx : freeze_idx + 12] = freeze_val

    # Range violation at ~month 15 (hour ~10920)
    violation_idx = n_total // 24 * 15
    out[violation_idx] = -0.1

    return out


def build_station_rows(
    rng: np.random.Generator,
    code: str,
    timestamps: np.ndarray,
    base_flow: float,
    is_alpine: bool,
    a: float | None,
    b: float | None,
) -> list[dict]:
    rows: list[dict] = []
    n = len(timestamps)

    discharge = generate_discharge(rng, timestamps, base_flow, is_alpine)
    discharge = inject_anomalies(discharge, rng, n)

    ts_list = timestamps.astype("datetime64[ms]").tolist()

    for i, ts in enumerate(ts_list):
        rows.append(
            {
                "station_code": code,
                "timestamp": ts,
                "parameter": "discharge",
                "value": float(discharge[i]),
                "source": "measured",
            }
        )

    if a is not None and b is not None:
        water_level = generate_water_level(rng, discharge, a, b)
        water_level = inject_anomalies(water_level, rng, n)
        for i, ts in enumerate(ts_list):
            rows.append(
                {
                    "station_code": code,
                    "timestamp": ts,
                    "parameter": "water_level",
                    "value": float(water_level[i]),
                    "source": "measured",
                }
            )

    return rows


def main() -> None:
    rng = np.random.default_rng(42)

    timestamps = np.arange(
        np.datetime64(START),
        np.datetime64(END) + np.timedelta64(1, "h"),
        np.timedelta64(1, "h"),
        dtype="datetime64[h]",
    )

    all_rows: list[dict] = []

    for code, (base_flow, is_alpine, a, b) in STATIONS.items():
        rows = build_station_rows(rng, code, timestamps, base_flow, is_alpine, a, b)
        all_rows.extend(rows)
        n_params = 2 if code not in DISCHARGE_ONLY else 1
        print(  # noqa: T201
            f"  {code}: {len(timestamps)} rows × {n_params} param(s) = {len(rows)} rows"
        )

    df = pl.DataFrame(all_rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("station_code").cast(pl.Utf8),
        pl.col("parameter").cast(pl.Utf8),
        pl.col("value").cast(pl.Float64),
        pl.col("source").cast(pl.Utf8),
    )

    df.write_parquet(OUTPUT_PATH)
    print(f"\nWrote {len(df):,} rows to {OUTPUT_PATH}")  # noqa: T201

    summary = (
        df.group_by(["station_code", "parameter"])
        .agg(pl.len().alias("n_rows"))
        .sort(["station_code", "parameter"])
    )
    print("\nSummary:")  # noqa: T201
    print(summary)  # noqa: T201


if __name__ == "__main__":
    main()
