from __future__ import annotations

import hashlib
import json
import os
import random
from contextlib import nullcontext
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

import structlog
from prefect import flow, runtime, task
from prefect.cache_policies import NO_CACHE
from prefect.utilities.annotations import unmapped

from sapphire_flow.exceptions import ArtifactIntegrityError, ConfigurationError
from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.flows.run_hindcast import run_hindcast_flow
from sapphire_flow.protocols.forecast_model import (
    GroupForecastModel,
    StationForecastModel,
)
from sapphire_flow.services.fine_tuning import qualify_fine_tuning_scope
from sapphire_flow.services.fine_tuning_evidence import (
    CandidateReceipt,
    RecordingReanalysisSource,
)
from sapphire_flow.services.model_registry import (
    build_station_code_resolver,
    discover_models,
    register_models,
)
from sapphire_flow.services.scope import determine_training_scope
from sapphire_flow.services.training import (
    retrain_group_model,
    retrain_station_model,
    store_and_promote_artifact,
    train_group_model,
    train_station_model,
)
from sapphire_flow.services.training_data import (
    assemble_group_training_data,
    assemble_station_training_data,
)
from sapphire_flow.store.fine_tuning import PgFineTuningReceipts, fitting_lock
from sapphire_flow.types.enums import ModelArtifactStatus
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.training import TrainingResult, TrainingScope, TrainingUnit

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    import sqlalchemy as sa

    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        HistoricalForcingStore,
        ModelArtifactStore,
        SkillStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.services.fine_tuning import FineTuningScope
    from sapphire_flow.store.audited_writer import AuditedWriter
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.model import (
        GroupTrainingData,
        ModelParams,
        StationTrainingData,
    )
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
) -> TrainingScope:
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


