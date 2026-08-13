"""Plan 157 T3 (G4/G7) — the executable entry point for importing an
externally-trained artifact.

Deliberately NOT a step in Flow 13 (`flows/onboard_model.py`): the import
path never assembles training data and never calls `model.train`. This is
a standalone, manually-triggered deployment (`import-model-artifact`, no
cron — see `cli/register_deployments.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from sapphire_flow.types.ids import ArtifactId

log = structlog.get_logger(__name__)


@task(name="resolve-import-model", log_prints=False, cache_policy=NO_CACHE)
def _resolve_model_task(model_id: str) -> object:
    from sapphire_flow.services.model_registry import discover_models
    from sapphire_flow.types.ids import ModelId

    models = discover_models()
    model = models.get(ModelId(model_id))
    if model is None:
        raise ConfigurationError(
            f"import_model_artifact_flow: model {model_id!r} is not "
            "discoverable — is its entry point installed in this worker's "
            "environment?"
        )
    return model


@task(name="import-external-artifact", log_prints=False, cache_policy=NO_CACHE)
def _import_task(  # noqa: PLR0913
    model: object,
    model_id: str,
    artifact_bytes: bytes,
    artifact_store: object,
    trained_at: object,
    clock: object,
    station_id: object,
    group_id: object,
    station_store: object,
    group_store: object,
    source_repository: str | None,
    source_commit: str | None,
    expected_config_hash: str | None,
    imported_by: str | None,
    notes: str | None,
    principal: object,
    supplied_target_tenant_id: object,
    audit_log_store: object,
    provenance_recorder: object,
    audited_writer: object,
) -> ArtifactId:
    from sapphire_flow.services.model_import import import_external_artifact
    from sapphire_flow.types.ids import ModelId

    return import_external_artifact(
        model=model,  # type: ignore[arg-type]
        model_id=ModelId(model_id),
        artifact_bytes=artifact_bytes,
        artifact_store=artifact_store,  # type: ignore[arg-type]
        trained_at=trained_at,  # type: ignore[arg-type]
        clock=clock,  # type: ignore[arg-type]
        station_id=station_id,  # type: ignore[arg-type]
        group_id=group_id,  # type: ignore[arg-type]
        station_store=station_store,  # type: ignore[arg-type]
        group_store=group_store,  # type: ignore[arg-type]
        source_repository=source_repository,
        source_commit=source_commit,
        expected_config_hash=expected_config_hash,
        imported_by=imported_by,
        notes=notes,
        principal=principal,  # type: ignore[arg-type]
        supplied_target_tenant_id=supplied_target_tenant_id,  # type: ignore[arg-type]
        audit_log_store=audit_log_store,  # type: ignore[arg-type]
        provenance_recorder=provenance_recorder,  # type: ignore[arg-type]
        audited_writer=audited_writer,  # type: ignore[arg-type]
    )


@flow(name="import-model-artifact", log_prints=False)
def import_model_artifact_flow(  # noqa: PLR0913
    model_id: str,
    artifact_bytes: bytes,
    trained_at: str,
    station_id: str | None = None,
    group_id: str | None = None,
    source_repository: str | None = None,
    source_commit: str | None = None,
    expected_config_hash: str | None = None,
    imported_by: str | None = None,
    notes: str | None = None,
    tenant_code: str | None = None,
    operator: str | None = None,
) -> str:
    """Register -> deserialize-validate -> store -> record provenance ->
    promote an externally-trained artifact. Never assembles training data
    and never calls `model.train` (Plan 157 T3's public-boundary
    acceptance criterion)."""
    import os
    from uuid import UUID

    from sapphire_flow.services.write_principal import resolve_flow_run_principal
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.ids import StationGroupId, StationId

    structlog.contextvars.bind_contextvars(model_id=model_id)

    database_url = os.environ["DATABASE_URL"]
    from sapphire_flow.flows._db import setup_production_stores

    _conn, stores = setup_production_stores(database_url)

    from sapphire_flow.store.audited_writer import make_audited_writer

    audited_writer = make_audited_writer(_conn)

    principal = resolve_flow_run_principal(
        tenant_store=stores.get("tenant_store"),  # type: ignore[arg-type]
        tenant_code=tenant_code,
        operator=operator,
    )

    from datetime import UTC, datetime

    def clock() -> object:
        return ensure_utc(datetime.now(UTC))

    model = _resolve_model_task(model_id)

    new_id = _import_task(
        model,
        model_id,
        artifact_bytes,
        stores["artifact_store"],
        ensure_utc(datetime.fromisoformat(trained_at)),
        clock,
        StationId(UUID(station_id)) if station_id is not None else None,
        StationGroupId(UUID(group_id)) if group_id is not None else None,
        stores["station_store"],
        stores["group_store"],
        source_repository,
        source_commit,
        expected_config_hash,
        imported_by,
        notes,
        principal,
        None,
        stores.get("audit_log_store"),
        None,
        audited_writer,
    )

    log.info("import_model_artifact_flow.complete", artifact_id=str(new_id))
    return str(new_id)
