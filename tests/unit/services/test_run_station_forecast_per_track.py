"""Plan 151 T7 red-first: the runner consumption seam (D10, D10a)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import polars as pl

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.run_station_forecast import (
    AssignmentFailureCause,
    run_all_station_forecasts_per_track,
)
from sapphire_flow.services.track_assembly import (
    ForcingContract,
    MissingTrackContext,
    ReadyContext,
    UnavailableTrackContext,
    assemble_assignment_inputs,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.enums import (
    EnsembleMode,
    ModelAssignmentStatus,
    NwpCycleSource,
)
from sapphire_flow.types.forcing_track import (
    FeatureName,
    FutureSteps,
)
from sapphire_flow.types.forcing_track import (
    StationUnavailableReason as Reason,
)
from sapphire_flow.types.forecast import ForecastProvenance
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.model import StationInputData, StationModelInputs
from sapphire_flow.types.station import ModelAssignment
from tests.conftest import make_station_config
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import FakeModelArtifactStore, FakeModelStateStore

_NOW = ensure_utc(datetime(2025, 6, 1, 6, 0, tzinfo=UTC))
_STEP = timedelta(hours=24)
_STATION_ID = StationId(uuid4())
_MODEL_HIGH = ModelId("model-high-priority")
_MODEL_LOW = ModelId("model-low-priority")
_RNG = random.Random(42)


def _clock() -> UtcDatetime:
    return _NOW


def _id_gen() -> UUID:
    return uuid4()


def _config() -> DeploymentConfig:
    return DeploymentConfig(
        max_retention_days=1000, observation_staleness_warning_hours=6.0
    )


def _empty_qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(version="1.0", rules=())


def _assignment(model_id: ModelId, priority: int) -> ModelAssignment:
    return ModelAssignment(
        station_id=_STATION_ID,
        model_id=model_id,
        time_step=_STEP,
        status=ModelAssignmentStatus.ACTIVE,
        priority=priority,
        created_at=_NOW,
    )


def _empty_df() -> pl.DataFrame:
    return pl.DataFrame({"timestamp": []}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _obs_df() -> pl.DataFrame:
    rows = [{"timestamp": _NOW - timedelta(hours=i), "value": 10.0} for i in range(10)]
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _ready_context(
    *,
    future_dynamic: pl.DataFrame | None = None,
    contract: ForcingContract | None = None,
    horizon_steps: int = 5,
) -> ReadyContext:
    inputs = StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=_obs_df(),
            past_dynamic=_empty_df(),
            future_dynamic=future_dynamic
            if future_dynamic is not None
            else _empty_df(),
            static=None,
        ),
        issue_time=_NOW,
        forecast_horizon_steps=horizon_steps,
        time_step=_STEP,
    )
    provenance = ForecastProvenance(
        nwp_cycle_source=NwpCycleSource.PRIMARY, nwp_cycle_reference_time=_NOW
    )
    return ReadyContext(
        inputs=inputs,
        observation_staleness_hours=1.0,
        nwp_age_hours=0.5,
        provenance=provenance,
        contract=contract,
    )


class _SpyModelArtifactStore(FakeModelArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.accessed_model_ids: list[ModelId] = []

    def fetch_active_artifact_for_station(
        self, station_id: StationId, model_id: ModelId
    ) -> tuple[ArtifactId, bytes] | None:
        self.accessed_model_ids.append(model_id)
        return super().fetch_active_artifact_for_station(station_id, model_id)


class _SpyModelStateStore(FakeModelStateStore):
    def __init__(self) -> None:
        super().__init__()
        self.accessed_model_ids: list[ModelId] = []

    def fetch_latest_state(
        self, station_id: StationId, model_id: ModelId
    ) -> tuple[UtcDatetime, bytes] | None:
        self.accessed_model_ids.append(model_id)
        return super().fetch_latest_state(station_id, model_id)


def _seed_artifact(store: FakeModelArtifactStore, model_id: ModelId) -> None:
    from sapphire_flow.types.enums import ModelArtifactStatus

    store.store_artifact(
        model_id=model_id,
        artifact_bytes=b"fake_artifact",
        training_period_start=_NOW,
        training_period_end=_NOW,
        trained_at=_NOW,
        station_id=_STATION_ID,
        status=ModelArtifactStatus.ACTIVE,
    )


def test_assembly_and_runner_share_exactly_one_warm_up_load() -> None:
    """Review fold-in (major): the FULL pipeline -- `assemble_assignment_inputs`
    (T6) feeding `run_all_station_forecasts_per_track` (T7) -- must load
    warm-up state exactly ONCE, from `_run_single_model`'s existing
    assignment-local call. Fails before the fix: T6 loaded it a first time
    into a `ModelRunContext` that `_run_single_model` then silently
    discarded and reloaded, doubling the store read (and moving the first
    read's failure mode outside the `WARM_UP_LOAD_FAILED` path)."""
    from sapphire_flow.types.forcing_track import AssignmentKey, NoForcingRequired
    from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
    from tests.fakes.fake_stores import (
        FakeBasinStore,
        FakeObservationStore,
        FakeStationStore,
    )

    artifact_store = _SpyModelArtifactStore()
    state_store = _SpyModelStateStore()
    _seed_artifact(artifact_store, _MODEL_HIGH)
    model = FakeStationForecastModel()

    obs_store = FakeObservationStore()
    station_store = FakeStationStore()
    basin_store = FakeBasinStore()
    reanalysis = FakeWeatherReanalysisSource()
    station_store.store_station(make_station_config(station_id=_STATION_ID))

    projection = NoForcingRequired(assignment=AssignmentKey((_STATION_ID, _MODEL_HIGH)))
    ready = assemble_assignment_inputs(
        station_id=_STATION_ID,
        model_id=_MODEL_HIGH,
        model=model,  # type: ignore[arg-type]
        projection=projection,
        track_outcome=None,
        issue_time=_NOW,
        obs_store=obs_store,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        basin_store=basin_store,  # type: ignore[arg-type]
        forcing_source=reanalysis,  # type: ignore[arg-type]
        clock=_clock,  # type: ignore[arg-type]
    )
    assert isinstance(ready, ReadyContext)

    result = run_all_station_forecasts_per_track(
        station_id=_STATION_ID,
        run_inputs={_MODEL_HIGH: ready},
        assignments=[_assignment(_MODEL_HIGH, priority=1)],
        models={_MODEL_HIGH: model},  # type: ignore[dict-item]
        artifact_store=artifact_store,
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=_empty_qc_rules(),
        qc_overrides=[],
        baselines=[],
        config=_config(),
        clock=_clock,  # type: ignore[arg-type]
        id_gen=_id_gen,  # type: ignore[arg-type]
        rng=_RNG,
        model_state_store=state_store,
    )

    assert _MODEL_HIGH in result.results, result.failed_models
    assert state_store.accessed_model_ids == [_MODEL_HIGH]


def test_missing_context_advances_chain_without_touching_registry_or_stores() -> None:
    """Fails today: `run_all_station_forecasts_per_track` does not exist
    (Plan 151 T7 is net-new). The higher-priority assignment is READY and
    must succeed; the lower-priority one has a MissingTrackContext and must
    be recorded as MISSING_CONTEXT WITHOUT ever touching the artifact store
    or model-state store for it (D10)."""
    artifact_store = _SpyModelArtifactStore()
    state_store = _SpyModelStateStore()
    _seed_artifact(artifact_store, _MODEL_HIGH)
    model = FakeStationForecastModel()

    run_inputs = {
        _MODEL_HIGH: _ready_context(),
        _MODEL_LOW: MissingTrackContext(assignment=(_STATION_ID, _MODEL_LOW)),  # type: ignore[arg-type]
    }

    result = run_all_station_forecasts_per_track(
        station_id=_STATION_ID,
        run_inputs=run_inputs,
        assignments=[
            _assignment(_MODEL_HIGH, priority=1),
            _assignment(_MODEL_LOW, priority=2),
        ],
        models={_MODEL_HIGH: model, _MODEL_LOW: model},  # type: ignore[dict-item]
        artifact_store=artifact_store,
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=_empty_qc_rules(),
        qc_overrides=[],
        baselines=[],
        config=_config(),
        clock=_clock,  # type: ignore[arg-type]
        id_gen=_id_gen,  # type: ignore[arg-type]
        rng=_RNG,
        model_state_store=state_store,
    )

    assert _MODEL_HIGH in result.results
    assert (
        result.failed_models[_MODEL_LOW].cause is AssignmentFailureCause.MISSING_CONTEXT
    )
    assert _MODEL_LOW not in artifact_store.accessed_model_ids
    assert _MODEL_LOW not in state_store.accessed_model_ids


def test_unavailable_track_context_records_track_unavailable() -> None:
    artifact_store = _SpyModelArtifactStore()
    state_store = _SpyModelStateStore()
    model = FakeStationForecastModel()
    run_inputs = {
        _MODEL_LOW: UnavailableTrackContext(
            assignment=(_STATION_ID, _MODEL_LOW),  # type: ignore[arg-type]
            reason=Reason.INCOMPLETE_AT_CYCLE,
        ),
    }

    result = run_all_station_forecasts_per_track(
        station_id=_STATION_ID,
        run_inputs=run_inputs,
        assignments=[_assignment(_MODEL_LOW, priority=1)],
        models={_MODEL_LOW: model},  # type: ignore[dict-item]
        artifact_store=artifact_store,
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=_empty_qc_rules(),
        qc_overrides=[],
        baselines=[],
        config=_config(),
        clock=_clock,  # type: ignore[arg-type]
        id_gen=_id_gen,  # type: ignore[arg-type]
        rng=_RNG,
        model_state_store=state_store,
    )

    assert (
        result.failed_models[_MODEL_LOW].cause
        is AssignmentFailureCause.TRACK_UNAVAILABLE
    )
    assert _MODEL_LOW not in artifact_store.accessed_model_ids


def _future_frame(days: dict[str, int]) -> pl.DataFrame:
    rows = []
    for feature, n in days.items():
        for step in range(n):
            rows.append(
                {
                    "timestamp": _NOW + timedelta(days=step + 1),
                    feature: 1.0,
                }
            )
    pivot: dict[object, dict[str, object]] = {}
    for row in rows:
        pivot.setdefault(row["timestamp"], {"timestamp": row["timestamp"]}).update(row)
    return pl.DataFrame(list(pivot.values())).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def test_heterogeneous_contract_reaches_predict_via_per_feature_coverage() -> None:
    """D10a: precip contracted at 2 steps, temp at 10, frame assembled to
    match each. Fails today under the scalar path (`required_steps=10` makes
    `min(counts)=2` inadequate) — the per-feature contract fixes it."""
    artifact_store = _SpyModelArtifactStore()
    state_store = _SpyModelStateStore()
    _seed_artifact(artifact_store, _MODEL_HIGH)
    model = FakeStationForecastModel()
    frame = _future_frame({"precip": 2, "temp": 10})
    contract = ForcingContract(
        feature_horizons={
            FeatureName("precip"): FutureSteps(value=2),
            FeatureName("temp"): FutureSteps(value=10),
        },
        ensemble_mode=EnsembleMode.SINGLE,
        future_dynamic_features=frozenset({"precip", "temp"}),
    )
    run_inputs = {
        _MODEL_HIGH: _ready_context(
            future_dynamic=frame, contract=contract, horizon_steps=10
        ),
    }

    result = run_all_station_forecasts_per_track(
        station_id=_STATION_ID,
        run_inputs=run_inputs,
        assignments=[_assignment(_MODEL_HIGH, priority=1)],
        models={_MODEL_HIGH: model},  # type: ignore[dict-item]
        artifact_store=artifact_store,
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=_empty_qc_rules(),
        qc_overrides=[],
        baselines=[],
        config=_config(),
        clock=_clock,  # type: ignore[arg-type]
        id_gen=_id_gen,  # type: ignore[arg-type]
        rng=_RNG,
        model_state_store=state_store,
    )

    assert _MODEL_HIGH in result.results, result.failed_models


def test_short_feature_relative_to_its_own_contract_still_fails_coverage() -> None:
    """Paired negative case: a `ReadyContext` genuinely short on ONE feature
    relative to THAT feature's own contracted horizon still fails
    INSUFFICIENT_COVERAGE — proving the per-feature gate is tightened, not
    deleted (D10a)."""
    artifact_store = _SpyModelArtifactStore()
    state_store = _SpyModelStateStore()
    _seed_artifact(artifact_store, _MODEL_HIGH)
    model = FakeStationForecastModel()
    # precip only has 1 day, but its OWN contract demands 2.
    frame = _future_frame({"precip": 1, "temp": 10})
    contract = ForcingContract(
        feature_horizons={
            FeatureName("precip"): FutureSteps(value=2),
            FeatureName("temp"): FutureSteps(value=10),
        },
        ensemble_mode=EnsembleMode.SINGLE,
        future_dynamic_features=frozenset({"precip", "temp"}),
    )
    run_inputs = {
        _MODEL_HIGH: _ready_context(
            future_dynamic=frame, contract=contract, horizon_steps=10
        ),
    }

    result = run_all_station_forecasts_per_track(
        station_id=_STATION_ID,
        run_inputs=run_inputs,
        assignments=[_assignment(_MODEL_HIGH, priority=1)],
        models={_MODEL_HIGH: model},  # type: ignore[dict-item]
        artifact_store=artifact_store,
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=_empty_qc_rules(),
        qc_overrides=[],
        baselines=[],
        config=_config(),
        clock=_clock,  # type: ignore[arg-type]
        id_gen=_id_gen,  # type: ignore[arg-type]
        rng=_RNG,
        model_state_store=state_store,
    )

    assert (
        result.failed_models[_MODEL_HIGH].cause
        is AssignmentFailureCause.INSUFFICIENT_COVERAGE
    )


def _member_suffixed_frame(
    feature: str, member_ids: list[int], n_steps: int
) -> pl.DataFrame:
    rows = []
    for step in range(n_steps):
        row: dict[str, object] = {"timestamp": _NOW + timedelta(days=step + 1)}
        for member_id in member_ids:
            row[f"{feature}_{member_id}"] = 1.0
        rows.append(row)
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def test_single_feature_partial_ensemble_checked_against_expected_member_ids() -> None:
    """Review fold-in (blocker — T8 finding 2): a SINGLE required feature
    trivially passes a cross-feature-only defensive check (nothing to
    disagree with). Only 3 of the source's 51 expected members are present
    -- the assembled frame must still be rejected once `expected_member_ids`
    is available on the contract. Fails today: the pre-fix
    `_assert_consistent_member_set` only compares features to EACH OTHER,
    so a single-feature model with a uniformly-partial member set sails
    through."""
    artifact_store = _SpyModelArtifactStore()
    state_store = _SpyModelStateStore()
    _seed_artifact(artifact_store, _MODEL_HIGH)
    model = FakeStationForecastModel()
    frame = _member_suffixed_frame("precip", [0, 1, 2], n_steps=2)
    contract = ForcingContract(
        feature_horizons={FeatureName("precip"): FutureSteps(value=2)},
        ensemble_mode=EnsembleMode.ENSEMBLE,
        future_dynamic_features=frozenset({"precip"}),
        expected_member_ids=frozenset(range(51)),
    )
    run_inputs = {
        _MODEL_HIGH: _ready_context(
            future_dynamic=frame, contract=contract, horizon_steps=2
        ),
    }

    result = run_all_station_forecasts_per_track(
        station_id=_STATION_ID,
        run_inputs=run_inputs,
        assignments=[_assignment(_MODEL_HIGH, priority=1)],
        models={_MODEL_HIGH: model},  # type: ignore[dict-item]
        artifact_store=artifact_store,
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=_empty_qc_rules(),
        qc_overrides=[],
        baselines=[],
        config=_config(),
        clock=_clock,  # type: ignore[arg-type]
        id_gen=_id_gen,  # type: ignore[arg-type]
        rng=_RNG,
        model_state_store=state_store,
    )

    assert (
        result.failed_models[_MODEL_HIGH].cause
        is AssignmentFailureCause.INSUFFICIENT_COVERAGE
    )
