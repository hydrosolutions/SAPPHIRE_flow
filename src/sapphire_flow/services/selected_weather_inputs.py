from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import polars as pl

from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.operational_inputs import build_future_dynamic_frame
from sapphire_flow.services.operational_weather_projection import (
    project_future_weather,
    project_past_weather,
    weather_use_periods,
)
from sapphire_flow.services.operational_weather_proof import verify_future_receipt
from sapphire_flow.services.selected_weather_evidence import (
    require_common_weather_cycle,
)
from sapphire_flow.services.weather_gap_fill import (
    fill_daily_weather,
    stored_weather_donors,
    weather_fill_parameters,
)
from sapphire_flow.types.enums import EnsembleMode, FutureWeatherPolicy, NwpCycleSource
from sapphire_flow.types.forecast import ForecastProvenance
from sapphire_flow.types.operational_weather import (
    FutureWeatherUse,
    PastWeatherUse,
    WeatherBlocked,
    WeatherCoverage,
    WeatherUnused,
    WeatherUse,
    period_weather_value,
)
from sapphire_flow.types.operational_weather_v2 import (
    FutureWeatherRequestV2,
    FutureWeatherUseV2,
    PastWeatherRequirementV2,
    PastWeatherUseV2,
    WeatherCacheAttemptV2,
    WeatherCacheEntryV2,
    WeatherUseV2,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.config.weather_gap import WeatherGapConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel, GroupForecastModel
    from sapphire_flow.protocols.stores import WeatherForecastStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.operational_weather import (
        FutureWeatherRequest,
        PastWeatherRequirement,
        WeatherCacheAttempt,
        WeatherCacheEntry,
    )
    from sapphire_flow.types.time_grid import TimeGrid
    from sapphire_flow.types.weather import WeatherForecastRecord
    from sapphire_flow.types.weather_gap import WeatherGapFill
    from sapphire_flow.types.weather_gap_v2 import WeatherGapFillV2


@dataclass(frozen=True, kw_only=True, slots=True)
class AssignmentWeatherInputs:
    past_dynamic: pl.DataFrame
    future_dynamic: pl.DataFrame
    use: WeatherUse | WeatherUseV2
    future_records: tuple[WeatherForecastRecord, ...]


def assemble_selected_weather(
    *,
    station_id: StationId,
    issue_time: UtcDatetime,
    used_at: UtcDatetime,
    entry: WeatherCacheEntry | WeatherCacheEntryV2 | None,
    expected_attempt: WeatherCacheAttempt | WeatherCacheAttemptV2 | None,
    past_requirement: PastWeatherRequirement | PastWeatherRequirementV2 | None,
    future_requirement: FutureWeatherRequest | FutureWeatherRequestV2 | None,
    store: WeatherForecastStore,
    earliest_cycle: UtcDatetime,
    max_past_bytes: int,
    ensemble_mode: EnsembleMode,
    model: ForecastModel | GroupForecastModel,
    model_id: ModelId,
    gap_config: WeatherGapConfig,
    max_retained_past_lead: timedelta = timedelta(hours=72),
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
    grid = (
        expected_attempt.grid
        if isinstance(expected_attempt, WeatherCacheAttemptV2)
        else None
    )
    ceiling = (
        expected_attempt.eligibility_cutoff
        if isinstance(expected_attempt, WeatherCacheAttemptV2)
        else issue_time
    )
    past = project_past_weather(
        entry,
        expected_attempt=expected_attempt,
        requirement=past_requirement,
        max_past_bytes=max_past_bytes,
        coverage=WeatherCoverage.ALLOW_GAPS,
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
    if isinstance(past, (PastWeatherUse, PastWeatherUseV2)):
        if isinstance(past, PastWeatherUse):
            from sapphire_flow.services.retained_operational_weather import (
                past_leads_allowed,
            )

            if not past_leads_allowed(past.days, max_lead=max_retained_past_lead):
                raise ConfigurationError(
                    "operational past weather exceeds original IFS lead limit"
                )
        periods = weather_use_periods(past)
        gap_parameters = weather_fill_parameters(
            model, model_id=model_id, config=gap_config, time_step=timedelta(days=1)
        )
        policy = gap_config.for_model(str(model_id))
        past_lookbacks = dict(model.data_requirements.declared_lookbacks)
        past_dynamic = pl.DataFrame(
            [
                {
                    "timestamp": day.start,
                    **{
                        parameter: period_weather_value(day, parameter)
                        for parameter in past.requirement.parameters
                    },
                }
                for day in periods
            ]
        )
        donors: Mapping[tuple[str, UtcDatetime], WeatherGapFill | WeatherGapFillV2] = {}
        if gap_parameters and entry is not None and future_requirement is not None:
            missing_days = {
                day.start
                for day in periods
                if any(
                    (period_weather_value(day, parameter)) is None
                    for parameter in gap_parameters
                )
            }
            donors = qualified_stored_weather_donors(
                station_id=station_id,
                entry=entry,
                future_requirement=future_requirement,
                store=store,
                ceiling=ceiling,
                missing_days=missing_days,
                gap_parameters=gap_parameters,
                grid=grid,
                past_end=past.requirement.end if grid is not None else None,
                max_past_lead=max_retained_past_lead if grid is None else None,
            )
        prepared = fill_daily_weather(
            past_dynamic,
            issue_time=ceiling,
            grid=grid,
            past_end=past.requirement.end if grid is not None else None,
            lookbacks={
                parameter: past_lookbacks.get(parameter, len(periods))
                for parameter in gap_parameters
            },
            max_gap_days=policy.max_gap_days,
            donors=donors,
            precipitation_lower_bounds={
                day.start: sum(
                    row.value for row in day.samples if row.parameter == "precipitation"
                )
                for day in periods
            },
        )
        past_dynamic = prepared.frame
        past = (
            replace(
                past, gap_fills=cast("tuple[WeatherGapFillV2, ...]", prepared.fills)
            )
            if isinstance(past, PastWeatherUseV2)
            else replace(
                past, gap_fills=cast("tuple[WeatherGapFill, ...]", prepared.fills)
            )
        )
        if not isinstance(model, ForecastInterfaceAdapter) and any(
            past_dynamic.get_column(parameter).null_count()
            for parameter in past.requirement.parameters
        ):
            raise ConfigurationError(
                "required operational past weather is blocked: missing_support"
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
            grid=grid,
            first_period_start=future_use.requirement.first_period_start
            if isinstance(future_use, FutureWeatherUseV2)
            else None,
            eligibility_cutoff=ceiling if grid is not None else None,
        )
        if future_dynamic.height != max(horizons.values()):
            raise ConfigurationError(
                "verified operational weather cannot form the required daily frame"
            )
    if isinstance(expected_attempt, WeatherCacheAttemptV2):
        assert isinstance(past, (PastWeatherUseV2, WeatherUnused))
        assert isinstance(future_use, (FutureWeatherUseV2, WeatherUnused))
        use = WeatherUseV2(
            grid=expected_attempt.grid,
            eligibility_cutoff=expected_attempt.eligibility_cutoff,
            station_id=station_id,
            issue_time=issue_time,
            used_at=used_at,
            attempt=expected_attempt,
            refresh_finished_at=entry.finished_at if entry is not None else None,
            past=past,
            future=future_use,
        )
    else:
        assert isinstance(past, (PastWeatherUse, WeatherUnused))
        assert isinstance(future_use, (FutureWeatherUse, WeatherUnused))
        use = WeatherUse(
            station_id=station_id,
            issue_time=issue_time,
            used_at=used_at,
            attempt=expected_attempt,
            refresh_finished_at=entry.finished_at if entry is not None else None,
            past=past,
            future=future_use,
        )
    return AssignmentWeatherInputs(
        past_dynamic=past_dynamic,
        future_dynamic=future_dynamic,
        use=use,
        future_records=records,
    )


def selected_group_weather_provenance(
    inputs: Mapping[StationId, AssignmentWeatherInputs],
    *,
    issue_time: UtcDatetime,
) -> ForecastProvenance:
    cycle = require_common_weather_cycle(item.use for item in inputs.values())
    return ForecastProvenance(
        nwp_cycle_reference_time=cycle,
        nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY
        if cycle is None
        else NwpCycleSource.PRIMARY
        if cycle == issue_time
        else NwpCycleSource.FALLBACK,
    )


def qualified_stored_weather_donors(
    *,
    station_id: StationId,
    entry: WeatherCacheEntry | WeatherCacheEntryV2,
    future_requirement: FutureWeatherRequest | FutureWeatherRequestV2,
    store: WeatherForecastStore,
    ceiling: UtcDatetime,
    missing_days: set[UtcDatetime],
    gap_parameters: frozenset[str],
    grid: TimeGrid | None = None,
    past_end: UtcDatetime | None = None,
    max_past_lead: timedelta | None = None,
) -> Mapping[tuple[str, UtcDatetime], WeatherGapFill | WeatherGapFillV2]:
    qualified_records: dict[object, WeatherForecastRecord] = {}
    for receipt in entry.future_receipts:
        if (
            receipt.scope.station_id != station_id
            or receipt.scope.source != future_requirement.source
            or receipt.scope.binding_fingerprint
            != future_requirement.binding_fingerprint
            or receipt.cycle_time > ceiling
            or isinstance(receipt.scope.request, FutureWeatherRequestV2)
            != (grid is not None)
            or (
                isinstance(receipt.scope.request, FutureWeatherRequestV2)
                and receipt.scope.request.grid != grid
            )
            or not any(
                (grid.floor(stamp) if grid is not None else stamp.replace(hour=0))
                in missing_days
                for req in receipt.scope.requirements
                for stamp in req.valid_times
            )
        ):
            continue
        donor_rows = store.fetch_weather_forecasts(
            station_id=station_id,
            nwp_source=receipt.scope.source,
            cycle_time=receipt.cycle_time,
            parameters=sorted({req.parameter for req in receipt.scope.requirements}),
        )
        verify_future_receipt(receipt, donor_rows)
        scope_keys = {
            (req.parameter, stamp, member)
            for req in receipt.scope.requirements
            for stamp in req.valid_times
            for member in req.member_ids
        }
        qualified_records.update(
            {
                row.id: row
                for row in donor_rows
                if (row.parameter, row.valid_time, row.member_id) in scope_keys
                and (
                    max_past_lead is None
                    or row.valid_time
                    + timedelta(
                        hours=3
                        if row.valid_time - row.cycle_time < timedelta(hours=144)
                        else 6
                    )
                    - row.cycle_time
                    <= max_past_lead
                )
            }
        )
    return stored_weather_donors(
        tuple(qualified_records.values()),
        station_id=station_id,
        issue_time=ceiling,
        parameters=gap_parameters,
        grid=grid,
        past_end=past_end,
    )
