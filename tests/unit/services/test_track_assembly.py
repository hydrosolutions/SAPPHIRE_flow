"""Plan 151 T6 red-first: per-assignment operational-input assembly (D9)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from structlog.testing import capture_logs

from sapphire_flow.services.track_assembly import (
    ForcingContract,
    ReadyContext,
    UnavailableTrackContext,
    assemble_assignment_inputs,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    ForcingRoute,
    NwpCycleSource,
    SpatialRepresentation,
)
from sapphire_flow.types.forcing_track import (
    AssignmentKey,
    FeatureName,
    ForcingRequired,
    ForcingTrackKey,
    FutureSteps,
    NoForcingRequired,
    StationTrackAvailable,
    StationTrackUnavailable,
    StationUnavailableReason,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.conftest import make_observation, make_observations, make_station_config
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeObservationStore,
    FakeStationStore,
)

_STATION = StationId(uuid4())
_MODEL = ModelId("track_assembly_test_model")
_STEP = timedelta(hours=24)
_ISSUE = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
_NOW = ensure_utc(datetime(2026, 1, 10, 1, tzinfo=UTC))


def _clock() -> object:
    return _NOW


class _FakeModel:
    artifact_scope = None

    def __init__(self, requirements: ModelDataRequirements) -> None:
        self.data_requirements = requirements

    def train(self, *args: object, **kwargs: object) -> bytes:
        return b""

    def predict(self, *args: object, **kwargs: object) -> tuple[dict, None]:
        return ({}, None)

    def serialize_artifact(self, artifact: object) -> bytes:
        return b""

    def deserialize_artifact(self, raw: bytes) -> bytes:
        return raw


def _requirements(
    *, future_dynamic_features: frozenset[str] = frozenset({"precip", "temp"})
) -> ModelDataRequirements:
    return ModelDataRequirements(
        target_parameters=frozenset(),
        past_dynamic_features=frozenset(),
        future_dynamic_features=future_dynamic_features,
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=1,
        forecast_horizon_steps=10,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.SINGLE,
    )


def _stores() -> tuple:
    station_store = FakeStationStore()
    basin_store = FakeBasinStore()
    obs_store = FakeObservationStore()
    reanalysis = FakeWeatherReanalysisSource()
    station_store.store_station(make_station_config(station_id=_STATION))
    return obs_store, station_store, basin_store, reanalysis


def _record(parameter: str, day: int, *, value: float = 1.0) -> WeatherForecastRecord:
    valid_time = ensure_utc(_ISSUE + timedelta(days=day))
    return WeatherForecastRecord(
        id=uuid4(),
        station_id=_STATION,
        nwp_source="ecmwf_ifs",
        cycle_time=_ISSUE,
        valid_time=valid_time,
        parameter=parameter,
        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
        band_id=None,
        member_id=None,
        value=value,
        created_at=_NOW,
    )


def test_assembles_frame_at_assignment_own_max_horizon_not_model_scalar() -> None:
    """Fails today (no `assemble_assignment_inputs` at all — Plan 151 T6 is
    net-new): the per-assignment frame must be capped to THIS assignment's
    OWN per-feature horizon max (2 here), never the model's declared scalar
    `forecast_horizon_steps` (10, from `_requirements()` above) and never a
    sibling assignment's larger horizon."""
    obs_store, station_store, basin_store, reanalysis = _stores()
    model = _FakeModel(_requirements(future_dynamic_features=frozenset({"precip"})))
    horizons = {FeatureName("precip"): FutureSteps(value=2)}
    key = ForcingTrackKey(
        nwp_source="ecmwf_ifs",
        ensemble_mode=EnsembleMode.SINGLE,
        time_step=_STEP,
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset(horizons),
    )
    projection = ForcingRequired(
        key=key,
        assignment_horizons=horizons,
        assignment=AssignmentKey((_STATION, _MODEL)),
    )
    records = [_record("precip", d) for d in range(1, 11)]  # 10 real days available
    outcome = StationTrackAvailable(
        cycle=_ISSUE, records=records, provenance=NwpCycleSource.PRIMARY
    )

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=model,  # type: ignore[arg-type]
        projection=projection,
        track_outcome=outcome,
        issue_time=_ISSUE,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    # Plan 151 D10: per-track assembly stamps the explicit discriminant the
    # FI boundary reads to decide whether D9's per-variable `future_steps`
    # slice applies.
    assert result.inputs.forcing_route is ForcingRoute.PER_TRACK
    frame = result.inputs.data.future_dynamic
    assert frame.height == 2, "must cap to the assignment's own 2-step horizon"
    assert result.contract == ForcingContract(
        feature_horizons=horizons,
        ensemble_mode=EnsembleMode.SINGLE,
        future_dynamic_features=frozenset({FeatureName("precip")}),
    )


