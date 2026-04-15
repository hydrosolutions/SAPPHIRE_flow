from __future__ import annotations

import time
import traceback
from datetime import UTC
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import ConfigurationError, ModelSmokeTestError
from sapphire_flow.types.enums import (
    ArtifactScope,
    ModelAssignmentStatus,
    OnboardingOutcome,
    StationStatus,
)
from sapphire_flow.types.ids import StationGroupId
from sapphire_flow.types.model_onboarding import (
    CompatibilityReport,
    ModelOnboardingResult,
    OnboardingUnitResult,
    SkillGateResult,
)
from sapphire_flow.types.station import GroupModelAssignment, ModelAssignment
from sapphire_flow.types.training import TrainingUnit

if TYPE_CHECKING:
    import random
    from collections.abc import Callable
    from datetime import timedelta

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import (
        ForecastModel,
    )
    from sapphire_flow.protocols.stores import (
        BasinStore,
        FlowRegimeConfigStore,
        HindcastStore,
        ModelArtifactStore,
        ModelStore,
        ObservationStore,
        SkillStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
    from sapphire_flow.types.model import (
        GroupTrainingData,
        ModelDataRequirements,
        StationTrainingData,
    )
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)


def validate_compatibility(
    model: ForecastModel,
    station_config: StationConfig,
    available_features: frozenset[str],
    available_static: frozenset[str],
    requested_time_step: timedelta,
) -> CompatibilityReport:
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )

    protocol_conforms = isinstance(model, StationForecastModel | GroupForecastModel)
    req: ModelDataRequirements = model.data_requirements

    if station_config.forecast_targets is None:
        missing_targets = frozenset(req.target_parameters)
    else:
        missing_targets = req.target_parameters - station_config.forecast_targets

    missing_past = req.past_dynamic_features - available_features
    missing_future = req.future_dynamic_features - available_features
    missing_static = req.static_features - available_static
    time_step_ok = requested_time_step in req.supported_time_steps

    return CompatibilityReport(
        model_id=model_id_from_model(model),
        station_id=station_config.id,
        group_id=None,
        protocol_conforms=protocol_conforms,
        missing_target_parameters=missing_targets,
        missing_past_dynamic=missing_past,
        missing_future_dynamic=missing_future,
        missing_static_features=missing_static,
        time_step_compatible=time_step_ok,
    )


def model_id_from_model(model: ForecastModel) -> ModelId:
    # Models carry their identity via the registry — passed explicitly at call sites
    # that need model_id. This helper is not used; model_id is always passed directly.
    raise NotImplementedError("model_id must be passed explicitly")


def validate_compatibility_for_unit(
    model_id: ModelId,
    model: ForecastModel,
    unit: TrainingUnit,
    station_store: StationStore,
    group_store: StationGroupStore,
    available_features: frozenset[str],
    available_static_by_station: dict[StationId, frozenset[str]],
    requested_time_step: timedelta,
) -> CompatibilityReport:
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )

    protocol_conforms = isinstance(model, StationForecastModel | GroupForecastModel)
    req: ModelDataRequirements = model.data_requirements
    missing_past = req.past_dynamic_features - available_features
    missing_future = req.future_dynamic_features - available_features
    time_step_ok = requested_time_step in req.supported_time_steps

    # Union of all missing targets/static across member stations
    all_missing_targets: frozenset[str] = frozenset()
    all_missing_static: frozenset[str] = frozenset()

    for sid in unit.station_ids:
        station = station_store.fetch_station(sid)
        if station is None:
            all_missing_targets = all_missing_targets | frozenset(req.target_parameters)
            all_missing_static = all_missing_static | req.static_features
            continue
        if station.forecast_targets is None:
            all_missing_targets = all_missing_targets | frozenset(req.target_parameters)
        else:
            all_missing_targets = all_missing_targets | (
                req.target_parameters - station.forecast_targets
            )
        avail_static = available_static_by_station.get(sid, frozenset())
        all_missing_static = all_missing_static | (req.static_features - avail_static)

    return CompatibilityReport(
        model_id=model_id,
        station_id=unit.station_id,
        group_id=unit.group_id,
        protocol_conforms=protocol_conforms,
        missing_target_parameters=all_missing_targets,
        missing_past_dynamic=missing_past,
        missing_future_dynamic=missing_future,
        missing_static_features=all_missing_static,
        time_step_compatible=time_step_ok,
    )


