"""Generate the synthetic Trishuli (00001) hourly water-level series.

One-off, seeded. Reads a real 15-minute lidar record only for its summary shape
(seasonal cycle, daily cycle, noise level); no real sample is copied.

    uv run python tests/fixtures/trishuli_synthetic/generate.py <lidar.csv>
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "trishuli_00001_synthetic_hourly.csv"
START, END = "2024-03-01 00:00", "2026-08-25 23:00"  # Nepal standard time, hourly
SENSOR_HEIGHT_MM = (
    1500.0  # arbitrary datum: stage = (1500 - lidar distance) / 1000, metres
)


def main(src: str) -> None:
    raw = pd.read_csv(src, parse_dates=["date"]).set_index("date")["dist_smoothed"]
    stage = ((SENSOR_HEIGHT_MM - raw) / 1000.0).loc[
        "2024-03-01":"2025-10-31"
    ]  # clean part
    daily = stage.resample("D").mean()
    season = (
        daily.groupby(daily.index.dayofyear).mean().reindex(range(1, 367)).interpolate()
    )
    season = (
        pd.concat([season.iloc[-30:], season, season.iloc[:30]])
        .rolling(15, center=True)
        .mean()
        .iloc[30:-30]
    )
    hourly_real = stage.resample("h").mean()
    diurnal = (
        (hourly_real - hourly_real.rolling(24, center=True).mean())
        .groupby(hourly_real.index.hour)
        .mean()
    )
    resid = (daily - daily.rolling(15, center=True).mean()).dropna()
    sigma = float(resid.std())

    rng = np.random.default_rng(450)
    idx = pd.date_range(START, END, freq="h")
    base = season.loc[idx.dayofyear].to_numpy()
    ar = np.zeros(len(idx))
    for i in range(1, len(idx)):  # slow AR(1) wander, hourly
        ar[i] = 0.995 * ar[i - 1] + rng.normal(0, sigma * 0.1)
    events = np.zeros(len(idx))  # monsoon flood pulses, Jun-Sep
    for t in rng.choice(
        np.flatnonzero((idx.month >= 6) & (idx.month <= 9)), 14, replace=False
    ):
        amp, tau = rng.uniform(0.1, 0.45), rng.uniform(10, 40)
        k = np.arange(len(idx) - t)
        events[t:] += amp * np.exp(-k / tau) * (1 - np.exp(-k / 3))
    level = np.clip(
        base
        + ar
        + events
        + diurnal.reindex(idx.hour).to_numpy()
        + rng.normal(0, 0.004, len(idx)),
        0.0,
        None,
    )
    out = pd.DataFrame(
        {
            "timestamp_npt": idx.strftime("%Y-%m-%dT%H:00"),
            "water_level_m": level.round(2),
        }
    )
    out.to_csv(OUT, index=False)
    sys.stdout.write(f"{len(out)} rows written to {OUT}\n")


if __name__ == "__main__":
    main(sys.argv[1])
