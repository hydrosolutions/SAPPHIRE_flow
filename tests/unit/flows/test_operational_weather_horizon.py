from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl
import pytest

from sapphire_flow.flows.operational_weather_horizon import common_prefix
from sapphire_flow.services.operational_weather_proof import WeatherProofError
from sapphire_flow.services.track_resolution import resolve_candidate
from sapphire_flow.types.enums import (
    EnsembleMode,
    NwpCycleSource,
    SpatialRepresentation,
)
from sapphire_flow.types.forcing_track import (
    AssignmentKey,
    FeatureName,
    ForcingRequired,
    ForcingTrackKey,
    FutureSteps,
    RawFetchOutcome,
    RawFetchStatus,
    ResolvedTrackRequest,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.weather import BasinAverageForecast
from tests.fakes.operational_weather import ISSUE
from tests.unit.services.test_recap_daily_weather import qualified_rows

if TYPE_CHECKING:
    from sapphire_flow.flows.operational_weather_horizon import NegotiatedWeather
    from sapphire_flow.protocols.adapters import CandidateAwareForecastSource
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.station import StationWeatherSource


def request() -> ResolvedTrackRequest:
    features = frozenset({FeatureName("precipitation"), FeatureName("temperature")})
    horizons = {name: FutureSteps(value=15) for name in features}
    key = ForcingTrackKey(
        nwp_source="ifs_ecmwf",
        ensemble_mode=EnsembleMode.SINGLE,
        time_step=timedelta(days=1),
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=features,
    )
    return ResolvedTrackRequest(
        key=key,
        fetch_horizons=horizons,
        assignments=tuple(
            ForcingRequired(
                key=key,
                assignment_horizons=horizons,
                assignment=AssignmentKey((StationId(UUID(int=i)), ModelId("cmal"))),
            )
            for i in (1, 2)
        ),
    )


@pytest.mark.parametrize("days", [1, 11, 15])
def test_common_prefix_uses_shortest_group_member(days: int) -> None:
    track = request()
    rows = {
        item.assignment[0]: [
            replace(row, station_id=item.assignment[0])
            for row in qualified_rows()
            if row.valid_time < ISSUE + timedelta(days=days if i else 15)
        ]
        for i, item in enumerate(track.assignments)
    }
    assert common_prefix(rows, track=track, issue_time=ISSUE) == days


@pytest.mark.parametrize("missing_day", [0, 4])
def test_prefix_refuses_leading_or_interior_holes(missing_day: int) -> None:
    track = request()
    rows = {
        item.assignment[0]: [
            replace(row, station_id=item.assignment[0])
            for row in qualified_rows()
            if not ISSUE + timedelta(days=missing_day)
            <= row.valid_time
            < ISSUE + timedelta(days=missing_day + 1)
        ]
        for item in track.assignments
    }
    assert common_prefix(rows, track=track, issue_time=ISSUE) == 0


def test_prefix_never_drops_missing_group_member() -> None:
    track = request()
    sid = track.assignments[0].assignment[0]
    assert common_prefix({sid: qualified_rows()}, track=track, issue_time=ISSUE) == 0


def test_prefix_rejects_invalid_daily_support() -> None:
    track = request()
    rows = {item.assignment[0]: qualified_rows() for item in track.assignments}
    sid = track.assignments[0].assignment[0]
    rows[sid][0] = replace(rows[sid][0], value=float("nan"))
    with pytest.raises(WeatherProofError, match="invalid required samples"):
        common_prefix(rows, track=track, issue_time=ISSUE)


def test_candidate_predicate_walks_back_without_refetch() -> None:
    track = replace(
        request(),
        fetch_horizons={
            FeatureName(name): FutureSteps(value=1)
            for name in ("precipitation", "temperature")
        },
    )
    sid = StationId(UUID(int=1))
    older = ISSUE - timedelta(days=1)
    calls: list[UtcDatetime] = []

    def fetch(cycle: UtcDatetime) -> RawFetchOutcome:
        calls.append(cycle)
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED,
            cycle=cycle,
            stations={
                sid: BasinAverageForecast(
                    nwp_source="ifs_ecmwf",
                    cycle_time=cycle,
                    values=pl.DataFrame(
                        [
                            {
                                "valid_time": row.valid_time,
                                "parameter": row.parameter,
                                "member_id": row.member_id,
                                "value": row.value,
                            }
                            for row in qualified_rows(cycle)
                        ]
                    ),
                )
            },
        )

    accepted = resolve_candidate(
        track,
        fetch_candidate=fetch,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=lambda c, n: (
            NwpCycleSource.PRIMARY if c == n else NwpCycleSource.FALLBACK
        ),
        nominal_now=ISSUE,
        issue_time=ISSUE,
        cycle_cadence_hours=24,
        max_cycle_age_hours=24,
        clock=lambda: ISSUE,
        id_gen=uuid4,
        candidate_records_check=lambda rows: rows[sid][0].cycle_time == older,
    )
    assert accepted is not None and accepted.resolved_cycle == older
    assert calls == [ISSUE, older]


