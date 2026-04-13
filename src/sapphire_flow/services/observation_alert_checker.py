from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from sapphire_flow.types.alert import Alert
from sapphire_flow.types.enums import AlertSource, AlertStatus, QcStatus
from sapphire_flow.types.ids import AlertId

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import (
        AlertStore,
        ObservationStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId

log = structlog.get_logger()

_OBS_LOOKBACK = timedelta(hours=24)


def check_observation_alerts(
    station_params: set[tuple[StationId, str]],
    obs_store: ObservationStore,
    station_store: StationStore,
    alert_store: AlertStore,
    now: UtcDatetime,
) -> None:
    by_station: dict[StationId, set[str]] = {}
    for station_id, parameter in station_params:
        by_station.setdefault(station_id, set()).add(parameter)

    for station_id, parameters in by_station.items():
        thresholds = station_store.fetch_thresholds(station_id)
        if not thresholds:
            continue

        level_parameters: dict[str, set[str]] = {}
        for t in thresholds:
            level_parameters.setdefault(t.danger_level, set()).add(t.parameter)

        exceeded_levels: dict[str, float] = {}
        evaluated_parameters: set[str] = set()

        for parameter in parameters:
            obs_list = obs_store.fetch_observations(
                station_id,
                parameter,
                start=now - _OBS_LOOKBACK,
                end=now,
                qc_status=QcStatus.QC_PASSED,
            )
            if not obs_list:
                continue

            latest = max(obs_list, key=lambda o: o.timestamp)
            if latest.value is None:
                continue

            evaluated_parameters.add(parameter)
            latest_value: float = latest.value

            for t in thresholds:
                if t.parameter == parameter and latest_value > t.value:
                    exceeded_levels[t.danger_level] = latest_value

        for level, trigger_value in exceeded_levels.items():
            alert_store.upsert_alert(
                Alert(
                    id=AlertId(uuid4()),
                    station_id=station_id,
                    source=AlertSource.OBSERVATION,
                    alert_level=level,
                    status=AlertStatus.RAISED,
                    trigger_probability=None,
                    trigger_value=trigger_value,
                    triggered_at=now,
                    acknowledged_at=None,
                    acknowledged_by=None,
                    resolved_at=None,
                    first_detected_at=now,
                    notified_at=None,
                    created_at=now,
                    model_ids=(),
                    alert_model_strategy=None,
                )
            )

        active = alert_store.fetch_active_alerts(
            station_id=station_id, source=AlertSource.OBSERVATION
        )
        for alert in active:
            if alert.alert_level in exceeded_levels:
                continue
            configured = level_parameters.get(alert.alert_level, set())
            if configured and not configured.issubset(evaluated_parameters):
                log.debug(
                    "observation_alert.resolution_deferred",
                    station_id=str(station_id),
                    alert_level=alert.alert_level,
                    missing=sorted(configured - evaluated_parameters),
                )
                continue
            alert_store.resolve_alert(alert.id)

        log.debug(
            "observation_alert.station_checked",
            station_id=str(station_id),
            evaluated_parameters=sorted(evaluated_parameters),
            exceeded_levels=list(exceeded_levels.keys()),
        )

    log.info(
        "observation_alert.completed",
        stations_checked=len(by_station),
    )