def _make_synthetic_station_training_data(
    req: ModelDataRequirements,
    rng: random.Random,
    n_past_rows: int = 50,
    n_future_rows: int = 10,
) -> StationTrainingData:
    from datetime import datetime

    from sapphire_flow.types.model import StationTrainingData

    time_step = next(iter(req.supported_time_steps))
    base = datetime(2000, 1, 1, tzinfo=UTC)
    past_ts = [base + i * time_step for i in range(n_past_rows)]
    future_ts = [base + (n_past_rows + i) * time_step for i in range(n_future_rows)]

    def _rand_col(n: int) -> list[float]:
        return [max(0.0, rng.gauss(1.0, 0.5)) for _ in range(n)]

    past_targets = pl.DataFrame(
        {"timestamp": past_ts}
        | {col: _rand_col(n_past_rows) for col in req.target_parameters}
    )
    past_dynamic = pl.DataFrame(
        {"timestamp": past_ts}
        | {col: _rand_col(n_past_rows) for col in req.past_dynamic_features}
    )
    future_dynamic = pl.DataFrame(
        {"timestamp": future_ts}
        | {col: _rand_col(n_future_rows) for col in req.future_dynamic_features}
    )
    static: pl.DataFrame | None = None
    if req.static_features:
        static = pl.DataFrame({col: [rng.random()] for col in req.static_features})

    return StationTrainingData(
        past_targets=past_targets,
        past_dynamic=past_dynamic,
        future_dynamic=future_dynamic,
        static=static,
        time_step=time_step,
        val_start=None,
    )


def _make_synthetic_group_training_data(
    req: ModelDataRequirements,
    rng: random.Random,
    n_stations: int = 3,
    n_past_rows: int = 50,
    n_future_rows: int = 10,
) -> GroupTrainingData:
    from datetime import datetime

    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.model import GroupTrainingData

    time_step = next(iter(req.supported_time_steps))
    base = datetime(2000, 1, 1, tzinfo=UTC)
    past_ts = [base + i * time_step for i in range(n_past_rows)]
    future_ts = [base + (n_past_rows + i) * time_step for i in range(n_future_rows)]
    station_ids = tuple(StationId(f"synthetic_{i}") for i in range(n_stations))

    def _stacked_df(
        cols: frozenset[str],
        timestamps: list,
    ) -> pl.DataFrame:
        rows: list[dict] = []
        for sid in station_ids:
            for ts in timestamps:
                row: dict = {"station_id": str(sid), "timestamp": ts}
                for col in cols:
                    row[col] = max(0.0, rng.gauss(1.0, 0.5))
                rows.append(row)
        return pl.DataFrame(rows)

    static: pl.DataFrame | None = None
    if req.static_features:
        rows = []
        for sid in station_ids:
            row = {"station_id": str(sid)}
            for col in req.static_features:
                row[col] = rng.random()
            rows.append(row)
        static = pl.DataFrame(rows)

    return GroupTrainingData(
        group_id=StationGroupId("synthetic_group"),
        station_ids=station_ids,
        past_targets=_stacked_df(req.target_parameters, past_ts),
        past_dynamic=_stacked_df(req.past_dynamic_features, past_ts),
        future_dynamic=_stacked_df(req.future_dynamic_features, future_ts),
        static=static,
        time_step=time_step,
        val_start=None,
    )


