# pyright: reportUnknownMemberType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sapphire_flow.types.enums import AuditEventType, ModelArtifactStatus

if TYPE_CHECKING:
    import random
    from collections.abc import Callable

    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.protocols.stores import AuditLogStore, ModelArtifactStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import (
        ArtifactId,
        ModelId,
        StationGroupId,
        StationId,
        TenantId,
    )
    from sapphire_flow.types.model import (
        GroupTrainingData,
        ModelParams,
        StationTrainingData,
    )
    from sapphire_flow.types.write_principal import WritePrincipal

log = structlog.get_logger()


def train_station_model(
    model: StationForecastModel,
    data: StationTrainingData,
    params: ModelParams,
    rng: random.Random,
) -> bytes:
    artifact = model.train(data, params, rng)
    return model.serialize_artifact(artifact)


def train_group_model(
    model: GroupForecastModel,
    data: GroupTrainingData,
    params: ModelParams,
    rng: random.Random,
) -> bytes:
    artifact = model.train(data, params, rng)
    return model.serialize_artifact(artifact)


def promote_artifact(
    artifact_store: ModelArtifactStore,
    model_id: ModelId,
    new_id: ArtifactId,
    *,
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
    principal: WritePrincipal | None = None,
    target_tenant_id: TenantId | None = None,
    audit_log_store: AuditLogStore | None = None,
    now: UtcDatetime | None = None,
    audit_rejection: bool = True,
) -> None:
    """Transition existing ACTIVE artifacts to SUPERSEDED, then activate new_id.

    ``audit_rejection=False`` makes the internal tenant check RAISE-ONLY — it
    still rejects a cross-tenant write, but writes NO rejection ``audit_log``
    row. Atomic (txn-wrapped) call sites pass ``False`` so a defensive
    rejection never inserts an audit row into a transaction that then rolls
    back (Plan 147 Slice E, residual BLOCKER 3); the durable pre-authorization
    at the call site is the audited rejection point. The SUCCESS ``MODEL_PROMOTED``
    row is still written on ``audit_log_store`` (the txn store) below. Direct
    callers (fakes, no txn) leave it ``True`` — enforce audits + raises as before.

    Plan 147 Slice E (G3/G6): the single promotion chokepoint every write
    path (training, onboarding, the scheduled flow) funnels through — so
    tenant write-isolation is enforced HERE, once. ``principal``/
    ``audit_log_store``/``now`` are optional for back-compat (tests/replay
    contexts with no DB-backed principal); a REAL principal is always
    threaded by production flow entrypoints. A tenant-scoped principal
    REQUIRES ``target_tenant_id`` + ``now`` to check against — a caller that
    supplies one without the other is a wiring bug, raised loudly.

    On authorization, writes the ``audit_log`` ``MODEL_PROMOTED`` provenance
    row (Slice E) — ``actor_type='system'``, operator/tenant in ``detail``.
    ``model_artifacts.promoted_by`` stays NULL (v1.0 headless — no
    config-string operator fits the UUID column, reserved for v1.x
    sessions)."""
    if principal is not None and principal.tenant_id is not None:
        if target_tenant_id is None or now is None:
            from sapphire_flow.exceptions import ConfigurationError

            raise ConfigurationError(
                "promote_artifact: a tenant-scoped principal requires both "
                "target_tenant_id and now to check tenant authorization"
            )
        from sapphire_flow.services.write_principal import enforce_tenant_isolation

        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=target_tenant_id,
            audit_log_store=audit_log_store if audit_rejection else None,
            event_type=AuditEventType.MODEL_REJECTED,
            target_type="model_artifact",
            target_id=str(new_id),
            detail={
                "model_id": str(model_id),
                "station_id": str(station_id) if station_id is not None else None,
                "group_id": str(group_id) if group_id is not None else None,
            },
            now=now,
        )

    existing_active = artifact_store.fetch_artifacts_by_status(
        model_id=model_id,
        status=ModelArtifactStatus.ACTIVE,
        station_id=station_id,
        group_id=group_id,
    )
    for old_id in existing_active:
        artifact_store.transition_artifact_status(
            old_id, ModelArtifactStatus.SUPERSEDED
        )
        log.info(
            "training.artifact_superseded",
            model_id=str(model_id),
            artifact_id=str(old_id),
        )

    artifact_store.transition_artifact_status(new_id, ModelArtifactStatus.ACTIVE)
    log.info(
        "training.artifact_promoted",
        model_id=str(model_id),
        artifact_id=str(new_id),
    )

    if audit_log_store is not None and now is not None:
        from sapphire_flow.types.auth import AuditEntry

        audit_log_store.append_entry(
            AuditEntry.system(
                event_type=AuditEventType.MODEL_PROMOTED,
                target_type="model_artifact",
                target_id=str(new_id),
                detail={
                    "model_id": str(model_id),
                    "station_id": str(station_id) if station_id is not None else None,
                    "group_id": str(group_id) if group_id is not None else None,
                    "operator": principal.id if principal is not None else None,
                    "tenant_id": (
                        str(target_tenant_id) if target_tenant_id is not None else None
                    ),
                },
                ip_address=None,
                created_at=now,
            )
        )


def store_and_promote_artifact(
    artifact_store: ModelArtifactStore,
    model_id: ModelId,
    artifact_bytes: bytes,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    clock: Callable[[], UtcDatetime],
    *,
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
    principal: WritePrincipal | None = None,
    target_tenant_id: TenantId | None = None,
    audit_log_store: AuditLogStore | None = None,
    audit_rejection: bool = True,
) -> ArtifactId:
    """``audit_rejection=False`` → the internal tenant checks are RAISE-ONLY
    (no rejection ``audit_log`` row); atomic (txn) call sites pass ``False`` so
    a defensive rejection is never written into a rollback-able txn (residual
    BLOCKER 3). SUCCESS provenance still writes on ``audit_log_store``."""
    trained_at = clock()

    # Check BEFORE any store call (Slice E "no domain change" on rejection —
    # promote_artifact's own check below would otherwise let a forbidden
    # write leave an orphaned TRAINING-status artifact row behind).
    if principal is not None and principal.tenant_id is not None:
        if target_tenant_id is None:
            from sapphire_flow.exceptions import ConfigurationError

            raise ConfigurationError(
                "store_and_promote_artifact: a tenant-scoped principal "
                "requires target_tenant_id to check tenant authorization"
            )
        from sapphire_flow.services.write_principal import enforce_tenant_isolation

        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=target_tenant_id,
            audit_log_store=audit_log_store if audit_rejection else None,
            event_type=AuditEventType.MODEL_REJECTED,
            target_type="model_artifact",
            target_id=str(model_id),
            detail={
                "model_id": str(model_id),
                "station_id": str(station_id) if station_id is not None else None,
                "group_id": str(group_id) if group_id is not None else None,
            },
            now=trained_at,
        )

    new_id, _sha256 = artifact_store.store_artifact(
        model_id=model_id,
        artifact_bytes=artifact_bytes,
        training_period_start=period_start,
        training_period_end=period_end,
        trained_at=trained_at,
        station_id=station_id,
        group_id=group_id,
    )

    promote_artifact(
        artifact_store=artifact_store,
        model_id=model_id,
        new_id=new_id,
        station_id=station_id,
        group_id=group_id,
        principal=principal,
        target_tenant_id=target_tenant_id,
        audit_log_store=audit_log_store,
        now=trained_at,
        audit_rejection=audit_rejection,
    )

    return new_id
