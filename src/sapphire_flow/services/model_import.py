"""Plan 157 T3 (G4/G7) — external-artifact import.

Flow 13 (`flows/onboard_model.py`) runs scope -> register -> validate ->
smoke -> assemble -> **train** -> store -> promote -> assign. There is no
path that registers an artifact SAP3 did not train, and nowhere records
what such an artifact *is*. `import_external_artifact` is that path: it
validates, stores, records provenance and promotes an externally-trained
artifact WITHOUT ever calling `model.train` — the public-boundary
acceptance criterion this module is built to satisfy.

Invariants (Plan 157 T3):
- **A config/artifact mismatch fails loudly, before any write.**
- **An undeserializable blob fails loudly, before any write.**
- **The tenant is DERIVED from the target** (station/group), never trusted
  from a caller-supplied argument.
- **`trained_at` (external training completion) is distinct from
  `imported_at`** (this import's own instant, `clock()`) — never conflated.
- **All-or-nothing**: with a real `AuditedWriter`, `store_artifact` +
  provenance + `promote_artifact` run in ONE transaction; on any failure no
  artifact row of any status, no changed prior ACTIVE row, and no
  provenance row survive, and the artifact file (if written) is deleted —
  no orphan.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

import structlog

from sapphire_flow.exceptions import ConfigurationError, ModelLoadError
from sapphire_flow.types.enums import AuditEventType, ModelArtifactStatus
from sapphire_flow.types.model import ModelArtifactProvenance

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        ModelArtifactStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.store.audited_writer import AuditedWriter
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import (
        ArtifactId,
        ModelId,
        StationGroupId,
        StationId,
        TenantId,
    )
    from sapphire_flow.types.write_principal import WritePrincipal

log = structlog.get_logger()


@runtime_checkable
class ProvenanceRecorder(Protocol):
    """`.record(...)` shape shared by `PgArtifactProvenanceStore` and test
    fakes — matches the existing `lineage_writer.record(...)` convention."""

    def record(self, provenance: ModelArtifactProvenance) -> None: ...


def _derive_target_tenant_id(
    *,
    station_id: StationId | None,
    group_id: StationGroupId | None,
    station_store: StationStore | None,
    group_store: StationGroupStore | None,
) -> TenantId:
    if (station_id is None) == (group_id is None):
        raise ConfigurationError(
            "import_external_artifact: exactly one of station_id/group_id is required"
        )
    if station_id is not None:
        if station_store is None:
            raise ConfigurationError(
                "import_external_artifact: station_id requires station_store"
            )
        station = station_store.fetch_station(station_id)
        if station is None:
            raise ConfigurationError(
                f"import_external_artifact: unknown station {station_id}"
            )
        return station.tenant_id
    if group_store is None:
        raise ConfigurationError(
            "import_external_artifact: group_id requires group_store"
        )
    group = group_store.fetch_group(cast("StationGroupId", group_id))
    if group is None:
        raise ConfigurationError(f"import_external_artifact: unknown group {group_id}")
    return group.tenant_id


def import_external_artifact(  # noqa: PLR0913
    *,
    model: ForecastModel,
    model_id: ModelId,
    artifact_bytes: bytes,
    trained_at: UtcDatetime,
    clock: Callable[[], UtcDatetime],
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
    station_store: StationStore | None = None,
    group_store: StationGroupStore | None = None,
    source_repository: str | None = None,
    source_commit: str | None = None,
    expected_config_hash: str | None = None,
    imported_by: str | None = None,
    notes: str | None = None,
    principal: WritePrincipal | None = None,
    supplied_target_tenant_id: TenantId | None = None,
    audit_log_store: AuditLogStore | None = None,
    artifact_store: ModelArtifactStore | None = None,
    provenance_recorder: ProvenanceRecorder | None = None,
    audited_writer: AuditedWriter | None = None,
) -> ArtifactId:
    """`artifact_store`/`provenance_recorder` are the test/replay fallback
    (`audited_writer=None`) wiring — both required in that mode. With a real
    `audited_writer`, the transactional stores it builds are used instead and
    `artifact_store`/`provenance_recorder`, if given, are ignored (a real
    production import always threads `audited_writer`)."""
    derived_tenant_id = _derive_target_tenant_id(
        station_id=station_id,
        group_id=group_id,
        station_store=station_store,
        group_store=group_store,
    )
    if (
        supplied_target_tenant_id is not None
        and supplied_target_tenant_id != derived_tenant_id
    ):
        from sapphire_flow.exceptions import TenantIsolationError

        raise TenantIsolationError(
            "import_external_artifact: supplied target_tenant_id "
            f"{supplied_target_tenant_id} does not match the tenant derived "
            f"from the import target ({derived_tenant_id}) — the tenant is "
            "always derived from the target, never trusted from the caller"
        )

    now = clock()
    if principal is not None and principal.tenant_id is not None:
        from sapphire_flow.services.write_principal import enforce_tenant_isolation

        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=derived_tenant_id,
            audit_log_store=audit_log_store,
            event_type=AuditEventType.MODEL_REJECTED,
            target_type="model_artifact",
            target_id=str(model_id),
            detail={
                "model_id": str(model_id),
                "station_id": str(station_id) if station_id is not None else None,
                "group_id": str(group_id) if group_id is not None else None,
                "operation": "import",
            },
            now=now,
        )

    # Config/artifact mismatch — fail loudly, before any write (D1 ships the
    # shim config as package data while the artifact is the native weights
    # file; the two can drift across a shim release).
    declared_config_hash = getattr(model, "config_hash", None)
    if (
        expected_config_hash is not None
        and declared_config_hash != expected_config_hash
    ):
        raise ConfigurationError(
            f"import_external_artifact: config/artifact mismatch for "
            f"{model_id} — the model declares config_hash "
            f"{declared_config_hash!r}, the import expected "
            f"{expected_config_hash!r}. Refusing to store."
        )

    # Deserialize validation — fail loudly, before any write. This never
    # calls model.train.
    try:
        model.deserialize_artifact(artifact_bytes)
    except Exception as exc:
        raise ModelLoadError(
            f"import_external_artifact: artifact for {model_id} failed to "
            "deserialize via the model's own deserialize_artifact — "
            "refusing to store"
        ) from exc

    log.info(
        "model_import.validated",
        model_id=str(model_id),
        station_id=str(station_id) if station_id is not None else None,
        group_id=str(group_id) if group_id is not None else None,
    )

    artifact_path_for_cleanup: str | None = None

    def _run(
        store: ModelArtifactStore,
        recorder: ProvenanceRecorder,
        audit: AuditLogStore | None,
        *,
        audit_rejection: bool,
    ) -> ArtifactId:
        nonlocal artifact_path_for_cleanup
        from sapphire_flow.services.training import promote_artifact

        new_id, _sha256 = store.store_artifact(
            model_id=model_id,
            artifact_bytes=artifact_bytes,
            training_period_start=trained_at,
            training_period_end=trained_at,
            trained_at=trained_at,
            station_id=station_id,
            group_id=group_id,
            status=ModelArtifactStatus.TRAINING,
        )
        record = store.fetch_artifact_record(new_id)
        if record is not None:
            artifact_path_for_cleanup = record.artifact_path

        recorder.record(
            ModelArtifactProvenance(
                artifact_id=new_id,
                source_repository=source_repository,
                source_commit=source_commit,
                config_hash=declared_config_hash,
                imported_at=now,
                imported_by=imported_by,
                notes=notes,
            )
        )

        promote_artifact(
            artifact_store=store,
            model_id=model_id,
            new_id=new_id,
            station_id=station_id,
            group_id=group_id,
            principal=principal,
            target_tenant_id=derived_tenant_id,
            audit_log_store=audit,
            now=now,
            audit_rejection=audit_rejection,
        )
        return new_id

    if audited_writer is None:
        # Test/replay wiring — no real transaction available. Fakes hold
        # in-memory state that a SQL rollback could not undo anyway, so
        # there is no atomicity to lose here; production always threads a
        # real AuditedWriter (see flows/import_model_artifact.py).
        if artifact_store is None or provenance_recorder is None:
            raise ConfigurationError(
                "import_external_artifact: artifact_store and "
                "provenance_recorder are both required when audited_writer "
                "is not provided"
            )
        return _run(
            artifact_store,
            provenance_recorder,
            audit_log_store,
            audit_rejection=True,
        )

    try:
        with audited_writer.transaction() as stores:
            new_id = _run(
                cast("ModelArtifactStore", stores["artifact_store"]),
                cast("ProvenanceRecorder", stores["provenance_store"]),
                cast("AuditLogStore", stores["audit_log_store"]),
                audit_rejection=False,
            )
    except Exception:
        if artifact_path_for_cleanup is not None:
            Path(artifact_path_for_cleanup).unlink(missing_ok=True)
            log.warning(
                "model_import.rolled_back_orphan_file_deleted",
                model_id=str(model_id),
                artifact_path=artifact_path_for_cleanup,
            )
        raise
    return new_id