def smoke_test_model(model: ForecastModel, rng: random.Random) -> None:
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.types.enums import ArtifactScope
    from sapphire_flow.types.ids import StationId

    req = model.data_requirements
    seed = hash(str(req)) & 0xFFFFFFFF
    rng.seed(seed)

    try:
        # Past needs lookback + horizon rows; future IS the horizon.
        smoke_horizon = max(req.forecast_horizon_steps, 10)
        n_past = req.lookback_steps + smoke_horizon + 10
        n_future = smoke_horizon

        if model.artifact_scope == ArtifactScope.GROUP:
            assert isinstance(model, GroupForecastModel)
            data = _make_synthetic_group_training_data(
                req, rng, n_past_rows=n_past, n_future_rows=n_future
            )
            artifact = model.train(data, {}, rng)
            raw_bytes = model.serialize_artifact(artifact)
            reloaded = model.deserialize_artifact(raw_bytes)

            time_step = next(iter(req.supported_time_steps))
            from sapphire_flow.types.model import GroupModelInputs

            station_ids = data.station_ids
            inputs = GroupModelInputs(
                group_id=data.group_id,
                station_ids=station_ids,
                past_targets=data.past_targets,
                past_dynamic=data.past_dynamic,
                future_dynamic=data.future_dynamic,
                static=data.static,
                issue_time=_utc_now(),
                forecast_horizon_steps=min(
                    req.forecast_horizon_steps, len(data.future_dynamic)
                ),
                time_step=time_step,
            )
            results = model.predict_batch(reloaded, inputs, rng)
            for _sid, (ensembles, _) in results.items():
                _validate_ensemble_dict(ensembles, req.target_parameters)
        else:
            assert isinstance(model, StationForecastModel)
            data = _make_synthetic_station_training_data(
                req, rng, n_past_rows=n_past, n_future_rows=n_future
            )
            artifact = model.train(data, {}, rng)
            raw_bytes = model.serialize_artifact(artifact)
            reloaded = model.deserialize_artifact(raw_bytes)

            time_step = next(iter(req.supported_time_steps))
            from sapphire_flow.types.model import StationInputData, StationModelInputs

            inputs = StationModelInputs(
                station_id=StationId("smoke_test_station"),
                data=StationInputData(
                    past_targets=data.past_targets,
                    past_dynamic=data.past_dynamic,
                    future_dynamic=data.future_dynamic,
                    static=data.static,
                ),
                issue_time=_utc_now(),
                forecast_horizon_steps=min(
                    req.forecast_horizon_steps, len(data.future_dynamic)
                ),
                time_step=time_step,
            )
            ensembles, _ = model.predict(reloaded, inputs, rng)
            _validate_ensemble_dict(ensembles, req.target_parameters)

    except Exception as exc:
        raise ModelSmokeTestError(
            f"Smoke test failed: {exc}\n{traceback.format_exc()}"
        ) from exc


def _utc_now() -> UtcDatetime:
    from datetime import datetime

    from sapphire_flow.types.datetime import UtcDatetime

    return UtcDatetime(datetime.now(tz=UTC))


def _validate_ensemble_dict(
    ensembles: dict[str, object],
    expected_keys: frozenset[str],
) -> None:
    missing = expected_keys - set(ensembles.keys())
    if missing:
        raise ModelSmokeTestError(
            f"predict() result missing keys: {missing}. Got: {set(ensembles.keys())}"
        )
    for key, ensemble in ensembles.items():
        if hasattr(ensemble, "parameter") and ensemble.parameter != key:
            raise ModelSmokeTestError(
                f"ForecastEnsemble key/value mismatch: key={key!r}, "
                f"ensemble.parameter={ensemble.parameter!r}"
            )