def test_per_feature_horizon_caps_each_column_independently() -> None:
    """Review fold-in (major): precip declares a 2-step horizon and temp a
    10-step horizon on the SAME assignment/track. With 10 real raw days
    available for BOTH, the assembled frame must cap precip to 2 non-null
    values and temp to 10 -- never one scalar (group-max) cap applied
    uniformly to every feature. Fails today: `forecast_horizon_steps =
    max(h.value for h in horizons.values())` (10) is the ONLY cap
    `build_future_dynamic_frame` receives, so precip also keeps 10."""
    obs_store, station_store, basin_store, reanalysis = _stores()
    model = _FakeModel(
        _requirements(future_dynamic_features=frozenset({"precip", "temp"}))
    )
    horizons = {
        FeatureName("precip"): FutureSteps(value=2),
        FeatureName("temp"): FutureSteps(value=10),
    }
    key = ForcingTrackKey(
        nwp_source="ecmwf_ifs",
        ensemble_mode=EnsembleMode.SINGLE,
        time_step=_STEP,
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset(horizons),
    )
    projection = ForcingRequired(
        key=key,
        assignment_horizons=horizons,
        assignment=AssignmentKey((_STATION, _MODEL)),
    )
    records = [_record("precip", d) for d in range(1, 11)] + [
        _record("temp", d) for d in range(1, 11)
    ]
    outcome = StationTrackAvailable(
        cycle=_ISSUE, records=records, provenance=NwpCycleSource.PRIMARY
    )

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=model,  # type: ignore[arg-type]
        projection=projection,
        track_outcome=outcome,
        issue_time=_ISSUE,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    frame = result.inputs.data.future_dynamic
    assert frame.height == 10
    assert frame["precip"].drop_nulls().len() == 2
    assert frame["temp"].drop_nulls().len() == 10


