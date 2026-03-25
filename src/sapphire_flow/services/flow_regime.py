from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import structlog

from sapphire_flow.types.skill import FlowRegimeConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import Observation

log = structlog.get_logger()


def compute_flow_regime(
    observations: list[Observation],
    station_id: StationId,
    parameter: str,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
    version: int = 1,
    min_observations: int = 365,
) -> FlowRegimeConfig | None:
    values = np.array(
        [
            obs.value
            for obs in observations
            if obs.station_id == station_id
            and obs.parameter == parameter
            and obs.value is not None
        ],
        dtype=float,
    )

    if len(values) < min_observations:
        log.warning(
            "insufficient_observations_for_flow_regime",
            station_id=str(station_id),
            parameter=parameter,
            count=len(values),
            min_required=min_observations,
        )
        return None

    now = clock()
    return FlowRegimeConfig(
        id=uuid_factory(),
        station_id=station_id,
        parameter=parameter,
        p50=float(np.percentile(values, 50)),
        p90=float(np.percentile(values, 90)),
        computed_at=now,
        observation_count=len(values),
        version=version,
        created_at=now,
    )
