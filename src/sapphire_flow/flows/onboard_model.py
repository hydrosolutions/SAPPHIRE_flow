from __future__ import annotations

import hashlib
import os
import random
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from prefect.concurrency.sync import concurrency
from prefect.utilities.annotations import unmapped

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.flows.run_hindcast import run_hindcast_flow
from sapphire_flow.protocols.forecast_model import (
    GroupForecastModel,
    StationForecastModel,
)
from sapphire_flow.services.model_onboarding import (
    assert_model_conforms,
    assert_operational_floors,
    create_group_assignment,
    create_station_assignment,
    determine_onboarding_scope,
    evaluate_skill_gate,
    smoke_test_model,
    validate_compatibility_for_unit,
)
from sapphire_flow.services.model_registry import build_registry_entry, register_models
from sapphire_flow.services.training import (
    promote_artifact,
    train_group_model,
    train_station_model,
)
from sapphire_flow.services.training_data import (
    assemble_group_training_data,
    assemble_station_training_data,
)
from sapphire_flow.types.enums import ModelArtifactStatus, OnboardingOutcome
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.model_onboarding import (
    CompatibilityReport,
    ModelOnboardingResult,
    OnboardingUnitResult,
    SkillGateResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.stores import HistoricalForcingStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId
    from sapphire_flow.types.model import (
        GroupTrainingData,
        ModelRegistryEntry,
        StationTrainingData,
    )
    from sapphire_flow.types.training import TrainingUnit

log = structlog.get_logger(__name__)


def _unit_shard(unit: TrainingUnit) -> str:
    return str(unit.station_id) if unit.station_id is not None else str(unit.group_id)


def _build_station_code_resolver(
    station_store: object,
) -> Callable[[StationId], str]:
    def resolve_station_code(station_id: StationId) -> str:
        station = station_store.fetch_station(station_id)  # type: ignore[union-attr]
        if station is None:
            raise ConfigurationError(
                f"station_store could not resolve station_id {station_id!r}"
            )

        station_code = station.code.strip()
        if not station_code:
            raise ConfigurationError(
                f"station_store resolved station_id {station_id!r} without a code"
            )
        return station_code

    return resolve_station_code


@task(
    name="determine-onboarding-scope",
    task_run_name="determine-onboarding-scope-{model_id}",
    cache_policy=NO_CACHE,
)
def _determine_onboarding_scope_task(
    model_id: ModelId,
    model: object,
    station_ids: frozenset[StationId] | None,
    group_ids: frozenset[StationGroupId] | None,
    station_store: object,
    group_store: object,
    training_period_start: object,
    training_period_end: object,
    time_step: timedelta,
) -> tuple[TrainingUnit, ...]:
    return determine_onboarding_scope(
        model_id=model_id,
        model=model,  # type: ignore[arg-type]
        station_ids=station_ids,
        group_ids=group_ids,
        station_store=station_store,  # type: ignore[arg-type]
        group_store=group_store,  # type: ignore[arg-type]
        training_period_start=training_period_start,  # type: ignore[arg-type]
        training_period_end=training_period_end,  # type: ignore[arg-type]
        time_step=time_step,
    )


@task(
    name="register-model-class",
    task_run_name="register-model-class-{model_id}",
    cache_policy=NO_CACHE,
)
def _register_model_class_task(
    model_id: ModelId,
    model: object,
    model_store: object,
    clock: object,
) -> ModelRegistryEntry:
    from sapphire_flow.types.model import ModelRecord

    entry = build_registry_entry(
        model_id=model_id,
        model=model,  # type: ignore[arg-type]
        registered_at=clock(),  # type: ignore[operator]
    )
    record = ModelRecord(
        id=entry.id,
        display_name=entry.display_name,
        artifact_scope=entry.artifact_scope,
        description=entry.description,
        created_at=clock(),  # type: ignore[operator]
    )
    model_store.register_model(record)  # type: ignore[union-attr]
    return entry


def _render_validate_compat_name() -> str:
    from prefect import runtime

    params = runtime.task_run.parameters or {}
    model_id = params.get("model_id", "unknown")
    unit = params.get("unit")
    shard = _unit_shard(unit) if unit is not None else "unknown"
    return f"validate-compat-{model_id}-{shard}"


def _render_assemble_onboarding_data_name() -> str:
    from prefect import runtime

    params = runtime.task_run.parameters or {}
    unit = params.get("unit")
    shard = _unit_shard(unit) if unit is not None else "unknown"
    return f"assemble-onboarding-data-{shard}"


def _render_train_onboarding_model_name() -> str:
    from prefect import runtime

    params = runtime.task_run.parameters or {}
    unit = params.get("unit")
    shard = _unit_shard(unit) if unit is not None else "unknown"
    return f"train-onboarding-model-{shard}"


def _render_store_onboarding_artifact_name() -> str:
    from prefect import runtime

    params = runtime.task_run.parameters or {}
    unit = params.get("unit")
    shard = _unit_shard(unit) if unit is not None else "unknown"
    return f"store-onboarding-artifact-{shard}"


def _render_promote_artifact_name() -> str:
    from prefect import runtime

    params = runtime.task_run.parameters or {}
    unit = params.get("unit")
    artifact_id = params.get("artifact_id", "unknown")
    shard = _unit_shard(unit) if unit is not None else "unknown"
    return f"promote-artifact-{shard}-{artifact_id}"


def _render_create_assignment_name() -> str:
    from prefect import runtime

    params = runtime.task_run.parameters or {}
    model_id = params.get("model_id", "unknown")
    unit = params.get("unit")
    shard = _unit_shard(unit) if unit is not None else "unknown"
    return f"create-assignment-{model_id}-{shard}"


def _render_onboard_model_flow_name() -> str:
    from prefect import runtime

    params = runtime.flow_run.parameters or {}
    model_id = params.get("model_id", "unknown")
    period_start = params.get("period_start")
    if period_start:
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(period_start)
            stamp = dt.strftime("%Y%m%d")
        except ValueError:
            stamp = str(period_start)
    else:
        scheduled = runtime.flow_run.scheduled_start_time
        stamp = scheduled.strftime("%Y%m%d") if scheduled else "unknown"
    return f"onboard-{model_id}-{stamp}"


@task(
    name="validate-compatibility",
    log_prints=False,
    task_run_name=_render_validate_compat_name,
    cache_policy=NO_CACHE,
)
def _validate_compatibility_task(
    model_id: ModelId,
    model: object,
    unit: TrainingUnit,
    station_store: object,
    group_store: object,
    basin_store: object,
    parameter_store: object,
    deployment_config: object,
) -> CompatibilityReport:
    avail_static_by_station: dict[StationId, frozenset[str]] = {}
    for sid in unit.station_ids:
        station = station_store.fetch_station(sid)  # type: ignore[union-attr]
        has_basin = station is not None and station.basin_id is not None
        if has_basin and basin_store is not None:
            basin = basin_store.fetch_basin(station.basin_id)  # type: ignore[union-attr]
            if basin is not None and basin.attributes:
                avail_static_by_station[sid] = frozenset(basin.attributes.keys())
            else:
                avail_static_by_station[sid] = frozenset()
        else:
            avail_static_by_station[sid] = frozenset()

    canonical_units = {
        parameter.name: parameter.unit
        for parameter in parameter_store.fetch_all()  # type: ignore[union-attr]
    }

    return validate_compatibility_for_unit(
        model_id=model_id,
        model=model,  # type: ignore[arg-type]
        unit=unit,
        station_store=station_store,  # type: ignore[arg-type]
        group_store=group_store,  # type: ignore[arg-type]
        available_past_features=deployment_config.available_past_nwp_parameters,  # type: ignore[union-attr]
        available_future_features=deployment_config.available_nwp_parameters,  # type: ignore[union-attr]
        available_static_by_station=avail_static_by_station,
        requested_time_step=unit.time_step,
        canonical_units=canonical_units,
    )


@task(
    name="smoke-test-model",
    log_prints=False,
    task_run_name="smoke-test-model",
    cache_policy=NO_CACHE,
)
def _smoke_test_model_task(
    model: object,
    deployment_config: object,
    rng: random.Random,
) -> None:
    from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter

    if isinstance(model, ForecastInterfaceAdapter):
        assert_model_conforms(model, rng)
        assert_operational_floors(
            model=model,
            config=deployment_config,  # type: ignore[arg-type]
            rng=rng,
        )
        return

    smoke_test_model(model=model, rng=rng)  # type: ignore[arg-type]


@task(
    name="assemble-onboarding-data",
    log_prints=False,
    task_run_name=_render_assemble_onboarding_data_name,
    cache_policy=NO_CACHE,
)
def _assemble_onboarding_data_task(
    unit: TrainingUnit,
    model: object,
    forcing_source: object,
    obs_store: object,
    basin_store: object,
    station_store: object,
    group_store: object,
) -> StationTrainingData | GroupTrainingData | None:
    if unit.station_id is not None:
        return assemble_station_training_data(
            station_id=unit.station_id,
            model=model,  # type: ignore[arg-type]
            period_start=unit.training_period_start,
            period_end=unit.training_period_end,
            time_step=unit.time_step,
            forcing_source=forcing_source,
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )
    else:
        group = group_store.fetch_group(unit.group_id)  # type: ignore[union-attr]
        if group is None:
            return None
        return assemble_group_training_data(
            group=group,
            model=model,  # type: ignore[arg-type]
            period_start=unit.training_period_start,
            period_end=unit.training_period_end,
            time_step=unit.time_step,
            forcing_source=forcing_source,
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )


@task(
    name="train-onboarding-model",
    log_prints=False,
    task_run_name=_render_train_onboarding_model_name,
    cache_policy=NO_CACHE,
)
def _train_onboarding_model_task(
    unit: TrainingUnit,
    model: object,
    data: object,
    rng: random.Random,
) -> bytes:
    if unit.station_id is not None:
        return train_station_model(
            model=model,  # type: ignore[arg-type]
            data=data,  # type: ignore[arg-type]
            params={},
            rng=rng,
        )
    else:
        return train_group_model(
            model=model,  # type: ignore[arg-type]
            data=data,  # type: ignore[arg-type]
            params={},
            rng=rng,
        )


@task(
    name="store-onboarding-artifact",
    log_prints=False,
    task_run_name=_render_store_onboarding_artifact_name,
    cache_policy=NO_CACHE,
)
def _store_onboarding_artifact_task(
    unit: TrainingUnit,
    artifact_bytes: bytes,
    artifact_store: object,
    clock: object,
) -> ArtifactId:
    artifact_id, sha256_hash = artifact_store.store_artifact(  # type: ignore[union-attr]
        model_id=unit.model_id,
        artifact_bytes=artifact_bytes,
        training_period_start=unit.training_period_start,
        training_period_end=unit.training_period_end,
        trained_at=clock(),  # type: ignore[operator]
        station_id=unit.station_id,
        group_id=unit.group_id,
        status=ModelArtifactStatus.TRAINING,
    )

    # SHA-256 integrity verification — a system-level integrity failure,
    # deliberately NOT wrapped by the training try/except in the flow body:
    # a store outage or hash mismatch must abort the run loudly, never be
    # downgraded to a per-unit FAILED_TRAINING outcome (Plan 130 Part B scope
    # is the training call only; see train_models.py's parallel split).
    computed_hash = hashlib.sha256(artifact_bytes).hexdigest()
    if computed_hash != sha256_hash:
        raise ValueError(
            f"SHA-256 mismatch for artifact {artifact_id}: "
            f"computed={computed_hash[:8]}... stored={sha256_hash[:8]}..."
        )

    return artifact_id


@task(
    name="evaluate-skill-gate",
    log_prints=False,
    task_run_name="evaluate-skill-gate-{model_id}-{artifact_id}",
    cache_policy=NO_CACHE,
)
def _evaluate_skill_gate_task(
    model_id: ModelId,
    artifact_id: ArtifactId,
    skill_store: object,
    deployment_config: object,
) -> SkillGateResult:
    return evaluate_skill_gate(
        model_id=model_id,
        model_artifact_id=artifact_id,
        skill_store=skill_store,  # type: ignore[arg-type]
        config=deployment_config,  # type: ignore[arg-type]
    )


@task(
    name="promote-artifact",
    log_prints=False,
    task_run_name=_render_promote_artifact_name,
    cache_policy=NO_CACHE,
)
def _promote_artifact_task(
    unit: TrainingUnit,
    artifact_id: ArtifactId,
    artifact_store: object,
) -> None:
    promote_artifact(
        artifact_store=artifact_store,  # type: ignore[arg-type]
        model_id=unit.model_id,
        new_id=artifact_id,
        station_id=unit.station_id,
        group_id=unit.group_id,
    )


@task(
    name="create-assignment",
    log_prints=False,
    task_run_name=_render_create_assignment_name,
    cache_policy=NO_CACHE,
)
def _create_assignment_task(
    unit: TrainingUnit,
    model_id: ModelId,
    assignment_priority: int,
    station_store: object,
    group_store: object,
    clock: object,
) -> None:
    if unit.station_id is not None:
        create_station_assignment(
            station_id=unit.station_id,
            model_id=model_id,
            time_step=unit.time_step,
            priority=assignment_priority,
            station_store=station_store,  # type: ignore[arg-type]
            clock=clock,  # type: ignore[arg-type]
        )
    else:
        create_group_assignment(
            group_id=unit.group_id,  # type: ignore[arg-type]
            model_id=model_id,
            time_step=unit.time_step,
            priority=assignment_priority,
            group_store=group_store,  # type: ignore[arg-type]
            clock=clock,  # type: ignore[arg-type]
        )


@flow(
    name="onboard-model",
    log_prints=False,
    flow_run_name=_render_onboard_model_flow_name,
)
def onboard_model_flow(
    model_id: str,
    station_ids: list[str] | None = None,
    group_ids: list[str] | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    time_step_hours: int = 24,
    assignment_priority: int | None = None,
    model_store: object = None,
    station_store: object = None,
    group_store: object = None,
    obs_store: object = None,
    basin_store: object = None,
    artifact_store: object = None,
    hindcast_store: object = None,
    skill_store: object = None,
    flow_regime_store: object = None,
    parameter_store: object = None,
    forcing_store: object = None,
    forcing_source: object = None,
    deployment_config: object = None,
    clock: object = None,
    rng: object = None,
) -> ModelOnboardingResult:
    from datetime import UTC, datetime
    from typing import cast
    from uuid import UUID

    import prefect.runtime

    from sapphire_flow.adapters.forecast_interface import adapt_if_fi
    from sapphire_flow.exceptions import ModelSmokeTestError
    from sapphire_flow.services.model_registry import discover_models
    from sapphire_flow.types.datetime import ensure_utc

    structlog.contextvars.bind_contextvars(model_id=model_id)

    # --- Production setup ---
    _conn: object = None  # noqa: F841 — GC anchor for bootstrapped DB connection
    if station_store is None:
        from sapphire_flow.flows._db import setup_production_stores

        database_url = os.environ["DATABASE_URL"]
        _conn, stores = setup_production_stores(database_url)
        model_store = stores["model_store"]
        station_store = stores["station_store"]
        group_store = stores["group_store"]
        obs_store = stores["obs_store"]
        basin_store = stores["basin_store"]
        artifact_store = stores["artifact_store"]
        hindcast_store = stores["hindcast_store"]
        skill_store = stores["skill_store"]
        flow_regime_store = stores["flow_regime_store"]
        parameter_store = stores["parameter_store"]
        forcing_store = stores["forcing_store"]

    if deployment_config is None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.deployment import load_config

            deployment_config = load_config(config_path)
        else:
            from sapphire_flow.config.deployment import DeploymentConfig

            deployment_config = DeploymentConfig(max_retention_days=600)

    # Route through the single reanalysis-source factory (Plan 115a §6) so the
    # mode is a deployment decision made in exactly one place. None stays
    # allowed for empty-scope (register-only) runs, or when forcing_store is
    # unavailable — the per-unit loop only dereferences forcing_source when
    # the scope is non-empty.
    if forcing_source is None and forcing_store is not None:
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            select_reanalysis_source,
        )
        from sapphire_flow.config.deployment import DeploymentConfig

        resolved_config = cast("DeploymentConfig", deployment_config)
        forcing_source = select_reanalysis_source(
            forcing_store=cast("HistoricalForcingStore", forcing_store),
            mode=resolved_config.reanalysis_source,
        )

    if model_store is None:
        raise ConfigurationError("model_store is required but was not provided")
    if station_store is None:
        raise ConfigurationError("station_store is required but was not provided")
    if group_store is None:
        raise ConfigurationError("group_store is required but was not provided")
    if obs_store is None:
        raise ConfigurationError("obs_store is required but was not provided")
    if basin_store is None:
        raise ConfigurationError("basin_store is required but was not provided")
    if artifact_store is None:
        raise ConfigurationError("artifact_store is required but was not provided")
    if hindcast_store is None:
        raise ConfigurationError("hindcast_store is required but was not provided")
    if skill_store is None:
        raise ConfigurationError("skill_store is required but was not provided")
    if flow_regime_store is None:
        raise ConfigurationError("flow_regime_store is required but was not provided")
    if parameter_store is None:
        raise ConfigurationError("parameter_store is required but was not provided")

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    if rng is None:
        rng = random.Random()

    # Parse boundary
    if period_start is not None:
        parsed_start: UtcDatetime = ensure_utc(datetime.fromisoformat(period_start))
    else:
        now = clock()
        parsed_start = ensure_utc(
            datetime(now.year - 2, now.month, now.day, tzinfo=UTC)
        )

    if period_end is not None:
        parsed_end: UtcDatetime = ensure_utc(datetime.fromisoformat(period_end))
    else:
        parsed_end = clock()

    if parsed_start.year < 1900:
        raise ValueError(f"period_start year {parsed_start.year} is before 1900")
    if parsed_end > clock() + timedelta(days=1):
        raise ValueError("period_end is more than 1 day in the future")

    time_step = timedelta(hours=time_step_hours)
    typed_model_id = ModelId(model_id)
    resolved_assignment_priority = (
        assignment_priority
        if assignment_priority is not None
        else deployment_config.assignment_priority_for_model(typed_model_id)  # type: ignore[attr-defined]
    )

    typed_station_ids: frozenset[StationId] | None = None
    if station_ids is not None:
        typed_station_ids = frozenset(StationId(UUID(s)) for s in station_ids)

    typed_group_ids: frozenset[StationGroupId] | None = None
    if group_ids is not None:
        typed_group_ids = frozenset(StationGroupId(UUID(g)) for g in group_ids)

    # Discover and register model
    discovered = discover_models()
    if typed_model_id not in discovered:
        raise ValueError(
            f"Model {model_id!r} not found in discovered models. "
            f"Available: {sorted(str(k) for k in discovered)}"
        )
    model_instance = discovered[typed_model_id]
    station_code_resolver = _build_station_code_resolver(station_store)
    model_instance = adapt_if_fi(
        model_instance,
        station_code_resolver=station_code_resolver,
    )
    register_models({typed_model_id: model_instance}, model_store, clock)  # type: ignore[arg-type]

    with concurrency(f"model_training:{model_id}", occupy=1):
        # M.0: Determine onboarding scope
        units = _determine_onboarding_scope_task(
            model_id=typed_model_id,
            model=model_instance,
            station_ids=typed_station_ids,
            group_ids=typed_group_ids,
            station_store=station_store,
            group_store=group_store,
            training_period_start=parsed_start,
            training_period_end=parsed_end,
            time_step=time_step,
        )

        unit_results: list[OnboardingUnitResult] = []

        for unit in units:
            sid_str = str(unit.station_id) if unit.station_id else None
            gid_str = str(unit.group_id) if unit.group_id else None

            log.info(
                "model.onboarding_unit_started",
                station_id=sid_str,
                group_id=gid_str,
            )

            # M.2: Compatibility check
            compat = _validate_compatibility_task(
                model_id=typed_model_id,
                model=model_instance,
                unit=unit,
                station_store=station_store,
                group_store=group_store,
                basin_store=basin_store,
                parameter_store=parameter_store,
                deployment_config=deployment_config,
            )

            if not compat.is_compatible:
                log.info(
                    "model.compatibility_failed",
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
                station_id=sid_str,
                group_id=gid_str,
                is_compatible=True,
            )

            # M.2b: Smoke test
            try:
                _smoke_test_model_task(
                    model=model_instance,
                    deployment_config=deployment_config,
                    rng=rng,
                )
                log.info(
                    "model.smoke_test_completed",
                    station_id=sid_str,
                    group_id=gid_str,
                    passed=True,
                )
            except ModelSmokeTestError as exc:
                log.error(
                    "model.smoke_test_failed",
                    station_id=sid_str,
                    group_id=gid_str,
                    error=str(exc),
                )
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

            # M.3: Assemble training data
            data = _assemble_onboarding_data_task(
                unit=unit,
                model=model_instance,
                forcing_source=forcing_source,
                obs_store=obs_store,
                basin_store=basin_store,
                station_store=station_store,
                group_store=group_store,
            )

            if data is None:
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

            # M.3: Train the model artifact. Wrapped so a raise from THIS call
            # (the reanalysis-tail missing-value crash class, or the existing
            # insufficient-data ValueError) is recorded as FAILED_TRAINING and
            # onboarding continues for the remaining units, instead of
            # aborting the whole run (Plan 130 Part B; the older service path
            # model_onboarding.py already maps this failure mode to
            # FAILED_TRAINING). Deliberately scoped to the training call
            # only — storage + SHA-256 verification further down signal
            # artifact-store corruption, a system-level integrity failure
            # that must still abort the run loudly, not be swallowed
            # per-unit (mirrors train_models.py's T.3/store split).
            try:
                artifact_bytes = _train_onboarding_model_task(
                    unit=unit,
                    model=model_instance,
                    data=data,
                    rng=rng,
                )
            except Exception as exc:  # flow-level guard, see docstring above
                log.error(
                    "model.training_failed",
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

            # M.3 store: store the artifact in TRAINING status. Not wrapped —
            # a store failure aborts the run (see docstring above).
            artifact_id = _store_onboarding_artifact_task(
                unit=unit,
                artifact_bytes=artifact_bytes,
                artifact_store=artifact_store,
                clock=clock,
            )

            # Verify SHA-256 hash before deserializing artifact
            sha256_stored = artifact_store.fetch_artifact(artifact_id)
            if sha256_stored is not None:
                _, stored_bytes = sha256_stored
                computed_hash = hashlib.sha256(artifact_bytes).hexdigest()
                stored_hash = hashlib.sha256(stored_bytes).hexdigest()
                if computed_hash != stored_hash:
                    raise ValueError(
                        f"SHA-256 mismatch for artifact {artifact_id}: "
                        f"computed={computed_hash[:8]}... stored={stored_hash[:8]}..."
                    )

            # M.4: Hindcast (direct subflow from flow body)
            structlog.contextvars.bind_contextvars(
                parent_flow_run_id=str(prefect.runtime.flow_run.id)
            )
            loaded_artifact = model_instance.deserialize_artifact(artifact_bytes)
            hindcast_run_id = uuid4()

            hindcast_steps_raw = run_hindcast_flow(
                model_id=unit.model_id,
                artifact_id=artifact_id,
                station_id=unit.station_id,
                group_id=unit.group_id,
                period_start=unit.training_period_start,
                period_end=unit.training_period_end,
                time_step=unit.time_step,
                model=model_instance,
                artifact=loaded_artifact,
                forcing_source=forcing_source,
                obs_store=obs_store,
                hindcast_store=hindcast_store,
                station_store=station_store,
                group_store=group_store,
                basin_store=basin_store,
                clock=clock,
                rng=rng,
                hindcast_run_id=hindcast_run_id,
            )

            if isinstance(hindcast_steps_raw, dict):
                hindcast_steps = [
                    step for steps in hindcast_steps_raw.values() for step in steps
                ]
            else:
                hindcast_steps = list(hindcast_steps_raw)

            # M.5: Compute skills (task.map fan-out per station × parameter)
            # NOTE: task.map() with unmapped() store args requires ThreadPoolTaskRunner.
            # Stores hold SQLAlchemy connections that are not pickle-serializable.
            station_ids_for_skill: list[StationId] = (
                [unit.station_id]
                if unit.station_id is not None
                else list(unit.station_ids)
            )
            assert isinstance(
                model_instance, (StationForecastModel, GroupForecastModel)
            )
            target_parameters = model_instance.data_requirements.target_parameters
            skill_pairs = [
                (sid, param)
                for sid in station_ids_for_skill
                for param in sorted(target_parameters)
            ]
            futures = compute_skills_task.map(
                station_id=[sid for sid, _ in skill_pairs],
                model_id=unmapped(unit.model_id),
                artifact_id=unmapped(artifact_id),
                parameter=[param for _, param in skill_pairs],
                hindcast_run_id=unmapped(hindcast_run_id),
                hindcast_store=unmapped(hindcast_store),
                obs_store=unmapped(obs_store),
                skill_store=unmapped(skill_store),
                station_store=unmapped(station_store),
                flow_regime_store=unmapped(flow_regime_store),
                deployment_config=unmapped(deployment_config),
                clock=unmapped(clock),
            )
            [f.result() for f in futures]

            # M.5 gate: Evaluate skill gate
            skill_gate = _evaluate_skill_gate_task(
                model_id=typed_model_id,
                artifact_id=artifact_id,
                skill_store=skill_store,
                deployment_config=deployment_config,
            )

            # Insufficient evaluation data → distinguish from gate rejection
            if len(skill_gate.metric_scores) == 0 and not skill_gate.passed:
                log.warning(
                    "model.skill_gate_completed",
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
                station_id=sid_str,
                group_id=gid_str,
                passed=True,
                failing_metrics=[],
            )

            # M.6: Promote artifact TRAINING → ACTIVE
            _promote_artifact_task(
                unit=unit,
                artifact_id=artifact_id,
                artifact_store=artifact_store,
            )
            log.info(
                "model.promotion_completed",
                station_id=sid_str,
                group_id=gid_str,
                artifact_id=str(artifact_id),
            )

            # M.7: Create station or group assignment
            _create_assignment_task(
                unit=unit,
                model_id=typed_model_id,
                assignment_priority=resolved_assignment_priority,
                station_store=station_store,
                group_store=group_store,
                clock=clock,
            )

            log.info(
                "model.onboarding_unit_completed",
                station_id=sid_str,
                group_id=gid_str,
                outcome=OnboardingOutcome.PROMOTED.value,
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
        model_id=typed_model_id,
        units=tuple(unit_results),
    )
    log.info(
        "model.onboarding_completed",
        promoted_count=result.promoted_count(),
        failed_count=result.failed_count(),
        skipped_count=result.skipped_count(),
        gate_rejected_count=result.gate_rejected_count(),
    )
    return result