def test_expected_member_ids_thread_onto_contract_for_ensemble_only() -> None:
    """Review fold-in (blocker): the source-derived `expected_member_ids`
    the caller resolved this track against must land on the
    `ForcingContract` for an ENSEMBLE assignment (so the runner's defensive
    re-check can validate against ground truth), and stay `None` for a
    non-ENSEMBLE (SINGLE) assignment, which has no member axis at all."""
    obs_store, station_store, basin_store, reanalysis = _stores()
    reqs = ModelDataRequirements(
        target_parameters=frozenset(),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset({"precip"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=1,
        forecast_horizon_steps=2,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.ENSEMBLE,
    )
    model = _FakeModel(reqs)
    horizons = {FeatureName("precip"): FutureSteps(value=2)}
    key = ForcingTrackKey(
        nwp_source="ecmwf_ifs",
        ensemble_mode=EnsembleMode.ENSEMBLE,
        time_step=_STEP,
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset(horizons),
    )
    projection = ForcingRequired(
        key=key,
        assignment_horizons=horizons,
        assignment=AssignmentKey((_STATION, _MODEL)),
    )
    records = [
        WeatherForecastRecord(
            id=uuid4(),
            station_id=_STATION,
            nwp_source="ecmwf_ifs",
            cycle_time=_ISSUE,
            valid_time=ensure_utc(_ISSUE + timedelta(days=d)),
            parameter="precip",
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=member_id,
            value=1.0,
            created_at=_NOW,
        )
        for d in range(1, 3)
        for member_id in (0, 1)
    ]
    outcome = StationTrackAvailable(
        cycle=_ISSUE, records=records, provenance=NwpCycleSource.PRIMARY
    )

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=model,  # type: ignore[arg-type]
        projection=projection,
        track_outcome=outcome,
        issue_time=_ISSUE,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
        expected_member_ids=frozenset({0, 1}),
    )

    assert isinstance(result, ReadyContext)
    assert result.contract is not None
    assert result.contract.expected_member_ids == frozenset({0, 1})


def test_nwp_age_hours_from_this_assignments_own_resolved_cycle() -> None:
    """A stale FALLBACK cycle for one assignment must not be reported as
    fresh via some OTHER shared cycle — nwp_age_hours is per-assignment."""
    obs_store, station_store, basin_store, reanalysis = _stores()
    model = _FakeModel(_requirements(future_dynamic_features=frozenset({"precip"})))
    horizons = {FeatureName("precip"): FutureSteps(value=2)}
    key = ForcingTrackKey(
        nwp_source="ecmwf_ifs",
        ensemble_mode=EnsembleMode.SINGLE,
        time_step=_STEP,
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset(horizons),
    )
    projection = ForcingRequired(
        key=key,
        assignment_horizons=horizons,
        assignment=AssignmentKey((_STATION, _MODEL)),
    )
    stale_cycle = ensure_utc(_ISSUE - timedelta(hours=30))
    outcome = StationTrackAvailable(
        cycle=stale_cycle,
        records=[_record("precip", d) for d in range(1, 3)],
        provenance=NwpCycleSource.FALLBACK,
    )

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=model,  # type: ignore[arg-type]
        projection=projection,
        track_outcome=outcome,
        issue_time=_ISSUE,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    assert result.nwp_age_hours is not None
    assert result.nwp_age_hours > 24.0
    assert result.provenance.nwp_cycle_source is NwpCycleSource.FALLBACK
    assert result.provenance.nwp_cycle_reference_time == stale_cycle


def test_no_forcing_required_assignment_gets_null_provenance_and_no_contract() -> None:
    obs_store, station_store, basin_store, reanalysis = _stores()
    model = _FakeModel(_requirements(future_dynamic_features=frozenset()))
    projection = NoForcingRequired(assignment=AssignmentKey((_STATION, _MODEL)))

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=model,  # type: ignore[arg-type]
        projection=projection,
        track_outcome=None,
        issue_time=_ISSUE,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    assert result.contract is None
    assert result.provenance.nwp_cycle_source is NwpCycleSource.RUNOFF_ONLY
    assert result.provenance.nwp_cycle_reference_time is None
    assert result.nwp_age_hours is None


def test_unavailable_track_outcome_short_circuits_without_assembling() -> None:
    """D10: an unavailable station at an otherwise-resolved cycle must yield
    `UnavailableTrackContext`, not attempt assembly at all."""
    obs_store, station_store, basin_store, reanalysis = _stores()
    model = _FakeModel(_requirements())
    horizons = {
        FeatureName("precip"): FutureSteps(value=2),
        FeatureName("temp"): FutureSteps(value=10),
    }
    key = ForcingTrackKey(
        nwp_source="ecmwf_ifs",
        ensemble_mode=EnsembleMode.SINGLE,
        time_step=_STEP,
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset(horizons),
    )
    projection = ForcingRequired(
        key=key,
        assignment_horizons=horizons,
        assignment=AssignmentKey((_STATION, _MODEL)),
    )
    outcome = StationTrackUnavailable(
        reason=StationUnavailableReason.INCOMPLETE_AT_CYCLE
    )

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=model,  # type: ignore[arg-type]
        projection=projection,
        track_outcome=outcome,
        issue_time=_ISSUE,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert result == UnavailableTrackContext(
        assignment=AssignmentKey((_STATION, _MODEL)),
        reason=StationUnavailableReason.INCOMPLETE_AT_CYCLE,
    )


def test_isolated_missing_daily_bucket_yields_incomplete_at_cycle_not_raise() -> None:
    """Plan 228 review fixer round (major): a real BAFU/SwissMetNet lookback
    window can legitimately contain an isolated missing bucket (a
    sensor/comms gap) — a KNOWN per-station data-availability condition,
    not a bug. ``assemble_assignment_inputs`` must classify it as
    ``UnavailableTrackContext(reason=INCOMPLETE_AT_CYCLE)`` directly
    (fallback-advance, `forecast.fallback_advanced` at the runner), never
    let the raised `ConfigurationError` fall through to the caller's
    generic `_contained_assemble` catch-all, which would mislabel it
    `ASSEMBLY_FAILED` (a genuine programming bug)."""
    obs_store, station_store, basin_store, reanalysis = _stores()
    requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=10,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.SINGLE,
    )
    model = _FakeModel(requirements)
    projection = NoForcingRequired(assignment=AssignmentKey((_STATION, _MODEL)))

    data_start = ensure_utc(_ISSUE - timedelta(days=10))
    days = [1, 2, 3, 5, 6, 7, 8, 9, 10]  # day 4 missing — isolated gap
    observations = [
        make_observation(
            station_id=_STATION,
            parameter="discharge",
            timestamp=ensure_utc(data_start + timedelta(days=d - 1)),
            rng=random.Random(d),
        )
        for d in days
    ]
    obs_store.store_observations(observations)

    with capture_logs() as logs:
        result = assemble_assignment_inputs(
            station_id=_STATION,
            model_id=_MODEL,
            model=model,  # type: ignore[arg-type]
            projection=projection,
            track_outcome=None,
            issue_time=_ISSUE,
            obs_store=obs_store,  # type: ignore[arg-type]
            station_store=station_store,  # type: ignore[arg-type]
            basin_store=basin_store,  # type: ignore[arg-type]
            forcing_source=reanalysis,  # type: ignore[arg-type]
            clock=_clock,  # type: ignore[arg-type]
        )

    assert result == UnavailableTrackContext(
        assignment=AssignmentKey((_STATION, _MODEL)),
        reason=StationUnavailableReason.INCOMPLETE_AT_CYCLE,
    )
    skip_events = [
        e for e in logs if e.get("event") == "track_assembly.cadence_mismatch_skip"
    ]
    assert skip_events


