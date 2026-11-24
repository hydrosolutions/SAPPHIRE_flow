from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.adapters.recap_gateway import RecapGatewayForecastAdapter
from sapphire_flow.adapters.recap_operational import (
    future_binding_fingerprint,
    future_binding_fingerprint_v2,
    operational_binding_fingerprint,
    operational_binding_fingerprint_v2,
)
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.operational_weather_refresh import StationWeatherRefresh
from sapphire_flow.services.retained_operational_weather import RetainedPastRequirement
from sapphire_flow.services.run_group_forecast import discover_group_runs
from sapphire_flow.services.weather_gap_fill import weather_fill_parameters
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    ModelAssignmentStatus,
    SpatialRepresentation,
)
from sapphire_flow.types.forcing_track import (
    AssignmentKey,
    FeatureName,
    ForcingRequired,
    ForcingTrackKey,
    FutureSteps,
)
from sapphire_flow.types.operational_weather import (
    LEGACY_WEATHER_PARAMETERS,
    WEATHER_PARAMETERS,
    FutureWeatherRequest,
    FutureWeatherRequirement,
    PastWeatherRequirement,
    WeatherCacheKey,
    require_midnight,
)
from sapphire_flow.types.operational_weather_v2 import (
    FutureWeatherRequestV2,
    PastWeatherRequirementV2,
)
from sapphire_flow.types.recap_intervals import (
    RADIATION_PARAMETERS,
    UNQUALIFIED_INTERVALS,
    GatewayIntervalContract,
)
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.adapters.recap_gateway import (
        GatewayPolygonRef,
        GatewayPolygonResolver,
    )
    from sapphire_flow.config.weather_gap import WeatherGapConfig
    from sapphire_flow.protocols.adapters import CandidateAwareForecastSource
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import StationGroupStore, StationStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.operational_weather import WeatherParameter
    from sapphire_flow.types.station import ModelAssignment, StationWeatherSource


@dataclass(frozen=True, kw_only=True, slots=True)
class AssignmentWeatherRequirement:
    past: PastWeatherRequirement | PastWeatherRequirementV2 | None
    future: FutureWeatherRequest | FutureWeatherRequestV2 | None
    ensemble_mode: EnsembleMode


@dataclass(frozen=True, kw_only=True, slots=True)
class FrozenWeatherResolver:
    bindings: tuple[tuple[StationWeatherSource, GatewayPolygonRef], ...]

    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None:
        return next((ref for binding, ref in self.bindings if binding == source), None)


@dataclass(frozen=True, kw_only=True, slots=True)
class PreparedWeatherRefresh:
    specs: tuple[StationWeatherRefresh, ...]
    assignments: Mapping[tuple[StationId, ModelId], AssignmentWeatherRequirement]
    resolver: FrozenWeatherResolver
    projections: tuple[ForcingRequired, ...]
    refusal_messages: tuple[str, ...] = ()
    grid: TimeGrid | None = None