def _resolve_donor_config_or_refuse(
    *,
    warm_start_writer: object,
    base_artifact_id: ArtifactId,
    model: object,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Plan 405 T1 — resolve the donor's provenance BEFORE training, and refuse here.

    ⭐ **Why before.** The resolved triple has to satisfy `WarmStartRecord`'s
    invariant, and that was previously discovered by CONSTRUCTING the record —
    after a successful train and a stored artifact. A `ValueError` there costs
    the whole training run and leaves an artifact whose provenance was refused.
    Validating the same triple up front means the raise cannot happen late: at
    this point nothing is trained and nothing is stored.

    ⛔ Not by constructing the record — `artifact_id` does not exist yet. It is
    the shared invariant that is applied (`check_config_provenance`), never a
    hand-rolled copy of it.

    The donor's config identity comes from the DONOR's own provenance, never
    from hashing whatever template is installed now (§ 13).

    ⭐ **Plan 405 T2 — the installed path and hash are now REAL and COMPARED.**
    Both are read off the model (`config_path` / `config_hash`, proxied through
    the FI adapter), and `resolve_donor_config` refuses with
    `ConfigurationError` when the installed template's hash differs from the
    one the donor was built from. ⇒ TWO refusals happen here, both before any
    training: the changed-template mismatch, and the shared invariant below.

    ⚠️ A model that declares neither yields `None` for both, and the resolver
    then records NULL WITH A REASON rather than guessing — a missing hash is not
    a mismatch.

    ⭐ **Plan 405 T6 — the PARAMS half is resolved and checked here too.** Until T6
    the params reason was a hardcoded non-empty constant built at the record site,
    which made checking it pointless; now it varies per donor class, so an empty
    one would bring back the exact late crash T1 removed — after a successful
    train and a stored artifact — through the params column instead.
    ⛔ *Chosen over "guarantee every class returns text": that guarantee rests on
    reading FIVE branches and being right about all of them, and this plan's whole
    history is me being wrong about claims of that shape. The check costs one line
    and removes the question.* ⚠️ *Said "three" here at first — the resolver's five
    branches, confused with D1's three donor CLASSES, in a docstring added by the
    same commit whose message boasted of catching that exact one-site slip
    elsewhere.*
    """
    from sapphire_flow.store.model_artifact_warm_start import (
        check_config_provenance,
        check_params_provenance,
    )

    installed_hash = getattr(model, "config_hash", None)
    installed_path = getattr(model, "config_path", None)
    path, sha256, reason = warm_start_writer.resolve_donor_config(  # type: ignore[attr-defined]
        base_artifact_id,
        installed_config_path=installed_path,
        installed_config_sha256=installed_hash,
    )
    check_config_provenance(path, reason)
    params_path, params_reason = warm_start_writer.resolve_donor_params(  # type: ignore[attr-defined]
        base_artifact_id
    )
    check_params_provenance(params_path, params_reason)
    return path, sha256, reason, params_path, params_reason


def _record_warm_start_provenance(
    *,
    warm_start_writer: object,
    artifact_id: ArtifactId,
    base_artifact_id: ArtifactId,
    donor_config: tuple[str | None, str | None, str | None, str | None, str | None],
    run_config: dict,
) -> None:
    """Plan 399 T3/T4 — record what this artifact was fine-tuned FROM.

    ⛔ Plan 405 T1 — `donor_config` carries values ALREADY resolved and validated
    before training. They are threaded in, never re-resolved: re-resolving here
    would move the raise rather than remove it, and the record would be built
    from a second read that is not the one the refusal decision was made on.

    ⭐ Plan 405 T6 — the params reason is now PER DONOR CLASS and arrives the same
    way. ⛔ *It was one hardcoded sentence for every donor, and § 4 measured that
    sentence already false: it called a retrain's configuration unknown while that
    configuration sat in our own row.*
    """
    from sapphire_flow.store.model_artifact_warm_start import WarmStartRecord

    path, sha256, reason, params_path, params_reason = donor_config
    warm_start_writer.record(  # type: ignore[attr-defined]
        WarmStartRecord(
            artifact_id=artifact_id,
            base_artifact_id=base_artifact_id,
            run_config=dict(run_config),
            base_config_path=path,
            base_config_sha256=sha256,
            base_config_unknown_reason=reason,
            base_params_path=params_path,
            base_params_unknown_reason=params_reason,
        )
    )


def _load_base_artifact(
    model: object,
    artifact_store: object,
    base_artifact_id: ArtifactId,
) -> object:
    """Plan 399 T3 — load the donor, from the FETCHED bytes.

    ⛔ `fetch_artifact` returns `(ArtifactId, bytes) | None`; `None` is a real
    return value for an unknown id and is REFUSED here rather than allowed to
    surface as an obscure failure later. And the artifact is deserialized from
    the bytes the STORE returned — not from anything held in memory, which is
    what the neighbouring integrity check does and is not a pattern to copy.
    """
    store = cast("ModelArtifactStore", artifact_store)
    fetched = store.fetch_artifact(base_artifact_id)
    if fetched is None:
        raise ConfigurationError(
            f"base artifact {base_artifact_id} not found; refusing to retrain "
            "(Plan 399 T3)"
        )
    _, stored_bytes = fetched

    # SHA-256 guard before deserializing. `PgModelArtifactStore.fetch_artifact`
    # already verifies internally, but this is a NEW flow-level deserialization
    # path and `tests/unit/test_hash_verification_coverage.py` is right to
    # require the check here rather than have it depend on a store's internals:
    # a caller-injected store need not verify, and a donor's integrity matters
    # MORE than usual because a whole new model gets built on top of it.
    record = store.fetch_artifact_record(base_artifact_id)
    if record is not None:
        computed = hashlib.sha256(stored_bytes).hexdigest()
        if computed != record.sha256_hash:
            raise ArtifactIntegrityError(
                f"base artifact {base_artifact_id} failed its SHA-256 check "
                f"(computed {computed[:8]}…, stored {record.sha256_hash[:8]}…); "
                "refusing to retrain from it"
            )

    return model.deserialize_artifact(stored_bytes)  # type: ignore[attr-defined]


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
    params: ModelParams | None = None,
    base_artifact: object = None,
) -> bytes:
    # Plan 399 T2 — the caller's training config. Was hardcoded `{}` here, so
    # NO model could ever receive configuration; a fine-tune cannot select a
    # strategy without this. `None` keeps every existing caller's behaviour.
    params = {} if params is None else params
    # Plan 399 T3 — a named donor routes to retrain. ⛔ No fall-back: a model
    # without warm-start support raises WarmStartUnsupportedError from the
    # service (D2), it does NOT quietly train from scratch.
    if base_artifact is not None:
        if unit.station_id is not None:
            return retrain_station_model(
                model=cast("StationForecastModel", model),
                base_artifact=base_artifact,
                data=cast("StationTrainingData", data),
                params=params,
                rng=rng,
                model_id=str(unit.model_id),
            )
        return retrain_group_model(
            model=cast("GroupForecastModel", model),
            base_artifact=base_artifact,
            data=cast("GroupTrainingData", data),
            params=params,
            rng=rng,
            model_id=str(unit.model_id),
        )
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
    promote: bool = True,
    receipt_for_artifact: object = None,
    candidate_provenance: object = None,
) -> object:

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
        if not promote:
            # Plan 399 T3 — a retrained artifact is stored, NOT promoted. The
            # current artifact keeps serving until a human decides otherwise;
            # promoting here would put an unevaluated fine-tune into service.
            aid, _ = cast("ModelArtifactStore", store).store_artifact(
                unit.model_id,
                artifact_bytes,
                unit.training_period_start,
                unit.training_period_end,
                clock(),
                station_id=unit.station_id,
                group_id=unit.group_id,
            )
            if receipt_for_artifact is not None:
                from sapphire_flow.types.auth import AuditEntry
                from sapphire_flow.types.enums import AuditEventType

                factory = cast(
                    "Callable[[ArtifactId], CandidateReceipt]", receipt_for_artifact
                )
                receipt = factory(aid)
                if audit is None:
                    raise ConfigurationError(
                        "fine-tuning candidate requires atomic audit storage"
                    )
                cast("AuditLogStore", audit).append_entry(
                    AuditEntry.system(
                        event_type=AuditEventType.FINE_TUNING_RECEIPT,
                        target_type="model_artifact",
                        target_id=str(aid),
                        detail={"receipt": receipt.payload(), "sha256": receipt.sha256},
                        ip_address=None,
                        created_at=clock(),
                    )
                )
            return aid
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
    if receipt_for_artifact is not None:
        from sapphire_flow.store.audited_writer import make_audited_stores

        if writer is None or typed_audit is None or candidate_provenance is None:
            raise ConfigurationError("fine-tuning requires atomic provenance storage")
        with writer.begin() as connection:
            stores = make_audited_stores(connection)
            aid = cast(
                "ArtifactId",
                _run(
                    stores["artifact_store"],
                    stores["audit_log_store"],
                    audit_rejection=False,
                ),
            )
            finalize = cast(
                "Callable[[ArtifactId, sa.Connection], None]", candidate_provenance
            )
            finalize(aid, connection)
            return aid
    if writer is None or typed_audit is None:
        return _run(artifact_store, typed_audit, audit_rejection=True)
    # Inside the atomic txn: the internal tenant check is RAISE-ONLY (no
    # rejection row written into a rollback-able txn — residual BLOCKER 3).
    with writer.transaction() as stores:
        return _run(
            stores["artifact_store"], stores["audit_log_store"], audit_rejection=False
        )


def _record_candidate_provenance(
    artifact_id: ArtifactId,
    connection: sa.Connection,
    *,
    base_artifact_id: ArtifactId,
    donor_config: tuple[str | None, str | None, str | None, str | None, str | None],
    run_config: dict,
    station_ids: tuple[StationId, ...],
) -> None:
    from sapphire_flow.store.model_artifact_lineage import PgArtifactLineageWriter
    from sapphire_flow.store.model_artifact_warm_start import PgWarmStartWriter

    _record_warm_start_provenance(
        warm_start_writer=PgWarmStartWriter(connection),
        artifact_id=artifact_id,
        base_artifact_id=base_artifact_id,
        donor_config=donor_config,
        run_config=run_config,
    )
    PgArtifactLineageWriter(connection).record(artifact_id, station_ids)


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
    # Plan 399 T2 (D3) — supplied per run, opaque to SAP3 and passed through to
    # the model unchanged. Omitted, every model sees `{}` exactly as before.
    training_params: dict | None = None,
    base_artifact_id: str | None = None,
    fine_tuning: dict[str, object] | None = None,
    fine_tuning_candidate_id: str | None = None,
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
    warm_start_writer: object = None,
    models: dict | None = None,
    clock: object = None,
    rng: object = None,
    deployment_config: object = None,
    tenant_store: object = None,
    audit_log_store: object = None,
) -> list[TrainingResult]:
    from datetime import UTC, datetime
    from uuid import UUID

    from sapphire_flow.services.write_principal import (
        resolve_flow_run_principal,
        target_tenant_id_for_unit,
    )
    from sapphire_flow.types.auth import AuditEntry
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.enums import AuditEventType

    experiment = None
    if fine_tuning_candidate_id is not None and fine_tuning is None:
        raise ConfigurationError(
            "evaluation retry requires the original fine-tuning configuration"
        )
    if fine_tuning is not None:
        from sapphire_flow.config.fine_tuning import parse_fine_tuning

        experiment = parse_fine_tuning(fine_tuning)
        if any(
            value is not None
            for value in (
                model_ids,
                station_ids,
                group_ids,
                period_start,
                period_end,
                training_params,
                base_artifact_id,
            )
        ):
            raise ConfigurationError(
                "configured fine-tuning cannot mix legacy run parameters"
            )
        model_ids = [str(experiment.model_id)]
        group_ids = [str(experiment.group_id)]
        base_artifact_id = str(experiment.base_artifact_id)
        period_start = experiment.fitting_start.isoformat()
        period_end = experiment.fitting_end.isoformat()
        time_step_hours = experiment.time_step_hours
        training_params = json.loads(experiment.training_parameters_json)
        rng = random.Random(experiment.seed)

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
    # Plan 399 T3 — the warm-start provenance writer, wired on the production
    # DB path exactly like `lineage_writer`. ⛔ Without this the recorder T4
    # ships has no caller, which is the defect the plan named against itself.
    if warm_start_writer is None and _conn is not None:
        from sapphire_flow.store.model_artifact_warm_start import PgWarmStartWriter

        warm_start_writer = PgWarmStartWriter(_conn)

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
        tenant_code=experiment.tenant_code if experiment else None,
        operator=experiment.operator if experiment else None,
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
    if experiment is not None and forcing_store is None:
        raise ConfigurationError(
            "configured fine-tuning requires the historical forcing store"
        )
    if (forcing_source is None or experiment is not None) and forcing_store is not None:
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            select_reanalysis_source,
        )
        from sapphire_flow.config.deployment import DeploymentConfig

        resolved_config = cast("DeploymentConfig", deployment_config)
        forcing_source = select_reanalysis_source(
            forcing_store=cast("HistoricalForcingStore", forcing_store),
            mode=cast(
                'Literal["single", "hybrid", "era5_land", "recap_era5_land"]',
                experiment.weather_source.value,
            )
            if experiment
            else resolved_config.reanalysis_source,
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

    time_step = timedelta(hours=time_step_hours)

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
        # Plan 228 T5: snap the default end to the last COMPLETE bucket
        # boundary at or before now — a raw `clock()` instant produces the
        # same partial trailing bucket D4 forbids everywhere else. An
        # explicitly supplied `period_end` (above) is the caller's business
        # and passes through unchanged.
        from sapphire_flow.services.training_data import floor_to_time_step

        parsed_end = floor_to_time_step(clock(), time_step)

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

    # Plan 399 T3 (§ 11) — attach the station-code resolver that GROUP paths
    # require. `discover_models()` wraps FI models with NO resolver, and the
    # adapter raises ConfigurationError for every GROUP input conversion, train
    # and predict without one. That is why group training has never produced an
    # artifact on any deployment: it could not. Attaching it here fixes group
    # TRAIN as well as retrain — a station-scoped model is unaffected, since it
    # never reaches the resolver.
    #
    # ⚠️ Placement matters: this MUST sit after the models-are-registered
    # if/else above. Inserted between them it captures the `else`, so
    # registration silently stops running whenever a station store IS supplied.
    if station_store is not None:
        resolver = build_station_code_resolver(cast("StationStore", station_store))
        for _model in models.values():
            attach = getattr(_model, "with_station_code_resolver", None)
            if callable(attach):
                attach(resolver)

    # Plan 399 T3 — resolve the donor ONCE if one was named. `None` means
    # ordinary training, which is the overwhelmingly common case and must be
    # completely unaffected.
    typed_base_artifact_id: ArtifactId | None = (
        ArtifactId(UUID(base_artifact_id)) if base_artifact_id else None
    )

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

    if experiment is not None:
        if run_principal is None:
            raise ConfigurationError(
                "configured fine-tuning requires a trusted run principal"
            )
        if len(scope.units) != 1 or scope.units[0].group_id != experiment.group_id:
            raise ConfigurationError(
                "configured fine-tuning must resolve exactly its requested group"
            )
        selected_model = models.get(experiment.model_id)
        if selected_model is None:
            raise ConfigurationError("fine-tuning model was not discovered")
        qualify_fine_tuning_scope(
            experiment,
            principal=run_principal,
            groups=cast("StationGroupStore", group_store),
            stations=cast("StationStore", station_store),
            artifacts=cast("ModelArtifactStore", artifact_store),
            model=selected_model,
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

    guard: AbstractContextManager[None] = nullcontext()
    receipts: PgFineTuningReceipts | None = None
    recording_source: RecordingReanalysisSource | None = None
    qualified: FineTuningScope | None = None
    incumbent_ids: tuple[ArtifactId, ...] = ()
    selected_model = models.get(experiment.model_id) if experiment else None
    finish_evaluation: (
        Callable[[CandidateReceipt, TrainingUnit, object], TrainingResult] | None
    ) = None
    if experiment is not None:
        if _conn is None or run_principal is None or run_principal.tenant_id is None:
            raise ConfigurationError(
                "configured fine-tuning requires production receipt storage"
            )
        connection = _conn
        receipts = PgFineTuningReceipts(connection)
        guard = fitting_lock(connection.engine, experiment, run_principal.tenant_id)
        recording_source = RecordingReanalysisSource(
            cast("WeatherReanalysisSource", forcing_source)
        )
        forcing_source = recording_source

        def _finish_evaluation(
            receipt: CandidateReceipt, unit: TrainingUnit, model: object
        ) -> TrainingResult:
            from dataclasses import replace

            from sapphire_flow.config.paths import resolve_artifact_dir
            from sapphire_flow.flows.fine_tuning_evaluation import evaluate_candidate
            from sapphire_flow.services.fine_tuning_evidence import canonical_json
            from sapphire_flow.services.hindcast import (
                resolve_hindcast_issue_batch_size,
            )
            from sapphire_flow.store.model_artifact_store import PgModelArtifactStore

            now = cast("Callable[[], UtcDatetime]", clock)
            candidate_run_id, donor_run_id = uuid4(), uuid4()
            attempt = {
                "state": "running",
                "issue_batch_size": resolve_hindcast_issue_batch_size(model),
                "candidate_run_id": str(candidate_run_id),
                "donor_run_id": str(donor_run_id),
            }
            with connection.engine.begin() as tx:
                PgFineTuningReceipts(tx).append(
                    replace(receipt, evaluation_json=canonical_json(attempt)),
                    now(),
                )
            try:
                updated, result = evaluate_candidate(
                    receipt,
                    unit=unit,
                    candidate_run_id=candidate_run_id,
                    donor_run_id=donor_run_id,
                    model=cast("GroupForecastModel", model),
                    artifacts=cast("ModelArtifactStore", artifact_store),
                    source=recording_source,
                    obs_store=obs_store,
                    hindcast_store=hindcast_store,
                    station_store=station_store,
                    group_store=group_store,
                    basin_store=basin_store,
                    skill_store=cast("SkillStore", skill_store),
                    flow_regime_store=flow_regime_store,
                    deployment_config=deployment_config,
                    clock=cast("Callable[[], UtcDatetime]", clock),
                )
            except Exception as exc:
                attempt.update(state="incomplete", error_type=type(exc).__name__)
                with connection.engine.begin() as tx:
                    PgFineTuningReceipts(tx).append(
                        replace(receipt, evaluation_json=canonical_json(attempt)),
                        now(),
                    )
                raise
            with connection.engine.begin() as tx:
                PgFineTuningReceipts(tx).append(
                    updated, cast("Callable[[], UtcDatetime]", clock)()
                )
                if result.skill_computed:
                    PgModelArtifactStore(
                        tx, resolve_artifact_dir()
                    ).transition_artifact_status(
                        receipt.artifact_id,
                        ModelArtifactStatus.PENDING_APPROVAL,
                    )
            return result

        finish_evaluation = _finish_evaluation

    with guard:
        if experiment is not None:
            assert run_principal is not None
            assert receipts is not None
            # Recheck current ownership and roster after acquiring the scope lock.
            qualified = qualify_fine_tuning_scope(
                experiment,
                principal=run_principal,
                groups=cast("StationGroupStore", group_store),
                stations=cast("StationStore", station_store),
                artifacts=cast("ModelArtifactStore", artifact_store),
                model=selected_model,
            )
            if qualified.group.station_ids != scope.units[0].station_ids:
                raise ConfigurationError("group membership changed during preflight")
            incumbent_ids = tuple(
                sorted(
                    cast(
                        "ModelArtifactStore", artifact_store
                    ).fetch_artifacts_by_status(
                        experiment.model_id,
                        ModelArtifactStatus.ACTIVE,
                        group_id=experiment.group_id,
                    ),
                    key=str,
                )
            )
            if fine_tuning_candidate_id is None:
                receipts.refuse_retained_candidate(experiment)

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

            if experiment is not None and fine_tuning_candidate_id is not None:
                from uuid import UUID

                assert receipts is not None and finish_evaluation is not None
                candidate_id = ArtifactId(UUID(fine_tuning_candidate_id))
                receipt = receipts.latest(candidate_id)
                if receipt.request.sha256 != experiment.sha256:
                    raise ConfigurationError(
                        "evaluation retry must use the original configuration"
                    )
                assert qualified is not None
                if (
                    frozenset(receipt.requested_stations) != qualified.group.station_ids
                    or receipt.donor_sha256 != qualified.donor.sha256_hash
                ):
                    raise ConfigurationError(
                        "evaluation retry scope or donor changed since fitting"
                    )
                record = cast(
                    "ModelArtifactStore", artifact_store
                ).fetch_artifact_record(candidate_id)
                if (
                    record is None
                    or record.status is not ModelArtifactStatus.TRAINING
                    or record.sha256_hash != receipt.artifact_sha256
                    or record.model_id != experiment.model_id
                    or record.group_id != experiment.group_id
                ):
                    raise ConfigurationError(
                        "only an incomplete inactive candidate can resume evaluation"
                    )
                results.append(finish_evaluation(receipt, unit, model_instance))
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
                if experiment is not None:
                    raise ConfigurationError(
                        "fine-tuning has insufficient fitting data"
                    )
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

            if experiment is not None:
                assert recording_source is not None
                cast("AuditLogStore", audit_log_store).append_entry(
                    AuditEntry.system(
                        event_type=AuditEventType.FINE_TUNING_STARTED,
                        target_type="fine_tuning_request",
                        target_id=str(experiment.request_id),
                        detail={
                            "request": experiment.payload(),
                            "configuration_sha256": experiment.sha256,
                            "requested_stations": sorted(
                                str(sid) for sid in unit.station_ids
                            ),
                            "fitted_stations": sorted(
                                str(sid)
                                for sid in cast("GroupTrainingData", data).station_ids
                            ),
                            "source_evidence": json.loads(recording_source.snapshot()),
                        },
                        ip_address=None,
                        created_at=cast("Callable[[], UtcDatetime]", clock)(),
                    )
                )

            # Plan 405 T1 — resolve the donor's config and REFUSE here, before any
            # training happens and before anything is stored. Deliberately OUTSIDE
            # the per-unit guard below: an unresolvable or contradictory donor
            # provenance is an integrity failure like the SHA-256 mismatch further
            # down, not a per-unit data shortfall, so it must abort the run loudly
            # rather than be recorded as one failed unit and continued past.
            donor_config: (
                tuple[str | None, str | None, str | None, str | None, str | None] | None
            ) = None
            if typed_base_artifact_id is not None and warm_start_writer is not None:
                donor_config = _resolve_donor_config_or_refuse(
                    warm_start_writer=warm_start_writer,
                    base_artifact_id=typed_base_artifact_id,
                    model=model_instance,
                )

            # T.3: train the model artifact. Wrapped so a raise from THIS call (the
            # reanalysis-tail missing-value crash class, or the existing
            # insufficient-data ValueError) is recorded as a failed unit and the
            # run CONTINUES for the remaining units, instead of aborting the whole
            # flow (Plan 130 Part B). Deliberately scoped to the training call
            # only — a SHA-256 mismatch further down signals artifact-store
            # corruption, a system-level integrity failure that must still abort
            # the run loudly, not be swallowed per-unit.
            try:
                base_artifact = (
                    _load_base_artifact(
                        model_instance, artifact_store, typed_base_artifact_id
                    )
                    if typed_base_artifact_id is not None
                    else None
                )
                artifact_bytes = _train_model_task(
                    unit=unit,
                    model=model_instance,
                    data=data,
                    rng=rng,
                    params=training_params,
                    base_artifact=base_artifact,
                )
            except Exception as exc:  # flow-level guard, see docstring above
                if experiment is not None:
                    raise
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

            receipt_factory: Callable[[ArtifactId], CandidateReceipt] | None = None
            candidate_provenance = None
            if experiment is not None:
                assert run_principal is not None and run_principal.tenant_id is not None
                assert qualified is not None and recording_source is not None
                fitted = tuple(
                    sorted(cast("GroupTrainingData", data).station_ids, key=str)
                )

                def build_receipt(
                    aid: ArtifactId,
                    artifact_bytes: bytes = artifact_bytes,
                    fitted: tuple[StationId, ...] = fitted,
                ) -> CandidateReceipt:
                    assert experiment is not None
                    assert (
                        run_principal is not None
                        and run_principal.tenant_id is not None
                    )
                    assert qualified is not None and recording_source is not None
                    return CandidateReceipt(
                        request=experiment,
                        tenant_id=run_principal.tenant_id,
                        artifact_id=aid,
                        artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
                        donor_sha256=qualified.donor.sha256_hash,
                        incumbent_ids=incumbent_ids,
                        requested_stations=tuple(
                            sorted(qualified.group.station_ids, key=str)
                        ),
                        fitted_stations=fitted,
                        fitting_sources_json=recording_source.snapshot(),
                    )

                receipt_factory = build_receipt
                if donor_config is None:
                    raise ConfigurationError(
                        "fine-tuning requires resolved donor provenance"
                    )
                candidate_provenance = partial(
                    _record_candidate_provenance,
                    base_artifact_id=experiment.base_artifact_id,
                    donor_config=donor_config,
                    run_config=training_params or {},
                    station_ids=fitted,
                )

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
                # Plan 399 T3 — a retrained artifact is never auto-promoted.
                promote=typed_base_artifact_id is None,
                receipt_for_artifact=receipt_factory,
                candidate_provenance=candidate_provenance,
            )

            # Plan 399 T3/T4 — warm-start provenance, right after the store, on the
            # same pattern as basin lineage below. Only for a RETRAIN: a freshly
            # trained artifact has no donor and records nothing (T4).
            if (
                experiment is None
                and donor_config is not None
                and warm_start_writer is not None
            ):
                _record_warm_start_provenance(
                    warm_start_writer=warm_start_writer,
                    artifact_id=cast("ArtifactId", artifact_id),
                    base_artifact_id=cast("ArtifactId", typed_base_artifact_id),
                    donor_config=donor_config,
                    run_config=training_params or {},
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
            if experiment is None:
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

            if experiment is not None:
                assert receipts is not None and finish_evaluation is not None
                receipt = receipts.latest(cast("ArtifactId", artifact_id))
                results.append(finish_evaluation(receipt, unit, model_instance))
                continue

            # Deserialize artifact for hindcast
            loaded_artifact = model_instance.deserialize_artifact(artifact_bytes)
            hindcast_run_id = uuid4()

            # T.4: run hindcast (as subflow)
            hindcast_steps_raw = run_hindcast_flow(
                model_id=unit.model_id,
                artifact_id=artifact_id,
                station_id=unit.station_id,
                group_id=unit.group_id,
                period_start=experiment.evaluation_start
                if experiment
                else unit.training_period_start,
                period_end=experiment.evaluation_end
                if experiment
                else unit.training_period_end,
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
            # task.map() with unmapped() store args requires an in-process runner
            # (ThreadPoolTaskRunner). Stores hold SQLAlchemy connections that are not
            # pickle-serializable — distributed/subprocess runners would fail.
            skill_computed = False
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
            # Plan 235 fixer round (major, D1 retry stability): mint ONE
            # generation id per (station, parameter) pair HERE, before the
            # fan-out, rather than leaving each mapped task instance's own
            # `None` default to mint one internally — a Prefect retry of any
            # ONE mapped task replays with its own already-bound id instead of
            # minting a fresh one on every retry.
            generation_ids = [uuid4() for _ in skill_pairs]
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
                generation_id=generation_ids,
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
