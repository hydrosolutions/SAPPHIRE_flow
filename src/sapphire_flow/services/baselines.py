from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from sapphire_flow.types.domain import ClimBaseline

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import Observation

_EPSILON = 1e-6


def _doy_distance(a: int, b: int) -> int:
    diff = abs(a - b)
    return min(diff, 366 - diff)


def compute_clim_baselines(
    observations: list[Observation],
    station_id: StationId,
    parameter: str,
    window_half_width: int = 15,
    min_samples: int = 10,
) -> list[ClimBaseline]:
    pairs = [
        (obs.timestamp.timetuple().tm_yday, obs.value)
        for obs in observations
        if obs.station_id == station_id
        and obs.parameter == parameter
        and obs.value is not None
    ]

    if not pairs:
        return []

    doys = np.array([p[0] for p in pairs], dtype=np.int32)
    values = np.array([p[1] for p in pairs], dtype=np.float64)

    results: list[ClimBaseline] = []

    for target_doy in range(1, 367):
        distances = np.minimum(
            np.abs(doys - target_doy), 366 - np.abs(doys - target_doy)
        )
        mask = distances <= window_half_width
        window_values = values[mask]

        if len(window_values) < min_samples:
            continue

        mean = float(np.mean(window_values))
        std = float(np.std(window_values, ddof=0))
        if std == 0.0:
            std = _EPSILON

        results.append(
            ClimBaseline(
                station_id=station_id,
                parameter=parameter,
                day_of_year=target_doy,
                rolling_mean=mean,
                rolling_std=std,
                sample_count=len(window_values),
            )
        )

    return results