def negotiate_case(
    *,
    days: int = 11,
    floor: int = 1,
    exact: bool = False,
    station_scope: bool = False,
    partial_group: bool = False,
    ensemble: bool = False,
    unequal: bool = False,
    failure: Exception | None = None,
    eligible_group_pairs: frozenset[tuple[StationId, ModelId]] | None = None,
    extra_model_stations: frozenset[StationId] | None = None,
) -> tuple[NegotiatedWeather, list[UtcDatetime]]:
    from typing import cast

    from forecast_interface.input.variable import HorizonSemantics

    from sapphire_flow.adapters import forecast_interface as fi
    from sapphire_flow.flows.operational_weather_horizon import (
        negotiate_weather_horizons,
    )
    from sapphire_flow.flows.operational_weather_prepare import (
        AssignmentWeatherRequirement,
        FrozenWeatherResolver,
        PreparedWeatherRefresh,
    )
    from sapphire_flow.types.enums import ModelAssignmentStatus
    from sapphire_flow.types.forcing_track import ForcingResolutionPolicy
    from sapphire_flow.types.ids import StationGroupId
    from sapphire_flow.types.operational_weather import (
        FutureWeatherRequest,
        FutureWeatherRequirement,
    )
    from sapphire_flow.types.station import GroupModelAssignment, StationGroup
    from tests.fakes.fake_stores import FakeStationGroupStore
    from tests.unit.adapters.test_forecast_interface_adapter_nan_gate import (
        RecordingFIForecastModel,
    )
    from tests.unit.adapters.test_recap_operational import make_binding

    track = request()
    mid = ModelId("cmal")
    gid = StationGroupId(uuid4())
    step = timedelta(days=1)
    requirement = fi.InputRequirement(
        targets={
            "discharge": fi.TargetSpec(
                unit=fi.Unit.M3_PER_S,
                representations=frozenset({fi.OutputRepresentation.DETERMINISTIC}),
            )
        },
        dynamic={
            step: fi.SpatialInputSpec(
                data={
                    fi.FISpatialRepresentation.BASIN_AVERAGE: fi.DynamicInputSpec(
                        future_known={
                            "nwp": {
                                name: fi.FutureKnownVariable(
                                    future_steps=14
                                    if unequal and name == "temperature"
                                    else 15,
                                    max_nan=0,
                                    unit=fi.Unit.MM
                                    if name == "precipitation"
                                    else fi.Unit.DEG_C,
                                    horizon_semantics=HorizonSemantics.EXACT
                                    if exact
                                    else HorizonSemantics.AT_MOST,
                                    min_future_steps=None if exact else floor,
                                )
                                for name in ("precipitation", "temperature")
                            }
                        }
                    )
                }
            )
        },
    )
    model = fi.ForecastInterfaceAdapter(
        RecordingFIForecastModel(
            fi.ModelFailure(
                model_name="fixture",
                issue_datetime=ISSUE,
                cause=fi.FailureCause.INPUT_DATA,
                message="unused",
            ),
            artifact_scope=fi.FIArtifactScope.STATION
            if station_scope
            else fi.FIArtifactScope.GROUP,
            requirement=requirement,
        )
    )
    groups = FakeStationGroupStore()
    members = frozenset(item.assignment[0] for item in track.assignments)
    groups.store_group(
        StationGroup(id=gid, name="fixture", station_ids=members, created_at=ISSUE)
    )
    groups.store_group_model_assignment(
        GroupModelAssignment(
            group_id=gid,
            model_id=mid,
            time_step=step,
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=ISSUE,
        )
    )
    models = {mid: model}
    if extra_model_stations is not None:
        extra_mid = ModelId("other")
        models[extra_mid] = model
        groups.store_group_model_assignment(
            GroupModelAssignment(
                group_id=gid,
                model_id=extra_mid,
                time_step=step,
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=ISSUE,
            )
        )
        track = replace(
            track,
            assignments=track.assignments
            + tuple(
                replace(item, assignment=AssignmentKey((item.assignment[0], extra_mid)))
                for item in track.assignments
                if item.assignment[0] in extra_model_stations
            ),
        )
    assignments = {
        (item.assignment[0], item.assignment[1]): AssignmentWeatherRequirement(
            past=None,
            ensemble_mode=EnsembleMode.SINGLE,
            future=FutureWeatherRequest(
                station_id=item.assignment[0],
                source="ifs_ecmwf",
                binding_fingerprint="b" * 64,
                issue_time=ISSUE,
                requirements=tuple(
                    FutureWeatherRequirement(
                        parameter=str(name),
                        member_ids=(0,),
                        valid_times=tuple(ISSUE + step * i for i in range(15)),
                    )
                    for name in sorted(track.key.features)
                ),
            ),
        )
        for item in track.assignments
    }
    prepared = PreparedWeatherRefresh(
        specs=(),
        assignments=assignments,
        resolver=FrozenWeatherResolver(bindings=()),
        projections=track.assignments[:1] if partial_group else track.assignments,
    )
    bindings = {
        sid: replace(make_binding(), station_id=sid, nwp_source="ifs_ecmwf")
        for sid in members
    }
    calls: list[UtcDatetime] = []

    class Source:
        def expected_member_ids(self, key: ForcingTrackKey) -> frozenset[int]:
            return frozenset({0, 1}) if ensemble else frozenset({0})

    def fetch(
        key: ForcingTrackKey, sources: list[StationWeatherSource], cycle: UtcDatetime
    ) -> RawFetchOutcome:
        calls.append(cycle)
        if failure is not None:
            raise failure
        values = pl.DataFrame(
            [
                {
                    "valid_time": row.valid_time,
                    "parameter": row.parameter,
                    "member_id": row.member_id,
                    "value": row.value,
                }
                for row in qualified_rows(cycle)
                if row.valid_time < ISSUE + step * days
            ]
        )
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED,
            cycle=cycle,
            stations={
                sid: BasinAverageForecast(
                    nwp_source="ifs_ecmwf", cycle_time=cycle, values=values
                )
                for sid in (source.station_id for source in sources)
            },
        )

    result = negotiate_weather_horizons(
        prepared=prepared,
        models=models,
        group_store=groups,
        forecast_bindings=bindings,
        adapter=cast("CandidateAwareForecastSource", Source()),
        policy=ForcingResolutionPolicy(
            cycle_cadence_hours=24, max_cycle_age_hours=24, max_retries=0
        ),
        issue_time=ISSUE,
        earliest_cycle=ISSUE,
        clock=lambda: ISSUE,
        id_gen=uuid4,
        fetch_candidate=fetch,
        eligible_group_pairs=eligible_group_pairs,
    )
    assert result.prepared.resolver == prepared.resolver
    assert tuple(item.key for item in result.prepared.projections) == tuple(
        item.key for item in prepared.projections
    )
    if not result.candidates:
        assert result.prepared == prepared
    return result, calls


