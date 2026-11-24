from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.services.recap_daily_weather import (
    recap_complete_daily_records,
    recap_complete_period_records,
)
from sapphire_flow.services.run_group_forecast import discover_group_runs
from sapphire_flow.services.track_projection import resolve_tracks
from sapphire_flow.services.track_resolution import AcceptedCandidate, resolve_candidate
from sapphire_flow.types.enums import EnsembleMode, FutureWeatherPolicy, NwpCycleSource
from sapphire_flow.types.forcing_track import (
    FutureSteps,
    RawFetchOutcome,
    RawFetchStatus,
)
from sapphire_flow.types.operational_weather_v2 import FutureWeatherRequestV2

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

    from sapphire_flow.flows.operational_weather_prepare import PreparedWeatherRefresh
    from sapphire_flow.protocols.adapters import CandidateAwareForecastSource
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import StationGroupStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forcing_track import (
        ForcingResolutionPolicy,
        ForcingTrackKey,
        ResolvedTrackRequest,
    )
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.operational_weather import FutureWeatherRequest
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.weather import WeatherForecastRecord

# SINGLE control: measured Nepal candidate <=21*4*116 = 9,744 rows.
MAX_NEGOTIATED_RECORDS = 65_536
log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class UnavailableCandidate:
    pass


CandidateHandoff = AcceptedCandidate | UnavailableCandidate


@dataclass(frozen=True, kw_only=True, slots=True)
class NegotiatedWeather:
    prepared: PreparedWeatherRefresh
    candidates: dict[ForcingTrackKey, CandidateHandoff]
    failure: Exception | None = None


def common_prefix(
    records: Mapping[StationId, list[WeatherForecastRecord]],
    *,
    track: ResolvedTrackRequest,
    issue_time: UtcDatetime,
    context: FutureWeatherRequestV2 | None = None,
) -> int:
    expected = {item.assignment[0] for item in track.assignments}
    if set(records) != expected:
        return 0
    ceiling = next(iter(track.fetch_horizons.values())).value
    counts: list[int] = []
    for rows in records.values():
        for feature in track.key.features:
            selected = [row for row in rows if row.parameter == feature]
            complete = (
                recap_complete_daily_records(
                    selected,
                    issue_time=issue_time,
                    feature_horizons={str(feature): ceiling},
                    ensemble_mode=track.key.ensemble_mode,
                )
                if context is None
                else recap_complete_period_records(
                    selected,
                    first_period_start=context.first_period_start,
                    cycle_ceiling=context.eligibility_cutoff,
                    grid=context.grid,
                    feature_horizons={str(feature): ceiling},
                    ensemble_mode=track.key.ensemble_mode,
                )
            )
            days = {
                row.valid_time.date()
                if context is None
                else context.grid.floor(row.valid_time)
                for row in complete
            }
            count = len(days)
            start = issue_time if context is None else context.first_period_start
            # A partial last day is a short tail; support on a later day is a hole.
            if count < ceiling and any(
                start + timedelta(days=count + 1)
                <= row.valid_time
                < start + timedelta(days=ceiling)
                for row in selected
            ):
                return 0
            counts.append(count)
    return min(counts, default=0)