def test_partial_trailing_day_excluded_at_a_non_midnight_cycle() -> None:
    """Plan 228 D4 (Non-goals RETRACTED — this was live, not a non-goal): at
    a non-midnight cycle, the most recent ``past_targets`` bucket must never
    be a partial day presented as a full one."""
    obs_store, station_store, basin_store, reanalysis = _stores()
    requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=7,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.SINGLE,
    )
    model = _FakeModel(requirements)
    projection = NoForcingRequired(assignment=AssignmentKey((_STATION, _MODEL)))

    issue_time = ensure_utc(_ISSUE + timedelta(hours=6))  # never a midnight boundary
    day_midnight = _ISSUE

    data_start = ensure_utc(issue_time - timedelta(days=9))
    background = make_observations(
        n=9 * 24 * 6,
        station_id=_STATION,
        parameter="discharge",
        start=data_start,
        interval=timedelta(minutes=10),
    )
    partial_day = [
        make_observation(
            station_id=_STATION,
            parameter="discharge",
            timestamp=ensure_utc(day_midnight + timedelta(hours=h)),
            value=999.0,
            rng=random.Random(3000 + h),
        )
        for h in range(6)
    ]
    obs_store.store_observations(background + partial_day)

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=model,  # type: ignore[arg-type]
        projection=projection,
        track_outcome=None,
        issue_time=issue_time,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=lambda: issue_time,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    past_targets = result.inputs.data.past_targets.sort("timestamp")
    assert 999.0 not in past_targets["discharge"].to_list(), (
        "past_targets contains a value built from the partial "
        "[00:00, 06:00) day-of-cycle window"
    )
    for ts in past_targets["timestamp"]:
        assert ts.hour == 0 and ts.minute == 0 and ts.second == 0
        assert ts < issue_time
