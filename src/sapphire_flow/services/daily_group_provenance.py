from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.types.daily_input_provenance import DailyInputProvenance

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.services.operational_inputs import OperationalInputMetadata
    from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import NwpCycleSource
    from sapphire_flow.types.ids import StationId


def capture_daily_group_provenance(
    *,
    result: GroupForecastOutcome,
    metadata_by_station: Mapping[StationId, OperationalInputMetadata],
    nwp_cycle_source: NwpCycleSource,
    nwp_cycle_reference_time: UtcDatetime | None,
) -> dict[StationId, DailyInputProvenance]:
    captured: dict[StationId, DailyInputProvenance] = {}
    for station_id, metadata in metadata_by_station.items():
        station_result = result.results.get(station_id)
        forecast = (
            station_result.forecasts[0]
            if station_result is not None and station_result.forecasts
            else None
        )
        captured[station_id] = DailyInputProvenance(
            nwp_source=nwp_cycle_source,
            nwp_reference_time=nwp_cycle_reference_time,
            nwp_age_hours=(
                metadata.nwp_age_hours if nwp_cycle_reference_time is not None else None
            ),
            observation_staleness_hours=metadata.observation_staleness_hours,
            input_quality=forecast.input_quality if forecast is not None else None,
            input_quality_flags=forecast.input_quality_flags
            if forecast is not None
            else (),
        )
    return captured
