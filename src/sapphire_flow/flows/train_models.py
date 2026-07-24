from __future__ import annotations

import hashlib
import os
import random
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from prefect import flow, runtime, task
from prefect.cache_policies import NO_CACHE
from prefect.utilities.annotations import unmapped

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.flows.run_hindcast import run_hindcast_flow
from sapphire_flow.protocols.forecast_model import (
    GroupForecastModel,
    StationForecastModel,
)
from sapphire_flow.services.model_registry import discover_models, register_models
from sapphire_flow.services.scope import determine_training_scope
from sapphire_flow.services.training import (
    store_and_promote_artifact,
    train_group_model,
    train_station_model,
)
from sapphire_flow.services.training_data import (
    assemble_group_training_data,
    assemble_station_training_data,
)
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.training import TrainingResult, TrainingScope, TrainingUnit

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        HistoricalForcingStore,
        ModelArtifactStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.store.audited_writer import AuditedWriter
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.write_principal import WritePrincipal

log = structlog.get_logger(__name__)


def _unit_shard(unit: TrainingUnit) -> str:
    return str(unit.station_id) if unit.station_id is not None else str(unit.group_id)


def _resolve_assemble_data_run_name() -> str:
    params = runtime.task_run.parameters or {}
    unit = params["unit"]
    return f"assemble-data-{_unit_shard(unit)}"


def _resolve_train_model_run_name() -> str:
    params = runtime.task_run.parameters or {}
    unit = params["unit"]
    return f"train-model-{_unit_shard(unit)}"


def _resolve_store_artifact_run_name() -> str:
    params = runtime.task_run.parameters or {}
    unit = params["unit"]
    return f"store-artifact-{_unit_shard(unit)}"


def _resolve_train_models_run_name() -> str:
    from datetime import datetime

    params = runtime.flow_run.parameters or {}
    period_start = params.get("period_start")
    period_end = params.get("period_end")
    if period_start is None:
        scheduled = runtime.flow_run.scheduled_start_time
        return f"train-{scheduled:%Y%m%d}"
    start_dt = datetime.fromisoformat(period_start)
    if period_end is None:
        return f"train-{start_dt:%Y%m%d}"
    end_dt = datetime.fromisoformat(period_end)
    return f"train-{start_dt:%Y%m%d}-{end_dt:%Y%m%d}"


@task(name="determine-scope", task_run_name="determine-scope", cache_policy=NO_CACHE)
def _determine_scope_task(
    model_ids: list[ModelId] | None,
    station_ids: list[StationId] | None,
    group_ids: list[StationGroupId] | None,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    model_store: object,
    station_store: object,
    group_store: object,
) -> object:
    return determine_training_scope(
        model_ids=model_ids,
        station_ids=station_ids,
        group_ids=group_ids,
        period_start=period_start,
        period_end=period_end,
        time_step=time_step,
        model_store=model_store,
        station_store=station_store,
        group_store=group_store,
    )