def prepare_weather_refresh(
    *,
    active_model_assignments: Mapping[StationId, list[ModelAssignment]],
    models: dict[ModelId, ForecastModel],
    forecast_bindings: Mapping[StationId, StationWeatherSource],
    station_store: StationStore,
    group_store: StationGroupStore | None,
    resolver: GatewayPolygonResolver,
    adapter: CandidateAwareForecastSource | None,
    issue_time: UtcDatetime,
    eligible_group_pairs: frozenset[tuple[StationId, ModelId]] | None = None,
    gap_config: WeatherGapConfig | None = None,
) -> PreparedWeatherRefresh:
    from types import MappingProxyType

    scheduled = {
        (sid, assignment.model_id): assignment.time_step
        for sid, assignments in active_model_assignments.items()
        for assignment in assignments
        if eligible_group_pairs is None
        and assignment.model_id in models
        and sid in forecast_bindings
    }
    if group_store is not None:
        for group, mid in discover_group_runs(dict(models), group_store):
            active = sorted(
                (
                    assignment
                    for assignment in group_store.fetch_group_model_assignments(
                        group.id
                    )
                    if assignment.model_id == mid
                    and assignment.status is ModelAssignmentStatus.ACTIVE
                ),
                key=lambda item: item.priority,
            )
            if active:
                scheduled.update(
                    {
                        (sid, mid): active[0].time_step
                        for sid in group.station_ids
                        if sid in forecast_bindings
                        and (
                            eligible_group_pairs is None
                            or (sid, mid) in eligible_group_pairs
                        )
                    }
                )
    declarations = {models[mid].data_requirements.time_grid for _, mid in scheduled}
    grid = None
    if declarations and declarations != {None}:
        if len(declarations) != 1 or None in declarations:
            raise ConfigurationError(
                "selected weather assignments have different time grids"
            )
        declaration = next(iter(declarations))
        assert declaration is not None
        if (
            eligible_group_pairs is None
            or declaration.label is not PeriodLabel.START
            or declaration.grid.step != timedelta(days=1)
        ):
            raise ConfigurationError(
                "declared weather grid requires explicitly selected daily START groups"
            )
        grid = declaration.grid
    if grid is None:
        require_midnight(issue_time)
    period_start = issue_time if grid is None else grid.floor(issue_time)
    mappings: dict[StationWeatherSource, GatewayPolygonRef] = {}
    projections: list[ForcingRequired] = []
    weather_parameters = WEATHER_PARAMETERS
    qualification = (
        adapter.interval_qualification
        if isinstance(adapter, RecapGatewayForecastAdapter)
        else UNQUALIFIED_INTERVALS
    )
    refusals: list[str] = []
    assignments: dict[tuple[StationId, ModelId], AssignmentWeatherRequirement] = {}
    specs: dict[StationId, StationWeatherRefresh] = {}
    for (sid, mid), step in scheduled.items():
        model = models[mid]
        requirements = model.data_requirements
        if (
            step != timedelta(days=1)
            or requirements.spatial_input_type
            is not SpatialRepresentation.BASIN_AVERAGE
        ):
            raise ConfigurationError(
                "selected operational weather requires daily basin-average assignments"
            )
        names = (
            requirements.past_dynamic_features | requirements.future_dynamic_features
        )
        if (
            RADIATION_PARAMETERS.intersection(names)
            and qualification.contract is GatewayIntervalContract.UNQUALIFIED
        ):
            refusals.append(
                f"Required operational weather unavailable for {sid}/{mid}: "
                "radiation intervals are unqualified"
            )
            continue
        if not names <= set(weather_parameters):
            raise ConfigurationError(
                "selected operational weather supports only "
                "qualified weather parameters"
            )
        lookbacks = dict(requirements.declared_lookbacks)
        past = (
            None
            if not requirements.past_dynamic_features
            else _past_requirement(
                grid=grid,
                start=ensure_utc(
                    period_start
                    - step
                    * max(
                        lookbacks.get(name, requirements.lookback_steps)
                        for name in requirements.past_dynamic_features
                    )
                ),
                end=period_start,
                parameters=tuple(
                    name
                    for name in weather_parameters
                    if name in requirements.past_dynamic_features
                ),
            )
        )
        horizons = (
            {
                str(name): value.value
                for name, value in model.future_feature_horizons(step).items()
            }
            if isinstance(model, ForecastInterfaceAdapter)
            else {
                name: requirements.forecast_horizon_steps
                for name in requirements.future_dynamic_features
            }
        )
        mode = requirements.ensemble_mode
        if isinstance(model, ForecastInterfaceAdapter) and horizons:
            mode = next(iter(model.future_feature_modes(step).values()))
        future = None
        if horizons:
            if adapter is None:
                raise ConfigurationError(
                    "selected future weather requires a candidate-aware source"
                )
            source = forecast_bindings[sid]
            ref = resolver.resolve(source)
            if ref is None or source.nwp_source != "ifs_ecmwf":
                raise ConfigurationError(
                    "selected future weather requires a mapped Recap forecast binding"
                )
            mappings[source] = ref
            key = ForcingTrackKey(
                nwp_source=source.nwp_source,
                ensemble_mode=mode,
                time_step=step,
                spatial_representation=source.extraction_type,
                features=frozenset(FeatureName(name) for name in horizons),
            )
            projections.append(
                ForcingRequired(
                    key=key,
                    assignment=AssignmentKey((sid, mid)),
                    assignment_horizons={
                        FeatureName(name): FutureSteps(value=count)
                        for name, count in horizons.items()
                    },
                )
            )
            members = tuple(sorted(adapter.expected_member_ids(key)))
            fingerprint = future_binding_fingerprint(
                source,
                ref,
                members,
                parameters=tuple(
                    name for name in weather_parameters if name in horizons
                ),
                interval_qualification=qualification,
            )
            if grid is not None:
                fingerprint = future_binding_fingerprint_v2(
                    source,
                    ref,
                    members,
                    grid,
                    parameters=tuple(
                        name for name in weather_parameters if name in horizons
                    ),
                    interval_qualification=qualification,
                )
            future_requirements = tuple(
                FutureWeatherRequirement(
                    parameter=name,
                    member_ids=members,
                    valid_times=tuple(
                        ensure_utc(period_start + step * day) for day in range(count)
                    ),
                )
                for name, count in sorted(horizons.items())
            )
            future = (
                FutureWeatherRequest(
                    station_id=sid,
                    source=source.nwp_source,
                    binding_fingerprint=fingerprint,
                    issue_time=issue_time,
                    requirements=future_requirements,
                )
                if grid is None
                else FutureWeatherRequestV2(
                    station_id=sid,
                    source=source.nwp_source,
                    binding_fingerprint=fingerprint,
                    issue_time=issue_time,
                    requirements=future_requirements,
                    grid=grid,
                    first_period_start=period_start,
                    eligibility_cutoff=issue_time,
                )
            )
        assignments[(sid, mid)] = AssignmentWeatherRequirement(
            past=past, future=future, ensemble_mode=mode
        )
        if past is None and future is None:
            continue
        source = station_store.fetch_operational_binding(sid)
        ref = resolver.resolve(source)
        if ref is None:
            raise ConfigurationError(
                "selected past weather requires a mapped operational binding"
            )
        mappings[source] = ref
        prior = specs.get(sid)
        pasts = [
            item
            for item in (past, None if prior is None else prior.past_requirement)
            if item is not None
        ]
        combined_past = (
            None
            if not pasts
            else _past_requirement(
                grid=grid,
                start=min(item.start for item in pasts),
                end=period_start,
                parameters=tuple(
                    sorted({name for item in pasts for name in item.parameters})
                ),
            )
        )
        requests = () if prior is None else prior.future_requests
        if future is not None and future not in requests:
            requests += (future,)
        past_parameters = (
            combined_past.parameters
            if combined_past is not None
            else LEGACY_WEATHER_PARAMETERS
        )
        past_fingerprint = (
            operational_binding_fingerprint(
                source,
                ref,
                parameters=past_parameters,
                interval_qualification=qualification,
            )
            if grid is None
            else operational_binding_fingerprint_v2(
                source,
                ref,
                grid,
                parameters=past_parameters,
                interval_qualification=qualification,
            )
        )
        specs[sid] = StationWeatherRefresh(
            key=WeatherCacheKey(
                station_id=sid,
                binding_fingerprint=past_fingerprint,
            ),
            source=source,
            past_requirement=combined_past,
            future_requests=requests,
            retained_past_requirements=(
                () if prior is None else prior.retained_past_requirements
            )
            + (
                ()
                if not isinstance(past, PastWeatherRequirement)
                or future is None
                or gap_config is None
                else (
                    RetainedPastRequirement(
                        past=past,
                        fill_parameters=weather_fill_parameters(
                            model, model_id=mid, config=gap_config, time_step=step
                        ),
                        max_gap_days=gap_config.for_model(str(mid)).max_gap_days,
                    ),
                )
            ),
            retained_future_bindings=(
                frozenset() if prior is None else prior.retained_future_bindings
            )
            | (
                frozenset({future.binding_fingerprint})
                if grid is None
                and past is not None
                and future is not None
                and all(req.member_ids == (0,) for req in future.requirements)
                and future.binding_fingerprint
                == future_binding_fingerprint(
                    forecast_bindings[sid],
                    ref,
                    (0,),
                    parameters=tuple(
                        name for name in weather_parameters if name in horizons
                    ),
                    interval_qualification=qualification,
                )
                else frozenset()
            ),
        )
    return PreparedWeatherRefresh(
        specs=tuple(
            spec
            if spec.future_requests
            and all(
                request.binding_fingerprint in spec.retained_future_bindings
                for request in spec.future_requests
            )
            else replace(
                spec,
                retained_past_requirements=(),
                retained_future_bindings=frozenset(),
            )
            for spec in specs.values()
        ),
        assignments=MappingProxyType(assignments),
        resolver=FrozenWeatherResolver(bindings=tuple(mappings.items())),
        projections=tuple(projections),
        refusal_messages=tuple(refusals),
        grid=grid,
    )


def _past_requirement(
    *,
    grid: TimeGrid | None,
    start: UtcDatetime,
    end: UtcDatetime,
    parameters: tuple[WeatherParameter, ...],
) -> PastWeatherRequirement | PastWeatherRequirementV2:
    if grid is None:
        return PastWeatherRequirement(start=start, end=end, parameters=parameters)
    return PastWeatherRequirementV2(
        grid=grid, start=start, end=end, parameters=parameters
    )