@pytest.mark.parametrize(
    "option", ["exact", "station_scope", "partial_group", "ensemble", "unequal"]
)
def test_negotiation_preserves_noneligible_routes(option: str) -> None:
    result, calls = negotiate_case(
        exact=option == "exact",
        station_scope=option == "station_scope",
        partial_group=option == "partial_group",
        ensemble=option == "ensemble",
        unequal=option == "unequal",
    )
    assert result.failure is None
    assert result.candidates == {}
    assert calls == []


@pytest.mark.parametrize(
    "days,floor,available", [(11, 1, True), (3, 3, True), (2, 3, False)]
)
def test_negotiation_applies_floor_and_exact_request(
    days: int, floor: int, available: bool
) -> None:
    from sapphire_flow.flows.operational_weather_horizon import UnavailableCandidate

    result, calls = negotiate_case(days=days, floor=floor)
    assert result.failure is None
    assert calls == [ISSUE]
    handoff = next(iter(result.candidates.values()))
    assert isinstance(handoff, UnavailableCandidate) is not available
    for value in result.prepared.assignments.values():
        assert value.future is not None
        assert {len(req.valid_times) for req in value.future.requirements} == {
            days if available else 15
        }


def test_negotiation_budget_refusal_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sapphire_flow.flows import operational_weather_horizon as horizon

    monkeypatch.setattr(horizon, "MAX_NEGOTIATED_RECORDS", 1)
    result, calls = negotiate_case()
    assert result.failure is None
    assert isinstance(
        next(iter(result.candidates.values())), horizon.UnavailableCandidate
    )
    assert calls == [ISSUE]


