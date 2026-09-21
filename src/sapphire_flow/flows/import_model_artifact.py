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
    training_period_start: object,
    training_period_end: object,
    clock: object,
    station_id: object,
    group_id: object,
    station_store: object,
    group_store: object,
    source_repository: str | None,
    source_commit: str | None,
    expected_config_hash: str,
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
        training_period_start=training_period_start,  # type: ignore[arg-type]
        training_period_end=training_period_end,  # type: ignore[arg-type]
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


def _decode_artifact_base64(artifact_base64: str) -> bytes:
    """Prefect deployment parameters cross the wire as JSON, and a `bytes`-
    typed flow parameter does NOT base64-decode a JSON string — pydantic's
    JSON-mode `bytes` validation does `str.encode()`, so `"AAEC/w=="` becomes
    the literal ASCII bytes `b"AAEC/w=="`, not the checkpoint it encodes.
    This is the actual deployment boundary: a `str` parameter, decoded here,
    strictly (``validate=True`` rejects non-base64 input rather than
    silently discarding invalid characters)."""
    import base64
    import binascii

    try:
        return base64.b64decode(artifact_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigurationError(
            "import_model_artifact_flow: artifact_base64 is not valid "
            "base64 — refusing to import"
        ) from exc


def _read_staged_artifact(artifact_path: str, expected_artifact_sha256: str) -> bytes:
    """Plan 307 T2 — read an artifact staged in the read-only `incoming`
    mount, with containment enforced BY THE OPEN and the digest taken of the
    exact buffer that is returned.

    The staging directory is writable by anyone with host shell access, so a
    resolve-then-open sequence is a TOCTOU: a checked component can be
    replaced with a symlink between the check and the open, and by the time a
    checksum could object the forbidden read has already happened.

    Three properties close that, and each exists for a reason:

    * **One path component only.** The artifact must be a direct child of the
      staging root. Independent review 2026-09-21 (major): walking
      intermediate directories and pinning a descriptor per level does NOT
      give containment, because a host writer can MOVE an intermediate
      directory out of the root between two opens and the pinned descriptor
      happily follows it. No subdirectories, no walk, no window.
    * **``O_NOFOLLOW`` on both opens**, including the root, so a symlinked
      root or a symlinked artifact fails the open itself.
    * **``O_NONBLOCK`` plus a regular-file check.** Independent review
      2026-09-21 (minor), confirmed by execution: opening a staged FIFO
      blocks forever, and neither symlink protection nor a checksum prevents
      it — the block happens in the open.

    ⚠️ Two residual limits, stated rather than papered over. A **hardlink**
    inside the root to an outside file is indistinguishable from an ordinary
    file and will be read; only a filesystem boundary could prevent that, and
    the checksum is what makes it a non-import. And the **root path itself is
    trusted configuration** — an attacker who controls the deployment's env
    or the root's parent directory controls the deployment.
    """
    import hashlib
    import os
    import stat
    from pathlib import PurePosixPath

    from sapphire_flow.config.paths import resolve_incoming_dir

    root = resolve_incoming_dir()
    candidate = PurePosixPath(artifact_path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(PurePosixPath(str(root)))
        except ValueError:
            raise ConfigurationError(
                "import_model_artifact_flow: artifact_path "
                f"{artifact_path!r} is outside the staging root "
                f"{str(root)!r} — refusing to import"
            ) from None
    parts = candidate.parts
    if len(parts) != 1 or parts[0] in ("", ".", ".."):
        raise ConfigurationError(
            f"import_model_artifact_flow: artifact_path {artifact_path!r} must "
            f"name a file directly inside the staging root {str(root)!r} — "
            "subdirectories are not supported, because containment cannot be "
            "guaranteed across an intermediate directory that a host writer "
            "can move. Refusing to import."
        )

    try:
        dir_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ConfigurationError(
            f"import_model_artifact_flow: staging root {str(root)!r} is not "
            "available as a real directory — the deployment's compose overlay "
            "must bind it read-only, and it must not be a symlink; refusing "
            "to import"
        ) from exc
    try:
        try:
            fd = os.open(
                parts[0],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=dir_fd,
            )
        except OSError as exc:
            raise ConfigurationError(
                "import_model_artifact_flow: artifact_path "
                f"{artifact_path!r} could not be opened inside the staging "
                f"root {str(root)!r} without following a symlink — refusing "
                "to import"
            ) from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ConfigurationError(
                    "import_model_artifact_flow: artifact_path "
                    f"{artifact_path!r} is not a regular file — refusing to "
                    "import"
                )
            os.set_blocking(fd, True)
        except BaseException:
            os.close(fd)
            raise
        with os.fdopen(fd, "rb") as handle:
            artifact_bytes = handle.read()
    finally:
        os.close(dir_fd)

    actual = hashlib.sha256(artifact_bytes).hexdigest()
    if actual.lower() != expected_artifact_sha256.strip().lower():
        raise ConfigurationError(
            "import_model_artifact_flow: artifact_path content does not "
            "match expected_artifact_sha256 — expected "
            f"{expected_artifact_sha256!r}, read {actual!r}. Refusing to "
            "import."
        )
    return artifact_bytes


def _resolve_artifact_bytes(
    *,
    artifact_base64: str | None,
    artifact_path: str | None,
    expected_artifact_sha256: str | None,
) -> bytes:
    """Exactly one source, never both, never neither (Plan 307 T2).

    ``expected_artifact_sha256`` is REQUIRED on the path route and is
    deliberately NOT accepted on the base64 route: there the bytes are
    already in the parameter, so there is nothing between caller and flow for
    a digest to protect against.
    """
    if artifact_base64 is not None and artifact_path is not None:
        raise ConfigurationError(
            "import_model_artifact_flow: give artifact_base64 OR artifact_path, "
            "not both — refusing to import"
        )
    if artifact_base64 is None and artifact_path is None:
        raise ConfigurationError(
            "import_model_artifact_flow: one of artifact_base64 or artifact_path is "
            "required — refusing to import"
        )
    if artifact_path is not None:
        if not expected_artifact_sha256:
            raise ConfigurationError(
                "import_model_artifact_flow: expected_artifact_sha256 is required when "
                "artifact_path is used — the staging directory is host-writable, so an "
                "unverified read is not an import we can vouch for. Refusing."
            )
        return _read_staged_artifact(artifact_path, expected_artifact_sha256)
    if expected_artifact_sha256 is not None:
        raise ConfigurationError(
            "import_model_artifact_flow: expected_artifact_sha256 applies to "
            "artifact_path only — the base64 route carries the bytes in the "
            "parameter itself, so a digest there protects nothing. Refusing "
            "rather than ignoring it, so a caller who believes they asked "
            "for verification is not quietly told otherwise."
        )
    return _decode_artifact_base64(artifact_base64)


@flow(name="import-model-artifact", log_prints=False)
def import_model_artifact_flow(  # noqa: PLR0913
    model_id: str,
    *,
    artifact_base64: str | None = None,
    artifact_path: str | None = None,
    expected_artifact_sha256: str | None = None,
    trained_at: str,
    training_period_start: str,
    training_period_end: str,
    expected_config_hash: str,
    station_id: str | None = None,
    group_id: str | None = None,
    source_repository: str | None = None,
    source_commit: str | None = None,
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
    artifact_bytes = _resolve_artifact_bytes(
        artifact_base64=artifact_base64,
        artifact_path=artifact_path,
        expected_artifact_sha256=expected_artifact_sha256,
    )

    new_id = _import_task(
        model,
        model_id,
        artifact_bytes,
        stores["artifact_store"],
        ensure_utc(datetime.fromisoformat(trained_at)),
        ensure_utc(datetime.fromisoformat(training_period_start)),
        ensure_utc(datetime.fromisoformat(training_period_end)),
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
