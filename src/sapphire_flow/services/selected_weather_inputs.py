from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.operational_inputs import build_future_dynamic_frame
from sapphire_flow.services.operational_weather_projection import (
    project_future_weather,
    project_past_weather,
)
from sapphire_flow.types.enums import EnsembleMode, FutureWeatherPolicy, NwpCycleSource
from sapphire_flow.types.forecast import ForecastProvenance
from sapphire_flow.types.operational_weather import (
    FutureWeatherUse,
    PastWeatherUse,
    WeatherBlocked,
    WeatherUnused,
    WeatherUse,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.protocols.stores import WeatherForecastStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.operational_weather import (
        FutureWeatherRequest,
        PastWeatherRequirement,
        WeatherCacheAttempt,
        WeatherCacheEntry,
    )
    from sapphire_flow.types.weather import WeatherForecastRecord


@dataclass(frozen=True, kw_only=True, slots=True)
class AssignmentWeatherInputs:
    past_dynamic: pl.DataFrame
    future_dynamic: pl.DataFrame
    use: WeatherUse
    future_records: tuple[WeatherForecastRecord, ...]


def assemble_selected_weather(
    *,
    station_id: StationId,
    issue_time: UtcDatetime,
    used_at: UtcDatetime,
    entry: WeatherCacheEntry | None,
    expected_attempt: WeatherCacheAttempt | None,
    past_requirement: PastWeatherRequirement | None,
    future_requirement: FutureWeatherRequest | None,
    store: WeatherForecastStore,
    earliest_cycle: UtcDatetime,
    max_past_bytes: int,
    ensemble_mode: EnsembleMode,
) -> AssignmentWeatherInputs:
    if expected_attempt is None:
        if past_requirement is not None or future_requirement is not None:
            raise ConfigurationError("required operational weather was not refreshed")
        return AssignmentWeatherInputs(
            past_dynamic=pl.DataFrame(),
            future_dynamic=pl.DataFrame(),
            use=WeatherUse(
                station_id=station_id,
                issue_time=issue_time,
                used_at=used_at,
                attempt=None,
                refresh_finished_at=None,
                past=WeatherUnused(),
                future=WeatherUnused(),
            ),
            future_records=(),
        )
    past = project_past_weather(
        entry,
        expected_attempt=expected_attempt,
        requirement=past_requirement,
        max_past_bytes=max_past_bytes,
    )
    future = project_future_weather(
        entry,
        expected_attempt=expected_attempt,
        requirement=future_requirement,
        store=store,
        earliest_cycle=earliest_cycle,
    )
    if isinstance(past, WeatherBlocked):
        raise ConfigurationError(
            f"required operational past weather is blocked: {past.reason.value}"
        )
    if isinstance(future, WeatherBlocked):
        raise ConfigurationError(
            "selected-route full requested future window is unavailable: "
            f"{future.reason.value}"
        )
    past_dynamic = pl.DataFrame()
    if isinstance(past, PastWeatherUse):
        past_dynamic = pl.DataFrame(
            [
                {
                    "timestamp": day.day,
                    **{
                        parameter: day.precipitation_mm
                        if parameter == "precipitation"
                        else day.temperature_c
                        for parameter in past.requirement.parameters
                    },
                }
                for day in past.days
            ]
        )
    if isinstance(future, WeatherUnused):
        future_use = future
        records: tuple[WeatherForecastRecord, ...] = ()
        future_dynamic = pl.DataFrame()
    else:
        future_use, records = future
        horizons = {
            item.parameter: len(item.valid_times)
            for item in future_use.requirement.requirements
        }
        future_dynamic = build_future_dynamic_frame(
            list(records),
            time_step=timedelta(days=1),
            issue_time=issue_time,
            forecast_horizon_steps=max(horizons.values()),
            future_dynamic_features=frozenset(horizons),
            ensemble_mode=ensemble_mode,
            feature_horizons=horizons,
            weather_policy=FutureWeatherPolicy.RECAP_COMPLETE_DAILY,
        )
        if future_dynamic.height != max(horizons.values()):
            raise ConfigurationError(
                "verified operational weather cannot form the required daily frame"
            )
    return AssignmentWeatherInputs(
        past_dynamic=past_dynamic,
        future_dynamic=future_dynamic,
        use=WeatherUse(
            station_id=station_id,
            issue_time=issue_time,
            used_at=used_at,
            attempt=expected_attempt,
            refresh_finished_at=entry.finished_at if entry is not None else None,
            past=past,
            future=future_use,
        ),
        future_records=records,
    )


def selected_group_weather_provenance(
    inputs: Mapping[StationId, AssignmentWeatherInputs],
    *,
    issue_time: UtcDatetime,
) -> ForecastProvenance:
    cycles = {
        item.use.future.receipt.cycle_time
        for item in inputs.values()
        if isinstance(item.use.future, FutureWeatherUse)
    }
    if len(cycles) > 1:
        raise ConfigurationError("selected group weather cannot mix source cycles")
    cycle = next(iter(cycles), None)
    return ForecastProvenance(
        nwp_cycle_reference_time=cycle,
        nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY
        if cycle is None
        else NwpCycleSource.PRIMARY
        if cycle == issue_time
        else NwpCycleSource.FALLBACK,
    )