def test_negotiation_fatal_is_deferred_for_attempt_recording() -> None:
    from sapphire_flow.adapters.recap_gateway import RecapAuthError

    failure = RecapAuthError("synthetic authorization failure", status_code=401)
    result, calls = negotiate_case(failure=failure)
    assert result.failure is failure
    assert result.candidates == {}
    assert calls == [ISSUE]


def test_explicit_eligible_subset_negotiates_complete_prefix() -> None:
    first = request().assignments[0].assignment
    result, calls = negotiate_case(
        partial_group=True, eligible_group_pairs=frozenset({first})
    )
    assert result.failure is None
    assert calls == [ISSUE]
    future = result.prepared.assignments[first].future
    assert future is not None
    assert {len(req.valid_times) for req in future.requirements} == {11}
    assert {
        h.value for h in result.prepared.projections[0].assignment_horizons.values()
    } == {11}


@pytest.mark.parametrize("selection", ["missing_member", "outside_selection", "empty"])
def test_incomplete_or_unselected_pairs_keep_strict_request(selection: str) -> None:
    pairs = [item.assignment for item in request().assignments]
    eligible = {"missing_member": pairs, "outside_selection": pairs[1:], "empty": []}[
        selection
    ]
    result, calls = negotiate_case(
        partial_group=True, eligible_group_pairs=frozenset(eligible)
    )
    assert result.failure is None
    assert result.candidates == {}
    assert calls == []


@pytest.mark.parametrize("include_missing_pair", [False, True])
def test_overlapping_models_require_their_own_eligible_members(
    include_missing_pair: bool,
) -> None:
    pairs = {item.assignment for item in request().assignments}
    first, second = (StationId(UUID(int=i)) for i in (1, 2))
    pairs.add((first, ModelId("other")))
    if include_missing_pair:
        pairs.add((second, ModelId("other")))
    result, calls = negotiate_case(
        eligible_group_pairs=frozenset(pairs), extra_model_stations=frozenset({first})
    )
    assert result.failure is None
    assert calls == ([] if include_missing_pair else [ISSUE])
    assert bool(result.candidates) is not include_missing_pair