def evaluate_skill_gate(
    model_id: ModelId,
    model_artifact_id: ArtifactId,
    skill_store: SkillStore,
    config: DeploymentConfig,
) -> SkillGateResult:
    scores = skill_store.fetch_skill_scores(model_id, model_artifact_id)
    valid_scores = [s for s in scores if s.sample_size >= config.min_skill_samples]

    metric_worst: dict[str, float] = {}
    for score in valid_scores:
        gate_metric = score.metric
        if gate_metric not in config.skill_gate_thresholds:
            continue
        gate_cfg = config.skill_gate_thresholds[gate_metric]
        prev = metric_worst.get(gate_metric)
        if prev is None:
            metric_worst[gate_metric] = score.score
        elif gate_cfg.higher_is_better:
            metric_worst[gate_metric] = min(prev, score.score)
        else:
            metric_worst[gate_metric] = max(prev, score.score)

    metric_scores = tuple(metric_worst.items())
    thresholds = tuple(
        (name, cfg.threshold, cfg.higher_is_better)
        for name, cfg in config.skill_gate_thresholds.items()
    )

    failing: set[str] = set()
    for metric_name, gate_cfg in config.skill_gate_thresholds.items():
        worst = metric_worst.get(metric_name)
        if worst is None:
            failing.add(metric_name)
        elif gate_cfg.higher_is_better:
            if worst < gate_cfg.threshold:
                failing.add(metric_name)
        else:
            if worst > gate_cfg.threshold:
                failing.add(metric_name)

    return SkillGateResult(
        model_artifact_id=model_artifact_id,
        metric_scores=metric_scores,
        thresholds=thresholds,
        failing_metrics=frozenset(failing),
    )


def create_station_assignment(
    station_id: StationId,
    model_id: ModelId,
    time_step: timedelta,
    priority: int,
    station_store: StationStore,
    clock: Callable[[], UtcDatetime],
) -> ModelAssignment:
    existing = station_store.fetch_model_assignments(station_id)
    for assignment in existing:
        if assignment.model_id == model_id:
            if assignment.status == ModelAssignmentStatus.INACTIVE:
                log.warning(
                    "model.assignment_skipped_inactive",
                    station_id=str(station_id),
                    model_id=str(model_id),
                )
                return assignment
            break

    new_assignment = ModelAssignment(
        station_id=station_id,
        model_id=model_id,
        time_step=time_step,
        status=ModelAssignmentStatus.ACTIVE,
        priority=priority,
        created_at=clock(),
    )
    station_store.store_model_assignment(new_assignment)
    return new_assignment


def create_group_assignment(
    group_id: StationGroupId,
    model_id: ModelId,
    time_step: timedelta,
    priority: int,
    group_store: StationGroupStore,
    clock: Callable[[], UtcDatetime],
) -> GroupModelAssignment:
    existing = group_store.fetch_group_model_assignments(group_id)
    for assignment in existing:
        if assignment.model_id == model_id:
            if assignment.status == ModelAssignmentStatus.INACTIVE:
                log.warning(
                    "model.assignment_skipped_inactive",
                    group_id=str(group_id),
                    model_id=str(model_id),
                )
                return assignment
            break

    new_assignment = GroupModelAssignment(
        group_id=group_id,
        model_id=model_id,
        time_step=time_step,
        status=ModelAssignmentStatus.ACTIVE,
        priority=priority,
        created_at=clock(),
    )
    group_store.store_group_model_assignment(new_assignment)
    return new_assignment


def determine_onboarding_scope(
    model_id: ModelId,
    model: ForecastModel,
    station_ids: frozenset[StationId] | None,
    group_ids: frozenset[StationGroupId] | None,
    station_store: StationStore,
    group_store: StationGroupStore,
    training_period_start: UtcDatetime,
    training_period_end: UtcDatetime,
    time_step: timedelta,
) -> tuple[TrainingUnit, ...]:
    scope = model.artifact_scope
    units: list[TrainingUnit] = []

    if scope == ArtifactScope.STATION:
        if station_ids is not None:
            resolved = station_ids
        else:
            all_stations = station_store.fetch_all_stations()
            resolved = frozenset(
                s.id
                for s in all_stations
                if s.station_status == StationStatus.OPERATIONAL
            )
        for sid in resolved:
            units.append(
                TrainingUnit(
                    model_id=model_id,
                    station_id=sid,
                    group_id=None,
                    station_ids=frozenset({sid}),
                    training_period_start=training_period_start,
                    training_period_end=training_period_end,
                    time_step=time_step,
                )
            )
    elif scope == ArtifactScope.GROUP:
        if group_ids is not None:
            for gid in group_ids:
                group = group_store.fetch_group(gid)
                member_ids = group.station_ids if group is not None else frozenset()
                units.append(
                    TrainingUnit(
                        model_id=model_id,
                        station_id=None,
                        group_id=gid,
                        station_ids=member_ids,
                        training_period_start=training_period_start,
                        training_period_end=training_period_end,
                        time_step=time_step,
                    )
                )
        else:
            groups = group_store.fetch_groups_for_model(model_id)
            if not groups:
                raise ConfigurationError(
                    f"No groups found for group-scoped model {model_id!r}. "
                    "Provide explicit group_ids for initial onboarding."
                )
            for group in groups:
                units.append(
                    TrainingUnit(
                        model_id=model_id,
                        station_id=None,
                        group_id=group.id,
                        station_ids=group.station_ids,
                        training_period_start=training_period_start,
                        training_period_end=training_period_end,
                        time_step=time_step,
                    )
                )

    return tuple(units)


