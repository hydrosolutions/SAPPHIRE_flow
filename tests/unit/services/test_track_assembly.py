"""Plan 151 T6 red-first: per-assignment operational-input assembly (D9)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
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
    WeatherSourceRole,
    WeatherSourceStatus,
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
from tests.conftest import (
    make_observation,
    make_observations,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeObservationStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

_STATION = StationId(uuid4())
_MODEL = ModelId("track_assembly_test_model")
_STEP = timedelta(hours=24)
_NWP_SOURCE_261 = "icon_ch2_eps"
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
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
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
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
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
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
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
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
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
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
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
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
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
            weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
            nwp_source=_NWP_SOURCE_261,
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
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
        clock=lambda: issue_time,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    past_targets = result.inputs.data.past_targets.sort("timestamp")
    for ts in past_targets["timestamp"]:
        assert ts.hour == 0 and ts.minute == 0 and ts.second == 0
        assert ts < issue_time
    # The partial [00:00, 06:00) day-of-cycle window must not surface as its
    # OWN bucket at all — not merely dodge an exact-999.0 membership check
    # (a diluted mean of 30 background + 6 sentinel readings would pass that
    # check while still being a genuine leaked partial bucket). Exactly
    # `lookback_steps` COMPLETE days is the real invariant.
    assert past_targets.height == requirements.lookback_steps, (
        f"expected exactly {requirements.lookback_steps} complete daily "
        f"buckets, got {past_targets.height} — the partial day-of-cycle "
        "window leaked through as an extra bucket"
    )


def test_freshness_reflects_the_partial_bucket_not_the_aligned_window() -> None:
    """Review fixer round (major): see the identical
    `test_operational_inputs.py` fix — D4 aligns/truncates `past_targets` to
    exclude the current, still-forming UTC-calendar bucket (correct for the
    model), but `observation_staleness_hours` was computed from that same
    truncated collection. At a non-midnight cycle with a fresh reading
    minutes old, staleness was measured from the prior midnight instead.
    """
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

    data_start = ensure_utc(issue_time - timedelta(days=9))
    background = make_observations(
        n=9 * 24 * 6,
        station_id=_STATION,
        parameter="discharge",
        start=data_start,
        interval=timedelta(minutes=10),
    )
    # A fresh reading 10 minutes before issue_time — inside the partial,
    # not-yet-complete UTC-calendar bucket that `past_targets` correctly
    # excludes.
    fresh_obs = make_observation(
        station_id=_STATION,
        parameter="discharge",
        timestamp=ensure_utc(issue_time - timedelta(minutes=10)),
        value=42.0,
    )
    obs_store.store_observations(background + [fresh_obs])

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
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
        clock=lambda: issue_time,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    assert result.observation_staleness_hours is not None
    assert result.observation_staleness_hours < 1.0, (
        "expected freshness to reflect the 10-minute-old raw reading, got "
        f"{result.observation_staleness_hours}h — freshness was computed "
        "from the aligned/truncated past_targets window instead of the "
        "latest raw observation"
    )


def test_past_dynamic_is_resampled_to_the_declared_step_on_the_per_track_path() -> None:
    """Plan 239 T1: this path delivers `past_dynamic` INDEPENDENTLY of
    `operational_inputs.py`, so fixing only that module would leave this
    production route handing a daily model hourly forcing. Mirrors
    `test_operational_inputs.py::TestPastDynamicHonoursDeclaredResolution`.
    """
    from sapphire_flow.types.station import StationWeatherSource

    obs_store, station_store, basin_store, reanalysis = _stores()
    station_store.store_weather_source(
        StationWeatherSource(
            station_id=_STATION,
            nwp_source="era5_land",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
    )
    requirements = ModelDataRequirements(
        target_parameters=frozenset(),
        past_dynamic_features=frozenset({"precipitation"}),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),  # DAILY
        lookback_steps=2,
        forecast_horizon_steps=1,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.SINGLE,
    )
    # HOURLY reanalysis, spanning the whole aligned lookback window.
    reanalysis.set_records(
        [
            make_raw_historical_forcing(
                station_id=_STATION,
                parameter="precipitation",
                valid_time=ensure_utc(_ISSUE - timedelta(days=3) + timedelta(hours=i)),
                value=1.0,
            )
            for i in range(3 * 24)
        ]
    )

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=_FakeModel(requirements),  # type: ignore[arg-type]
        projection=NoForcingRequired(assignment=AssignmentKey((_STATION, _MODEL))),
        track_outcome=None,
        issue_time=_ISSUE,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        weather_forecast_store=FakeWeatherForecastStore(),  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
        clock=_clock,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    past_dynamic = result.inputs.data.past_dynamic
    assert not past_dynamic.is_empty()
    stamps = sorted(ensure_utc(ts) for ts in past_dynamic["timestamp"].to_list())
    gaps = {b - a for a, b in zip(stamps, stamps[1:], strict=False)}
    assert gaps <= {_STEP}, (
        f"per-track past_dynamic arrived at {gaps}, not the declared {_STEP}"
    )
    assert len(stamps) == requirements.lookback_steps


def test_past_forcing_tail_is_filled_on_the_per_track_path() -> None:
    """Plan 261 T1, independent review 2026-09-09 (major): the fill's unit
    tests call the helper directly, so removing the call from THIS assembler
    left them green. Mirrors
    `test_operational_inputs.py::TestPastForcingTailReachesTheWindowThroughTheAssembler`.

    A daily model, hourly reanalysis stopping one whole day short of the
    aligned window, and a complete 24-hour control-member forecast covering
    that missing day.
    """
    from sapphire_flow.types.station import StationWeatherSource

    obs_store, station_store, basin_store, reanalysis = _stores()
    station_store.store_weather_source(
        StationWeatherSource(
            station_id=_STATION,
            nwp_source="era5_land",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
    )
    requirements = ModelDataRequirements(
        target_parameters=frozenset(),
        past_dynamic_features=frozenset({"precipitation"}),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),  # DAILY
        lookback_steps=2,
        forecast_horizon_steps=1,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.SINGLE,
    )
    # An OFF-MIDNIGHT issue: 06Z for a daily model. `past_targets_end` is the
    # aligned bound (00:00Z), NOT the issue time — independent review
    # 2026-09-10 (major): both assembler tests previously used a bucket-aligned
    # issue time, so `window_end=issue_time` was indistinguishable from the
    # correct bound and mutating either call site left the suite green.
    issue_time = ensure_utc(datetime(2026, 1, 10, 6, tzinfo=UTC))
    window_end = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
    last_measured_day = ensure_utc(window_end - timedelta(days=2))
    # Hourly reanalysis covering only the FIRST of the two lookback days.
    reanalysis.set_records(
        [
            make_raw_historical_forcing(
                station_id=_STATION,
                parameter="precipitation",
                valid_time=ensure_utc(last_measured_day + timedelta(hours=i)),
                value=1.0,
            )
            for i in range(24)
        ]
    )
    forecast_store = FakeWeatherForecastStore()
    cycle = ensure_utc(window_end - timedelta(days=1, hours=6))  # 18Z, two days back
    forecast_store.store_weather_forecasts(
        [
            WeatherForecastRecord(
                id=uuid4(),
                station_id=_STATION,
                nwp_source=_NWP_SOURCE_261,
                cycle_time=cycle,
                valid_time=ensure_utc(
                    window_end - timedelta(days=1) + timedelta(hours=hour)
                ),
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=2.0,
                created_at=window_end,
            )
            for hour in range(24)
        ]
        # A COMPLETE day beyond the aligned bound. If the fill ran to the
        # issue time instead of `past_targets_end`, this day would be appended
        # as a partial bucket and `past_dynamic` would outgrow `past_targets`.
        + [
            WeatherForecastRecord(
                id=uuid4(),
                station_id=_STATION,
                nwp_source=_NWP_SOURCE_261,
                cycle_time=ensure_utc(window_end - timedelta(hours=6)),
                valid_time=ensure_utc(window_end + timedelta(hours=hour)),
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=3.0,
                created_at=window_end,
            )
            for hour in range(24)
        ]
    )

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=_FakeModel(requirements),  # type: ignore[arg-type]
        projection=NoForcingRequired(assignment=AssignmentKey((_STATION, _MODEL))),
        track_outcome=None,
        issue_time=issue_time,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        weather_forecast_store=forecast_store,  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
        clock=lambda: issue_time,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    past_dynamic = result.inputs.data.past_dynamic
    timestamps = [ensure_utc(t) for t in past_dynamic.get_column("timestamp").to_list()]
    # Both lookback days present: the measured one and the forecast-filled one.
    # Crucially the frame ENDS at the aligned bound — the in-progress bucket
    # (2026-01-10, which the store fully covers) is never appended.
    assert timestamps == [
        last_measured_day,
        ensure_utc(window_end - timedelta(days=1)),
    ]
    # (no height-vs-past_targets check here: this fixture seeds no
    # observations, so past_targets is empty by design. The station-assembler
    # test carries that comparison.)
    filled_value = past_dynamic.filter(
        pl.col("timestamp") == ensure_utc(window_end - timedelta(days=1))
    ).get_column("precipitation")[0]
    assert filled_value == 48.0  # 24 h x 2.0, SUM


def test_the_per_track_fill_stops_at_the_aligned_bound_not_the_issue_time() -> None:
    """Independent Codex review 2026-09-11 (major): the per-track fill's bound
    was pinned only by a DAILY model issued at 06Z, where mutating this call
    site to `window_end=issue_time` fetches six hours of the current day, the
    completeness gate rejects that partial bucket and the asserted output never
    moves. Measured: the mutation left 1184 service tests green.

    `test_operational_inputs.py::test_the_fill_stops_at_the_aligned_bound_not_the_issue_time`
    already carries the discriminating shape for the STATION path; this is its
    per-track mirror. An HOURLY model issued at 00:30 makes the two bounds
    differ, and a stored forecast AT the aligned bound is the bait: one hourly
    record COMPLETES an hourly bucket, so nothing masks the mutation.
    """
    from sapphire_flow.types.station import StationWeatherSource

    hourly = timedelta(hours=1)
    obs_store, station_store, basin_store, reanalysis = _stores()
    station_store.store_weather_source(
        StationWeatherSource(
            station_id=_STATION,
            nwp_source="era5_land",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
    )
    requirements = ModelDataRequirements(
        target_parameters=frozenset(),
        past_dynamic_features=frozenset({"precipitation"}),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({hourly}),
        lookback_steps=3,
        forecast_horizon_steps=1,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.SINGLE,
    )
    window_end = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))  # the aligned bound
    issue_time = ensure_utc(window_end + timedelta(minutes=30))
    # Reanalysis reaches the first lookback bucket only; 22:00 and 23:00 are
    # the fill's work.
    reanalysis.set_records(
        [
            make_raw_historical_forcing(
                station_id=_STATION,
                parameter="precipitation",
                valid_time=ensure_utc(window_end - timedelta(hours=3)),
                value=1.0,
            )
        ]
    )
    cycle = ensure_utc(window_end - timedelta(hours=6))
    forecast_store = FakeWeatherForecastStore()
    forecast_store.store_weather_forecasts(
        [
            WeatherForecastRecord(
                id=uuid4(),
                station_id=_STATION,
                nwp_source=_NWP_SOURCE_261,
                cycle_time=cycle,
                valid_time=ensure_utc(window_end - timedelta(hours=hours_back)),
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=2.0,
                created_at=window_end,
            )
            for hours_back in (2, 1)
        ]
        # THE BAIT: exactly at the aligned bound. Filling to `issue_time`
        # would append this in-progress bucket — and for an hourly model one
        # record is a COMPLETE bucket, so the completeness gate cannot hide it.
        + [
            WeatherForecastRecord(
                id=uuid4(),
                station_id=_STATION,
                nwp_source=_NWP_SOURCE_261,
                cycle_time=cycle,
                valid_time=window_end,
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=9.0,
                created_at=window_end,
            )
        ]
    )

    result = assemble_assignment_inputs(
        station_id=_STATION,
        model_id=_MODEL,
        model=_FakeModel(requirements),  # type: ignore[arg-type]
        projection=NoForcingRequired(assignment=AssignmentKey((_STATION, _MODEL))),
        track_outcome=None,
        issue_time=issue_time,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        weather_forecast_store=forecast_store,  # type: ignore[arg-type]
        nwp_source=_NWP_SOURCE_261,
        clock=lambda: issue_time,  # type: ignore[arg-type]
    )

    assert isinstance(result, ReadyContext)
    timestamps = [
        ensure_utc(t)
        for t in result.inputs.data.past_dynamic.get_column("timestamp").to_list()
    ]
    assert window_end not in timestamps, (
        "the in-progress bucket must never be appended on the per-track path"
    )
    assert timestamps == [
        ensure_utc(window_end - timedelta(hours=3)),
        ensure_utc(window_end - timedelta(hours=2)),
        ensure_utc(window_end - timedelta(hours=1)),
    ]
