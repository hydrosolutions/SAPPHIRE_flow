"""Plan 157 T3 (G4/G7) — external-artifact import.

Flow 13 (`flows/onboard_model.py`) runs scope -> register -> validate ->
smoke -> assemble -> **train** -> store -> promote -> assign. There is no
path that registers an artifact SAP3 did not train, and nowhere records
what such an artifact *is*. `import_external_artifact` is that path: it
validates, stores, records provenance and promotes an externally-trained
artifact WITHOUT ever calling `model.train` — the public-boundary
acceptance criterion this module is built to satisfy.

Invariants (Plan 157 T3):
- **A config/artifact mismatch fails loudly, before any write.** Both the
  model's declared `config_hash` and the caller-supplied
  `expected_config_hash` (the digest carried by the artifact's own import
  manifest — D1 ships the shim config as package data, so the artifact and
  its config can drift across a shim release) are REQUIRED and must match.
  There is no warning-only bypass — a caller that omits either value is
  refused, not merely logged.
- **An undeserializable blob fails loudly, before any write.**
- **The tenant is DERIVED from the target** (station/group), never trusted
  from a caller-supplied argument.
- **`trained_at` (external training completion), `training_period_start`/
  `training_period_end` (the real external training window) and
  `imported_at`** (this import's own instant, `clock()`) are three
  DISTINCT, caller-supplied values — never conflated, never fabricated as a
  zero-length window at `trained_at`.
- **All-or-nothing, always — including the fake-backed test/replay path.**
  `store_artifact` + provenance + `promote_artifact` run inside ONE unit of
  work: a real `AuditedWriter` transaction in production, or an in-memory
  snapshot/restore wrapper around the caller's fakes otherwise. On any
  failure no artifact row of any status, no changed prior ACTIVE row, and
  no provenance row survive, and the artifact file (if written) is deleted
  — no orphan.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

import structlog

from sapphire_flow.exceptions import ConfigurationError, ModelLoadError
from sapphire_flow.store.audited_writer import AuditedWriter
from sapphire_flow.types.enums import ArtifactScope, AuditEventType, ModelArtifactStatus
from sapphire_flow.types.model import ModelArtifactProvenance, ModelRecord

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        ModelArtifactStore,
        ModelStore,
        StationGroupStore,
        StationStore,
    )
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


@dataclass(slots=True)
class _InMemoryAuditedWriter:
    """The unit-of-work for the test/replay fallback path (no real
    `AuditedWriter`/Postgres transaction available) — Plan 157 T3 fixer
    round, finding: "require a unit-of-work for every import, including
    fakes." Without this, a mid-sequence failure (e.g. `provenance_store.
    record` raising) left whatever the fakes had already mutated — an
    artifact stuck at TRAINING forever — with nothing to prove or enforce
    all-or-nothing for the fake-backed path the unit test suite actually
    exercises.

    Snapshots each fake store's private in-memory dict on entry and
    restores it verbatim on any exception, mirroring what a real Postgres
    ROLLBACK does for the DB-backed path. Stores that do not expose the
    expected private attribute (e.g. `PgModelArtifactStore` used directly,
    with no `AuditedWriter`, on an AUTOCOMMIT connection) are left alone —
    that combination is deliberately NON-atomic and
    `test_provenance_failure_persists_orphan_on_autocommit_wiring`
    characterizes exactly that."""

    artifact_store: ModelArtifactStore
    provenance_store: ProvenanceRecorder
    audit_log_store: AuditLogStore | None
    model_store: ModelStore

    def _snapshot(self) -> dict[str, object]:
        return {
            name: dict(state)
            for name, state in (
                ("artifact_records", getattr(self.artifact_store, "_records", None)),
                ("artifact_bytes", getattr(self.artifact_store, "_bytes", None)),
                (
                    "provenance_records",
                    getattr(self.provenance_store, "_records", None),
                ),
                ("models", getattr(self.model_store, "_models", None)),
            )
            if state is not None
        }

    def _restore(self, snapshot: dict[str, object]) -> None:
        targets = (
            ("artifact_records", self.artifact_store, "_records"),
            ("artifact_bytes", self.artifact_store, "_bytes"),
            ("provenance_records", self.provenance_store, "_records"),
            ("models", self.model_store, "_models"),
        )
        for key, owner, attr in targets:
            if key not in snapshot:
                continue
            live = getattr(owner, attr, None)
            if live is None:
                continue
            live.clear()
            live.update(cast("dict[object, object]", snapshot[key]))

    @contextmanager
    def transaction(self) -> Iterator[dict[str, object]]:
        snapshot = self._snapshot()
        try:
            yield {
                "artifact_store": self.artifact_store,
                "provenance_store": self.provenance_store,
                "audit_log_store": self.audit_log_store,
                "model_store": self.model_store,
            }
        except Exception:
            self._restore(snapshot)
            raise


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


def _assert_scope_matches_target(
    *,
    model_id: ModelId,
    artifact_scope: ArtifactScope,
    station_id: StationId | None,
    group_id: StationGroupId | None,
) -> None:
    """A GROUP-scoped model imported against a station_id (or vice versa)
    would report success while being operationally invisible to whichever
    lookup path actually queries by the OTHER scope. `_derive_target_tenant_id`
    already enforces exactly one of station_id/group_id — this enforces that
    the one supplied matches what the model itself declares."""
    if artifact_scope is ArtifactScope.STATION:
        if station_id is None:
            raise ConfigurationError(
                f"import_external_artifact: model {model_id} is STATION-scoped "
                "but this import supplied a group_id, not a station_id"
            )
        return
    if artifact_scope is ArtifactScope.GROUP:
        if group_id is None:
            raise ConfigurationError(
                f"import_external_artifact: model {model_id} is GROUP-scoped "
                "but this import supplied a station_id, not a group_id"
            )
        return
    raise ConfigurationError(
        f"import_external_artifact: model {model_id} declares unsupported "
        f"artifact_scope {artifact_scope!r} — only STATION and GROUP can be "
        "imported"
    )


def _declared_artifact_scope(model: ForecastModel, model_id: ModelId) -> ArtifactScope:
    declared_scope = getattr(model, "artifact_scope", None)
    if not isinstance(declared_scope, ArtifactScope):
        raise ConfigurationError(
            f"import_external_artifact: model {model_id} does not declare a "
            "valid artifact_scope"
        )
    return declared_scope


def _register_or_verify_model(
    *,
    model_id: ModelId,
    model: ForecastModel,
    declared_scope: ArtifactScope,
    model_store: ModelStore,
    now: UtcDatetime,
) -> ModelRecord:
    """A fresh external model has no `models` row yet — `model_artifacts.
    model_id` is an FK, so the first import of a genuinely new model would
    otherwise fail with an opaque FK violation (or, worse, silently depend on
    some unrelated earlier flow having registered it). Mirrors Flow 13's own
    `register_models` (idempotent `ON CONFLICT DO NOTHING`), but ALSO
    verifies a pre-existing row's scope actually matches what the discovered
    model declares today — registry drift across a shim upgrade must fail
    loudly, not silently keep stale metadata."""
    existing = model_store.fetch_model(model_id)
    if existing is not None:
        if existing.artifact_scope != declared_scope:
            raise ConfigurationError(
                f"import_external_artifact: model {model_id} is already "
                f"registered with artifact_scope {existing.artifact_scope.value!r}, "
                f"but the discovered model now declares "
                f"{declared_scope.value!r} — refusing to import (registry "
                "drift)"
            )
        return existing

    from sapphire_flow.services.model_registry import build_registry_entry

    entry = build_registry_entry(model_id, model, registered_at=now)
    record = ModelRecord(
        id=entry.id,
        display_name=entry.display_name,
        artifact_scope=entry.artifact_scope,
        description=entry.description,
        created_at=now,
    )
    model_store.register_model(record)
    # Re-read the authoritative row rather than trusting `record` blindly:
    # registration is `ON CONFLICT DO NOTHING` (idempotent), so a concurrent
    # worker (or a mixed-version deployment) could have inserted a row with a
    # DIFFERENT scope between the `fetch_model` above and this insert. A
    # stale in-hand `record` would let the rest of this import proceed
    # against an assumption the database no longer holds.
    authoritative = model_store.fetch_model(model_id)
    if authoritative is None:
        raise ConfigurationError(
            f"import_external_artifact: model {model_id} registration did not "
            "produce a readable row"
        )
    if authoritative.artifact_scope != declared_scope:
        raise ConfigurationError(
            f"import_external_artifact: model {model_id} was registered "
            f"concurrently with artifact_scope "
            f"{authoritative.artifact_scope.value!r}, but the discovered "
            f"model declares {declared_scope.value!r} — refusing to import "
            "(registry drift)"
        )
    log.info("model_import.model_registered", model_id=str(model_id))
    return authoritative


def import_external_artifact(  # noqa: PLR0913
    *,
    model: ForecastModel,
    model_id: ModelId,
    artifact_bytes: bytes,
    trained_at: UtcDatetime,
    training_period_start: UtcDatetime,
    training_period_end: UtcDatetime,
    expected_config_hash: str,
    clock: Callable[[], UtcDatetime],
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
    station_store: StationStore | None = None,
    group_store: StationGroupStore | None = None,
    source_repository: str | None = None,
    source_commit: str | None = None,
    imported_by: str | None = None,
    notes: str | None = None,
    principal: WritePrincipal | None = None,
    supplied_target_tenant_id: TenantId | None = None,
    audit_log_store: AuditLogStore | None = None,
    artifact_store: ModelArtifactStore | None = None,
    provenance_recorder: ProvenanceRecorder | None = None,
    model_store: ModelStore | None = None,
    audited_writer: AuditedWriter | None = None,
) -> ArtifactId:
    """`artifact_store`/`provenance_recorder`/`model_store` are the
    test/replay fallback (`audited_writer=None`) wiring — all three required
    in that mode; they are wrapped in an `_InMemoryAuditedWriter` so the
    all-or-nothing guarantee holds there too. With a real `audited_writer`,
    the transactional stores it builds are used instead and any of the
    three, if given, are ignored (a real production import always threads
    `audited_writer`).

    `trained_at`, `training_period_start`/`training_period_end` (the REAL
    external training window) and `imported_at` (`clock()`, below) are three
    deliberately distinct values — none is ever synthesized from another.
    `expected_config_hash` is REQUIRED: it is the digest carried by the
    artifact's own import manifest, and it must match the model's declared
    `config_hash` (also required, non-`None`) before anything is written —
    there is no warning-only opt-out."""
    derived_tenant_id = _derive_target_tenant_id(
        station_id=station_id,
        group_id=group_id,
        station_store=station_store,
        group_store=group_store,
    )
    declared_scope = _declared_artifact_scope(model, model_id)
    _assert_scope_matches_target(
        model_id=model_id,
        artifact_scope=declared_scope,
        station_id=station_id,
        group_id=group_id,
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
    # file; the two can drift across a shim release). Both the model's own
    # declared config_hash and the caller-supplied expected_config_hash (the
    # artifact manifest's digest) are REQUIRED — a missing value is refused,
    # never silently skipped (Plan 157 T3 fixer round: a warning-only bypass
    # let a mismatched artifact promote and later predict against the wrong
    # packaged config).
    declared_config_hash = getattr(model, "config_hash", None)
    if declared_config_hash is None:
        raise ConfigurationError(
            f"import_external_artifact: model {model_id} does not declare a "
            "config_hash — refusing to import without a config/artifact "
            "drift check"
        )
    if declared_config_hash != expected_config_hash:
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
        registry: ModelStore,
        *,
        audit_rejection: bool,
    ) -> ArtifactId:
        nonlocal artifact_path_for_cleanup
        from sapphire_flow.services.training import promote_artifact

        _register_or_verify_model(
            model_id=model_id,
            model=model,
            declared_scope=declared_scope,
            model_store=registry,
            now=now,
        )

        stored = store.store_artifact(
            model_id=model_id,
            artifact_bytes=artifact_bytes,
            training_period_start=training_period_start,
            training_period_end=training_period_end,
            trained_at=trained_at,
            station_id=station_id,
            group_id=group_id,
            status=ModelArtifactStatus.TRAINING,
        )
        new_id = stored.artifact_id
        # Store-owned, known atomically with the write — no separate
        # (fallible) fetch_artifact_record query needed just to learn what
        # to clean up on transaction failure (Plan 157 T3 fixer round).
        artifact_path_for_cleanup = stored.artifact_path

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

    writer: AuditedWriter | _InMemoryAuditedWriter
    if audited_writer is None:
        # Test/replay wiring — no real AuditedWriter/Postgres transaction
        # available. Wrapped in `_InMemoryAuditedWriter` so the
        # all-or-nothing guarantee still holds: EVERY call goes through
        # `.transaction()`, real or fake (Plan 157 T3 fixer round —
        # production always threads a real AuditedWriter; see
        # flows/import_model_artifact.py).
        if artifact_store is None or provenance_recorder is None or model_store is None:
            raise ConfigurationError(
                "import_external_artifact: artifact_store, "
                "provenance_recorder and model_store are all required when "
                "audited_writer is not provided"
            )
        writer = _InMemoryAuditedWriter(
            artifact_store=artifact_store,
            provenance_store=provenance_recorder,
            audit_log_store=audit_log_store,
            model_store=model_store,
        )
    else:
        writer = audited_writer

    # `_InMemoryAuditedWriter` is not a real (non-AUTOCOMMIT) Postgres
    # transaction, so `promote_artifact`'s defensive tenant-rejection audit
    # row must still be written directly there — matching the historical
    # "direct callers (fakes, no txn) leave it True" convention.
    audit_rejection = not isinstance(writer, AuditedWriter)

    try:
        with writer.transaction() as stores:
            new_id = _run(
                cast("ModelArtifactStore", stores["artifact_store"]),
                cast("ProvenanceRecorder", stores["provenance_store"]),
                cast("AuditLogStore", stores["audit_log_store"]),
                cast("ModelStore", stores["model_store"]),
                audit_rejection=audit_rejection,
            )
    except Exception:
        # Only unlink the file when the STORE's own record of it was also
        # rolled back — a real AuditedWriter transaction always rolls back;
        # `_InMemoryAuditedWriter` only rolls back stores it can snapshot
        # (fakes exposing `_records`). A bare `PgModelArtifactStore` on a
        # plain AUTOCOMMIT connection (no writer at all — the
        # non-atomic characterization path) rolls back NEITHER: deleting the
        # file there would leave the still-committed DB row pointing at a
        # file that no longer exists, which is worse than the orphan itself.
        db_state_rolled_back = isinstance(writer, AuditedWriter) or hasattr(
            writer.artifact_store, "_records"
        )
        if artifact_path_for_cleanup is not None and db_state_rolled_back:
            Path(artifact_path_for_cleanup).unlink(missing_ok=True)
            log.warning(
                "model_import.rolled_back_orphan_file_deleted",
                model_id=str(model_id),
                artifact_path=artifact_path_for_cleanup,
            )
        raise
    return new_id