@task(
    name="assemble-training-data",
    task_run_name=_resolve_assemble_data_run_name,
    cache_policy=NO_CACHE,
)
def _assemble_data_task(
    unit: TrainingUnit,
    model: object,
    forcing_source: object,
    obs_store: object,
    basin_store: object,
    station_store: object,
    group_store: object,
) -> object:
    if unit.station_id is not None:
        return assemble_station_training_data(
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
        if group is None:
            return None
        return assemble_group_training_data(
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


@task(
    name="train-model",
    task_run_name=_resolve_train_model_run_name,
    cache_policy=NO_CACHE,
)
def _train_model_task(
    unit: TrainingUnit,
    model: object,
    data: object,
    rng: random.Random,
) -> bytes:
    params: dict = {}
    if unit.station_id is not None:
        return train_station_model(model=model, data=data, params=params, rng=rng)
    else:
        return train_group_model(model=model, data=data, params=params, rng=rng)


@task(
    name="store-artifact",
    task_run_name=_resolve_store_artifact_run_name,
    cache_policy=NO_CACHE,
)
def _store_artifact_task(
    unit: TrainingUnit,
    artifact_bytes: bytes,
    artifact_store: object,
    clock: Callable[[], UtcDatetime],
    station_store: object = None,
    group_store: object = None,
    principal: object = None,
    audit_log_store: object = None,
    audited_writer: object = None,
) -> object:
    from typing import cast

    typed_principal = cast("WritePrincipal | None", principal)
    typed_audit = cast("AuditLogStore | None", audit_log_store)
    target_tenant_id = None
    if typed_principal is not None and typed_principal.tenant_id is not None:
        from sapphire_flow.services.write_principal import target_tenant_id_for_unit

        target_tenant_id = target_tenant_id_for_unit(
            unit,
            cast("StationStore", station_store),
            cast("StationGroupStore", group_store),
        )

    def _run(store: object, audit: object, *, audit_rejection: bool) -> object:
        return store_and_promote_artifact(
            artifact_store=cast("ModelArtifactStore", store),
            model_id=unit.model_id,
            artifact_bytes=artifact_bytes,
            period_start=unit.training_period_start,
            period_end=unit.training_period_end,
            clock=clock,
            station_id=unit.station_id,
            group_id=unit.group_id,
            principal=typed_principal,
            target_tenant_id=target_tenant_id,
            audit_log_store=cast("AuditLogStore | None", audit),
            audit_rejection=audit_rejection,
        )

    # Plan 147 Slice E: the store-artifact + promote mutations and their
    # MODEL_PROMOTED audit row run in ONE real transaction so a failed audit
    # insert rolls the domain write back. `audited_writer is None` only in
    # test/replay wiring (caller-injected fake stores) — keep the direct
    # AUTOCOMMIT path there. train_models_flow pre-filters foreign-tenant units
    # BEFORE this task (see `authorized_units`), so store_and_promote's own
    # tenant check inside the txn never rejects — no separate pre-authorize is
    # needed here (unlike the onboard flow, which has no pre-filter).
    writer = cast("AuditedWriter | None", audited_writer)
    if writer is None or typed_audit is None:
        return _run(artifact_store, typed_audit, audit_rejection=True)
    # Inside the atomic txn: the internal tenant check is RAISE-ONLY (no
    # rejection row written into a rollback-able txn — residual BLOCKER 3).
    with writer.transaction() as stores:
        return _run(
            stores["artifact_store"], stores["audit_log_store"], audit_rejection=False
        )


@task(
    name="record-artifact-lineage",
    cache_policy=NO_CACHE,
)
def _record_lineage_task(
    lineage_writer: object,
    artifact_id: object,
    trained_station_ids: object,
) -> None:
    """Plan 120 Task 2D: write `model_artifact_basin_versions` rows right
    after the artifact is stored (and, on the training path, promoted).
    NON-ATOMIC and LOG-LOUD on failure — see `store/model_artifact_lineage.py`
    docstring for the full rationale. `lineage_writer is None` only in
    non-production wiring that has no DB to write to; that path is
    intentionally a no-op, not a swallowed failure."""
    if lineage_writer is None:
        return
    lineage_writer.record(artifact_id, trained_station_ids)  # type: ignore[attr-defined]


@flow(
    name="train-models",
    log_prints=False,
    flow_run_name=_resolve_train_models_run_name,
)
def train_models_flow(
    model_ids: list[str] | None = None,
    station_ids: list[str] | None = None,
    group_ids: list[str] | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    time_step_hours: int = 24,
    model_store: object = None,
    station_store: object = None,
    group_store: object = None,
    obs_store: object = None,
    basin_store: object = None,
    artifact_store: object = None,
    hindcast_store: object = None,
    skill_store: object = None,
    flow_regime_store: object = None,
    forcing_store: object = None,
    forcing_source: object = None,
    lineage_writer: object = None,
    models: dict | None = None,
    clock: object = None,
    rng: object = None,
    deployment_config: object = None,
    tenant_store: object = None,
    audit_log_store: object = None,
) -> list[TrainingResult]:
    from datetime import UTC, datetime
    from typing import cast
    from uuid import UUID

    from sapphire_flow.services.write_principal import (
        resolve_flow_run_principal,
        target_tenant_id_for_unit,
    )
    from sapphire_flow.types.auth import AuditEntry
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.enums import AuditEventType

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
        forcing_store = stores["forcing_store"]
        if lineage_writer is None:
            lineage_writer = stores["lineage_writer"]
        if tenant_store is None:
            tenant_store = stores.get("tenant_store")
        if audit_log_store is None:
            audit_log_store = stores.get("audit_log_store")

    # Plan 147 Slice E: the real-transaction seam for the discrete audited
    # write (_store_artifact_task) — built only on the production DB-backed
    # path (a bootstrapped `_conn` exists); None for caller-injected stores,
    # which keep the direct AUTOCOMMIT path.
    audited_writer: object = None
    if _conn is not None:
        from sapphire_flow.store.audited_writer import make_audited_writer

        audited_writer = make_audited_writer(_conn)

    # Plan 147 Slice E (G3/G6): the SCHEDULED flow's SINGLE config-selected
    # run principal, resolved BEFORE _determine_scope_task selects any unit
    # (never from unit.station_id/unit.group_id). None (no enforcement) only
    # in test/replay wiring with no tenant_store/SAPPHIRE_CONFIG — the
    # scheduled deployment always has both.
    run_principal = resolve_flow_run_principal(
        tenant_store=tenant_store,  # type: ignore[arg-type]
        tenant_code=None,
        operator=None,
    )

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
    # allowed when forcing_store is unavailable (e.g. caller-provided stores
    # without one) — empty-scope training short-circuits without
    # dereferencing forcing_source.
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

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    if rng is None:
        rng = random.Random()

    # Parse period
    if period_start is not None:
        parsed_start: UtcDatetime = ensure_utc(datetime.fromisoformat(period_start))
    else:
        now = clock()
        parsed_start = ensure_utc(
            datetime(now.year - 5, now.month, now.day, tzinfo=UTC)
        )

    if period_end is not None:
        parsed_end: UtcDatetime = ensure_utc(datetime.fromisoformat(period_end))
    else:
        parsed_end = clock()

    time_step = timedelta(hours=time_step_hours)

    typed_model_ids: list[ModelId] | None = (
        [ModelId(m) for m in model_ids] if model_ids is not None else None
    )
    typed_station_ids: list[StationId] | None = None
    if station_ids is not None:
        typed_station_ids = [StationId(UUID(s)) for s in station_ids]
    typed_group_ids: list[StationGroupId] | None = None
    if group_ids is not None:
        typed_group_ids = [StationGroupId(UUID(g)) for g in group_ids]

    # Discover and register models if not pre-provided
    if models is None:
        discovered = discover_models()
        register_models(discovered, model_store, clock)
        models = discovered
    else:
        register_models(models, model_store, clock)

    # T.1: determine scope
    scope = _determine_scope_task(
        model_ids=typed_model_ids,
        station_ids=typed_station_ids,
        group_ids=typed_group_ids,
        period_start=parsed_start,
        period_end=parsed_end,
        time_step=time_step,
        model_store=model_store,
        station_store=station_store,
        group_store=group_store,
    )

    results: list[TrainingResult] = []

    # Plan 147 Slice E (G3/G6): filter to the run principal's tenant AFTER
    # scope selection but BEFORE any unit is trained/promoted — a
    # foreign-tenant unit is skipped-with-audit, never trained. The
    # principal (resolved above, before scope selection) never derives from
    # a unit's station_id/group_id.
    if run_principal is not None and run_principal.tenant_id is not None:
        typed_station_store = cast("StationStore", station_store)
        typed_group_store = cast("StationGroupStore", group_store)
        typed_clock = cast("Callable[[], UtcDatetime]", clock)
        typed_audit_log_store = cast("AuditLogStore | None", audit_log_store)

        authorized_units = []
        for unit in scope.units:
            unit_tenant_id = target_tenant_id_for_unit(
                unit, typed_station_store, typed_group_store
            )
            if unit_tenant_id is not None and unit_tenant_id != run_principal.tenant_id:
                log.warning(
                    "train_models.unit_skipped_foreign_tenant",
                    unit=_unit_shard(unit),
                    model_id=str(unit.model_id),
                    principal_tenant_id=str(run_principal.tenant_id),
                    unit_tenant_id=str(unit_tenant_id),
                )
                if typed_audit_log_store is not None:
                    typed_audit_log_store.append_entry(
                        AuditEntry.system(
                            event_type=AuditEventType.MODEL_REJECTED,
                            target_type="training_unit",
                            target_id=_unit_shard(unit),
                            detail={
                                "model_id": str(unit.model_id),
                                "outcome": "skipped_foreign_tenant",
                                "operator": run_principal.id,
                                "principal_tenant_id": str(run_principal.tenant_id),
                                "unit_tenant_id": str(unit_tenant_id),
                            },
                            ip_address=None,
                            created_at=typed_clock(),
                        )
                    )
                results.append(
                    TrainingResult(
                        training_unit=unit,
                        artifact_id=None,
                        hindcast_steps=[],
                        skill_computed=False,
                        error=(
                            "tenant isolation: unit belongs to a different "
                            "tenant than the run principal"
                        ),
                    )
                )
                continue
            authorized_units.append(unit)
        scope = TrainingScope(units=tuple(authorized_units))

    for unit in scope.units:
        model_instance = models.get(unit.model_id)
        if model_instance is None:
            results.append(
                TrainingResult(
                    training_unit=unit,
                    artifact_id=None,
                    hindcast_steps=[],
                    skill_computed=False,
                    error=f"model {unit.model_id} not found in discovered models",
                )
            )
            continue

        # T.2: assemble training data
        data = _assemble_data_task(
            unit=unit,
            model=model_instance,
            forcing_source=forcing_source,
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
            group_store=group_store,
        )

        if data is None:
            results.append(
                TrainingResult(
                    training_unit=unit,
                    artifact_id=None,
                    hindcast_steps=[],
                    skill_computed=False,
                    error="insufficient data",
                )
            )
            continue

        # T.3: train the model artifact. Wrapped so a raise from THIS call (the
        # reanalysis-tail missing-value crash class, or the existing
        # insufficient-data ValueError) is recorded as a failed unit and the
        # run CONTINUES for the remaining units, instead of aborting the whole
        # flow (Plan 130 Part B). Deliberately scoped to the training call
        # only — a SHA-256 mismatch further down signals artifact-store
        # corruption, a system-level integrity failure that must still abort
        # the run loudly, not be swallowed per-unit.
        try:
            artifact_bytes = _train_model_task(
                unit=unit,
                model=model_instance,
                data=data,
                rng=rng,
            )
        except Exception as exc:  # flow-level guard, see docstring above
            log.error(
                "train_models.unit_training_failed",
                unit=_unit_shard(unit),
                model_id=str(unit.model_id),
                error=str(exc),
            )
            results.append(
                TrainingResult(
                    training_unit=unit,
                    artifact_id=None,
                    hindcast_steps=[],
                    skill_computed=False,
                    error=str(exc),
                )
            )
            continue

        artifact_id = _store_artifact_task(
            unit=unit,
            artifact_bytes=artifact_bytes,
            artifact_store=artifact_store,
            clock=clock,
            station_store=station_store,
            group_store=group_store,
            principal=run_principal,
            audit_log_store=audit_log_store,
            audited_writer=audited_writer,
        )

        # Plan 120 Task 2D: lineage AFTER store + promote. Trained subset —
        # {unit.station_id} for a station-scoped unit, data.station_ids (the
        # post-skip subset) for a group-scoped one, NOT unit.station_ids
        # (the full pre-skip membership).
        trained_station_ids: tuple[StationId, ...] = (
            (unit.station_id,)
            if unit.station_id is not None
            else tuple(data.station_ids)  # type: ignore[union-attr]
        )
        _record_lineage_task(
            lineage_writer=lineage_writer,
            artifact_id=artifact_id,
            trained_station_ids=trained_station_ids,
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

        # Deserialize artifact for hindcast
        loaded_artifact = model_instance.deserialize_artifact(artifact_bytes)
        hindcast_run_id = uuid4()

        # T.4: run hindcast (as subflow)
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

        # Flatten hindcast results to list[HindcastStepResult]
        if isinstance(hindcast_steps_raw, dict):
            hindcast_steps = [
                step for steps in hindcast_steps_raw.values() for step in steps
            ]
        else:
            hindcast_steps = hindcast_steps_raw

        # T.5: compute skills per station × parameter (task.map fan-out)
        # NOTE: task.map() with unmapped() store args requires in-process task runner
        # (ThreadPoolTaskRunner). Stores hold SQLAlchemy connections that are not
        # pickle-serializable — distributed/subprocess runners would fail.
        skill_computed = False
        station_ids_for_skill: list[StationId] = (
            [unit.station_id] if unit.station_id is not None else list(unit.station_ids)
        )

        assert isinstance(model_instance, (StationForecastModel, GroupForecastModel))
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
        skill_results = [f.result() for f in futures]
        skill_computed = any(scores for scores, _ in skill_results)

        results.append(
            TrainingResult(
                training_unit=unit,
                artifact_id=artifact_id,
                hindcast_steps=hindcast_steps,
                skill_computed=skill_computed,
                error=None,
            )
        )

    return results