def negotiate_weather_horizons(
    *,
    prepared: PreparedWeatherRefresh,
    models: dict[ModelId, ForecastModel],
    group_store: StationGroupStore | None,
    forecast_bindings: Mapping[StationId, StationWeatherSource],
    adapter: CandidateAwareForecastSource,
    policy: ForcingResolutionPolicy,
    issue_time: UtcDatetime,
    earliest_cycle: UtcDatetime,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    fetch_candidate: Callable[
        [ForcingTrackKey, list[StationWeatherSource], UtcDatetime], RawFetchOutcome
    ],
    eligible_group_pairs: frozenset[tuple[StationId, ModelId]] | None = None,
) -> NegotiatedWeather:
    if group_store is None:
        return NegotiatedWeather(prepared=prepared, candidates={})
    groups = discover_group_runs(dict(models), group_store)
    group_pairs = {(sid, mid) for group, mid in groups for sid in group.station_ids}
    replacements: dict[
        FutureWeatherRequest | FutureWeatherRequestV2,
        FutureWeatherRequest | FutureWeatherRequestV2,
    ] = {}
    candidates: dict[ForcingTrackKey, CandidateHandoff] = {}
    lengths: dict[ForcingTrackKey, int] = {}
    retained_count = 0
    try:
        for track in resolve_tracks(list(prepared.projections)):
            context = None
            if prepared.grid is not None:
                requests = [
                    prepared.assignments[item.assignment].future
                    for item in track.assignments
                ]
                if any(
                    not isinstance(request, FutureWeatherRequestV2)
                    or request.grid != prepared.grid
                    or request.first_period_start != prepared.grid.floor(issue_time)
                    or request.issue_time != issue_time
                    or request.eligibility_cutoff != issue_time
                    for request in requests
                ):
                    raise ValueError(
                        "negotiation period context differs from selected model grid"
                    )
                [context, *_] = requests
                assert isinstance(context, FutureWeatherRequestV2)
            if (
                track.key.time_step != timedelta(days=1)
                or track.key.ensemble_mode is not EnsembleMode.SINGLE
                or adapter.expected_member_ids(track.key) != frozenset({0})
                or len({h.value for h in track.fetch_horizons.values()}) != 1
                or any(item.assignment not in group_pairs for item in track.assignments)
                or (
                    eligible_group_pairs is not None
                    and any(
                        item.assignment not in eligible_group_pairs
                        for item in track.assignments
                    )
                )
            ):
                continue
            ceiling = next(iter(track.fetch_horizons.values())).value
            floors: list[int] = []
            for item in track.assignments:
                model = models[item.assignment[1]]
                if not isinstance(model, ForecastInterfaceAdapter):
                    break
                minima = model.future_feature_minima(track.key.time_step)
                maxima = model.future_feature_horizons(track.key.time_step)
                if frozenset(minima) != track.key.features or any(
                    maxima[name].value != ceiling or minimum.value >= ceiling
                    for name, minimum in minima.items()
                ):
                    break
                floors.extend(value.value for value in minima.values())
            else:
                floor = max(floors)
                station_ids = {item.assignment[0] for item in track.assignments}
                assignment_pairs = {item.assignment for item in track.assignments}
                # Eligibility comes from selection, never available weather or bindings.
                if any(
                    not {
                        (sid, mid)
                        for sid in group.station_ids
                        if eligible_group_pairs is None
                        or (sid, mid) in eligible_group_pairs
                    }
                    <= assignment_pairs
                    for group, mid in groups
                    if any(item.assignment[1] == mid for item in track.assignments)
                ):
                    continue
                sources = [
                    forecast_bindings[sid] for sid in sorted(station_ids, key=str)
                ]
                remaining = MAX_NEGOTIATED_RECORDS - retained_count

                def covered(
                    rows: dict[StationId, list[WeatherForecastRecord]],
                    *,
                    request: ResolvedTrackRequest = track,
                    budget: int = remaining,
                    minimum: int = floor,
                    period_context: FutureWeatherRequestV2 | None = context,
                ) -> bool:
                    return (
                        sum(len(values) for values in rows.values()) <= budget
                        and common_prefix(
                            rows,
                            track=request,
                            issue_time=issue_time,
                            context=period_context,
                        )
                        >= minimum
                    )

                accepted = resolve_candidate(
                    replace(
                        track,
                        fetch_horizons={
                            name: FutureSteps(value=floor)
                            for name in track.key.features
                        },
                    ),
                    fetch_candidate=partial(
                        _bounded_fetch,
                        key=track.key,
                        bindings=sources,
                        budget=remaining,
                        fetch_candidate=fetch_candidate,
                    ),
                    expected_member_ids=frozenset({0}),
                    nominal_cycle_source=lambda cycle, nominal: (
                        NwpCycleSource.PRIMARY
                        if cycle == nominal
                        else NwpCycleSource.FALLBACK
                    ),
                    nominal_now=issue_time,
                    issue_time=issue_time,
                    cycle_cadence_hours=policy.cycle_cadence_hours,
                    max_cycle_age_hours=policy.max_cycle_age_hours,
                    clock=clock,
                    id_gen=id_gen,
                    weather_policy=FutureWeatherPolicy.RECAP_COMPLETE_DAILY,
                    earliest_cycle=earliest_cycle,
                    candidate_records_check=covered,
                    grid=None if context is None else context.grid,
                    first_period_start=None
                    if context is None
                    else context.first_period_start,
                    eligibility_cutoff=None
                    if context is None
                    else context.eligibility_cutoff,
                )
                if accepted is None:
                    candidates[track.key] = UnavailableCandidate()
                    continue
                count = common_prefix(
                    accepted.complete_station_records,
                    track=track,
                    issue_time=issue_time,
                    context=context,
                )
                assert floor <= count <= ceiling
                retained_count += sum(
                    len(rows) for rows in accepted.complete_station_records.values()
                )
                candidates[track.key] = accepted
                lengths[track.key] = count
                for item in track.assignments:
                    request = prepared.assignments[item.assignment].future
                    assert request is not None
                    replacements[request] = replace(
                        request,
                        requirements=tuple(
                            replace(req, valid_times=req.valid_times[:count])
                            for req in request.requirements
                        ),
                    )
        updated = replace(
            prepared,
            assignments={
                key: replace(
                    value,
                    future=replacements.get(value.future, value.future)
                    if value.future is not None
                    else None,
                )
                for key, value in prepared.assignments.items()
            },
            specs=tuple(
                replace(
                    spec,
                    future_requests=tuple(
                        replacements.get(req, req) for req in spec.future_requests
                    ),
                )
                for spec in prepared.specs
            ),
            projections=tuple(
                replace(
                    item,
                    assignment_horizons={
                        name: FutureSteps(value=lengths[item.key])
                        for name in item.key.features
                    },
                )
                if item.key in lengths
                else item
                for item in prepared.projections
            ),
        )
        if updated.resolver != prepared.resolver:
            raise ValueError("negotiation changed the frozen weather resolver")
        if {track.key for track in resolve_tracks(list(updated.projections))} != {
            track.key for track in resolve_tracks(list(prepared.projections))
        }:
            raise ValueError("negotiation changed forcing track identities")
        return NegotiatedWeather(prepared=updated, candidates=candidates)
    except Exception as exc:
        # Begin/fail/finalize ordinary attempts before propagating a fatal probe error.
        return NegotiatedWeather(prepared=prepared, candidates={}, failure=exc)


def _bounded_fetch(
    cycle: UtcDatetime,
    *,
    key: ForcingTrackKey,
    bindings: list[StationWeatherSource],
    budget: int,
    fetch_candidate: Callable[
        [ForcingTrackKey, list[StationWeatherSource], UtcDatetime], RawFetchOutcome
    ],
) -> RawFetchOutcome:
    outcome = fetch_candidate(key, bindings, cycle)
    if sum(value.values.height for value in outcome.stations.values()) > budget:
        log.warning(
            "selected_weather.negotiation_record_budget",
            record_budget=budget,
            track_features=sorted(key.features),
        )
        return RawFetchOutcome(
            status=RawFetchStatus.ABSENT_AT_CYCLE,
            cycle=cycle,
            stations={},
            absent_detail="negotiated_record_budget",
        )
    return outcome
