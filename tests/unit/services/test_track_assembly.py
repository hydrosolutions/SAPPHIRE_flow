"""Plan 151 T6 red-first: per-assignment operational-input assembly (D9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.services.track_assembly import (
    ForcingContract,
    ReadyContext,
    UnavailableTrackContext,
    assemble_assignment_inputs,
)
from sapphire_flow.types.datetime import ensure_utc
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
    NoForcingRequired,
    StationTrackAvailable,
    StationTrackUnavailable,
    StationUnavailableReason,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeModelStateStore,
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
    state_store = FakeModelStateStore()
    reanalysis = FakeWeatherReanalysisSource()
    station_store.store_station(make_station_config(station_id=_STATION))
    return obs_store, station_store, basin_store, state_store, reanalysis


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
    obs_store, station_store, basin_store, state_store, reanalysis = _stores()
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
        model_state_store=state_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    frame = result.run_context.inputs.data.future_dynamic
    assert frame.height == 2, "must cap to the assignment's own 2-step horizon"
    assert result.contract == ForcingContract(
        feature_horizons=horizons,
        ensemble_mode=EnsembleMode.SINGLE,
        future_dynamic_features=frozenset({FeatureName("precip")}),
    )


def test_nwp_age_hours_from_this_assignments_own_resolved_cycle() -> None:
    """A stale FALLBACK cycle for one assignment must not be reported as
    fresh via some OTHER shared cycle — nwp_age_hours is per-assignment."""
    obs_store, station_store, basin_store, state_store, reanalysis = _stores()
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
        model_state_store=state_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    assert result.run_context.nwp_age_hours is not None
    assert result.run_context.nwp_age_hours > 24.0
    assert result.provenance.nwp_cycle_source is NwpCycleSource.FALLBACK
    assert result.provenance.nwp_cycle_reference_time == stale_cycle


def test_no_forcing_required_assignment_gets_null_provenance_and_no_contract() -> None:
    obs_store, station_store, basin_store, state_store, reanalysis = _stores()
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
        model_state_store=state_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    assert result.contract is None
    assert result.provenance.nwp_cycle_source is NwpCycleSource.RUNOFF_ONLY
    assert result.provenance.nwp_cycle_reference_time is None
    assert result.run_context.nwp_age_hours is None


def test_unavailable_track_outcome_short_circuits_without_assembling() -> None:
    """D10: an unavailable station at an otherwise-resolved cycle must yield
    `UnavailableTrackContext`, not attempt assembly at all."""
    obs_store, station_store, basin_store, state_store, reanalysis = _stores()
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
        model_state_store=state_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )

    assert result == UnavailableTrackContext(
        assignment=AssignmentKey((_STATION, _MODEL)),
        reason=StationUnavailableReason.INCOMPLETE_AT_CYCLE,
    )