def onboard_model(
    model_id: ModelId,
    model: ForecastModel,
    units: tuple[TrainingUnit, ...],
    model_store: ModelStore,
    station_store: StationStore,
    group_store: StationGroupStore,
    artifact_store: ModelArtifactStore,
    obs_store: ObservationStore,
    basin_store: BasinStore,
    hindcast_store: HindcastStore,
    skill_store: SkillStore,
    flow_regime_store: FlowRegimeConfigStore,
    forcing_source: WeatherReanalysisSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    rng: random.Random,
    assignment_priority: int = 0,
    run_hindcast_fn: Callable[..., list] | None = None,
    compute_skill_fn: Callable[..., None] | None = None,
    skip_smoke_test: bool = False,
) -> ModelOnboardingResult:
    from sapphire_flow.services.training import (
        promote_artifact,
        train_group_model,
        train_station_model,
    )
    from sapphire_flow.services.training_data import (
        assemble_group_training_data,
        assemble_station_training_data,
    )

    log.info("model.onboarding_started", model_id=str(model_id), unit_count=len(units))
    unit_results: list[OnboardingUnitResult] = []

    for unit in units:
        unit_start = time.monotonic()
        sid_str = str(unit.station_id) if unit.station_id else None
        gid_str = str(unit.group_id) if unit.group_id else None
        log.info(
            "model.onboarding_unit_started",
            model_id=str(model_id),
            station_id=sid_str,
            group_id=gid_str,
        )

        # Step 1: Compatibility check
        avail_static: dict[StationId, frozenset[str]] = {}
        for sid in unit.station_ids:
            station = station_store.fetch_station(sid)
            if station is not None and station.basin_id is not None:
                basin = basin_store.fetch_basin(station.basin_id)
                if basin is not None and basin.attributes:
                    avail_static[sid] = frozenset(basin.attributes.keys())
                else:
                    avail_static[sid] = frozenset()
            else:
                avail_static[sid] = frozenset()

        compat = validate_compatibility_for_unit(
            model_id=model_id,
            model=model,
            unit=unit,
            station_store=station_store,
            group_store=group_store,
            available_features=config.available_nwp_parameters,
            available_static_by_station=avail_static,
            requested_time_step=unit.time_step,
        )

        if not compat.is_compatible:
            log.info(
                "model.compatibility_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                is_compatible=False,
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.SKIPPED_COMPAT,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                )
            )
            continue

        log.info(
            "model.compatibility_completed",
            model_id=str(model_id),
            station_id=sid_str,
            group_id=gid_str,
            is_compatible=True,
        )

        # Step 1b: Smoke test (skipped when called from station onboarding)
        smoke_failed = False
        if skip_smoke_test:
            log.info(
                "model.smoke_test_skipped",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
            )
        else:
            try:
                smoke_test_model(model, rng)
                log.info(
                    "model.smoke_test_completed",
                    model_id=str(model_id),
                    station_id=sid_str,
                    group_id=gid_str,
                    passed=True,
                )
            except ModelSmokeTestError as exc:
                log.error(
                    "model.smoke_test_failed",
                    model_id=str(model_id),
                    station_id=sid_str,
                    group_id=gid_str,
                    error=str(exc),
                )
                smoke_failed = True
                unit_results.append(
                    OnboardingUnitResult(
                        unit=unit,
                        outcome=OnboardingOutcome.FAILED_SMOKE_TEST,
                        compatibility=compat,
                        artifact_id=None,
                        hindcast_steps=(),
                        skill_gate=None,
                        error=str(exc),
                    )
                )
                continue
        if smoke_failed:
            continue

        # Step 2: Assemble training data
        try:
            if unit.station_id is not None:
                training_data = assemble_station_training_data(
                    station_id=unit.station_id,
                    model=model,
                    period_start=unit.training_period_start,
                    period_end=unit.training_period_end,
                    time_step=unit.time_step,
                    forcing_source=forcing_source,
                    obs_store=obs_store,
                    basin_store=basin_store,
                    station_store=station_store,
                )
            else:
                group = group_store.fetch_group(unit.group_id)
                training_data = assemble_group_training_data(
                    group=group,
                    model=model,
                    period_start=unit.training_period_start,
                    period_end=unit.training_period_end,
                    time_step=unit.time_step,
                    forcing_source=forcing_source,
                    obs_store=obs_store,
                    basin_store=basin_store,
                    station_store=station_store,
                )
        except Exception as exc:
            log.error(
                "model.training_data_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_TRAINING,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                    error=str(exc),
                )
            )
            continue

        if training_data is None:
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.SKIPPED_NO_DATA,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                )
            )
            continue

        # Step 3: Train
        try:
            from sapphire_flow.types.enums import ArtifactScope

            if model.artifact_scope == ArtifactScope.STATION:
                artifact_bytes = train_station_model(model, training_data, {}, rng)
            else:
                artifact_bytes = train_group_model(model, training_data, {}, rng)
        except Exception as exc:
            log.error(
                "model.training_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_TRAINING,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                    error=str(exc),
                )
            )
            continue

        # Step 4: Store artifact (TRAINING status — do NOT promote yet)
        try:
            from sapphire_flow.types.enums import ModelArtifactStatus

            artifact_id, _ = artifact_store.store_artifact(
                model_id=model_id,
                artifact_bytes=artifact_bytes,
                training_period_start=unit.training_period_start,
                training_period_end=unit.training_period_end,
                trained_at=clock(),
                station_id=unit.station_id,
                group_id=unit.group_id,
                status=ModelArtifactStatus.TRAINING,
            )
        except Exception as exc:
            log.error(
                "model.artifact_store_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_TRAINING,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                    error=str(exc),
                )
            )
            continue

        # Step 5: Hindcast
        hindcast_steps: list = []
        if run_hindcast_fn is not None:
            try:
                hindcast_steps = run_hindcast_fn(
                    unit=unit,
                    model=model,
                    artifact_id=artifact_id,
                    artifact_store=artifact_store,
                    obs_store=obs_store,
                    hindcast_store=hindcast_store,
                    forcing_source=forcing_source,
                    station_store=station_store,
                    basin_store=basin_store,
                    clock=clock,
                    rng=rng,
                )
            except Exception as exc:
                log.error(
                    "model.hindcast_failed",
                    model_id=str(model_id),
                    station_id=sid_str,
                    group_id=gid_str,
                    error=str(exc),
                )
                unit_results.append(
                    OnboardingUnitResult(
                        unit=unit,
                        outcome=OnboardingOutcome.FAILED_HINDCAST,
                        compatibility=compat,
                        artifact_id=artifact_id,
                        hindcast_steps=(),
                        skill_gate=None,
                        error=str(exc),
                    )
                )
                continue

        # Step 6: Compute skill
        if compute_skill_fn is not None:
            try:
                compute_skill_fn(
                    unit=unit,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_store=hindcast_store,
                    obs_store=obs_store,
                    skill_store=skill_store,
                    flow_regime_store=flow_regime_store,
                    config=config,
                )
            except Exception as exc:
                log.error(
                    "model.skill_computation_failed",
                    model_id=str(model_id),
                    station_id=sid_str,
                    group_id=gid_str,
                    error=str(exc),
                )
                unit_results.append(
                    OnboardingUnitResult(
                        unit=unit,
                        outcome=OnboardingOutcome.FAILED_SKILL,
                        compatibility=compat,
                        artifact_id=artifact_id,
                        hindcast_steps=tuple(hindcast_steps),
                        skill_gate=None,
                        error=str(exc),
                    )
                )
                continue

        # Step 7: Evaluate skill gate
        try:
            skill_gate = evaluate_skill_gate(
                model_id=model_id,
                model_artifact_id=artifact_id,
                skill_store=skill_store,
                config=config,
            )
        except Exception as exc:
            log.error(
                "model.skill_gate_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_SKILL,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=None,
                    error=str(exc),
                )
            )
            continue

        # Insufficient evaluation data — distinguish from gate rejection
        if len(skill_gate.metric_scores) == 0 and not skill_gate.passed:
            log.warning(
                "model.skill_gate_completed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                passed=False,
                failing_metrics=list(skill_gate.failing_metrics),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.SKIPPED_INSUFFICIENT_EVAL,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=skill_gate,
                )
            )
            continue

        if not skill_gate.passed:
            log.warning(
                "model.skill_gate_completed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                passed=False,
                failing_metrics=list(skill_gate.failing_metrics),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.GATE_REJECTED,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=skill_gate,
                )
            )
            continue

        log.info(
            "model.skill_gate_completed",
            model_id=str(model_id),
            station_id=sid_str,
            group_id=gid_str,
            passed=True,
            failing_metrics=[],
        )

        # Step 8: Promote artifact (TRAINING → ACTIVE)
        try:
            promote_artifact(
                artifact_store=artifact_store,
                model_id=model_id,
                new_id=artifact_id,
                station_id=unit.station_id,
                group_id=unit.group_id,
            )
            log.info(
                "model.promotion_completed",
                model_id=str(model_id),
                artifact_id=str(artifact_id),
                station_id=sid_str,
                group_id=gid_str,
            )
        except Exception as exc:
            log.error(
                "model.promotion_failed",
                model_id=str(model_id),
                artifact_id=str(artifact_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_ASSIGNMENT,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=skill_gate,
                    error=str(exc),
                )
            )
            continue

        # Step 9: Create assignment
        try:
            if unit.station_id is not None:
                create_station_assignment(
                    station_id=unit.station_id,
                    model_id=model_id,
                    time_step=unit.time_step,
                    priority=assignment_priority,
                    station_store=station_store,
                    clock=clock,
                )
            else:
                create_group_assignment(
                    group_id=unit.group_id,
                    model_id=model_id,
                    time_step=unit.time_step,
                    priority=assignment_priority,
                    group_store=group_store,
                    clock=clock,
                )
        except Exception as exc:
            log.error(
                "model.assignment_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_ASSIGNMENT,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=skill_gate,
                    error=str(exc),
                )
            )
            continue

        duration_ms = int((time.monotonic() - unit_start) * 1000)
        log.info(
            "model.onboarding_unit_completed",
            model_id=str(model_id),
            station_id=sid_str,
            group_id=gid_str,
            outcome=OnboardingOutcome.PROMOTED.value,
            duration_ms=duration_ms,
        )
        unit_results.append(
            OnboardingUnitResult(
                unit=unit,
                outcome=OnboardingOutcome.PROMOTED,
                compatibility=compat,
                artifact_id=artifact_id,
                hindcast_steps=tuple(hindcast_steps),
                skill_gate=skill_gate,
            )
        )

    result = ModelOnboardingResult(
        model_id=model_id,
        units=tuple(unit_results),
    )
    log.info(
        "model.onboarding_completed",
        model_id=str(model_id),
        promoted_count=result.promoted_count(),
        failed_count=result.failed_count(),
        skipped_count=result.skipped_count(),
        gate_rejected_count=result.gate_rejected_count(),
    )
    return result
