# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sapphire_flow.db import metadata as db
from sapphire_flow.store.forecast_publication_store import PgForecastPublicationStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.enums import ForecastDataUse, ForecastStatus
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.integration.ops.mixed_restore_fixture import (
    IMAGE_DIGEST,
    Restored,
    assert_container_absent,
    assert_manifest,
    cleanup_owned,
    manifest,
    record_resources,
    start_owned,
)
from tests.integration.ops.mixed_restore_fixture import restored as restored
from tests.integration.store.test_forecast_publication_store import _NOW, _transaction

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer


def test_exact_mixed_content_and_comparator_sensitivity(restored: Restored) -> None:
    assert restored.test.evidence is not None and restored.standard.evidence is not None
    with restored.harness.owner_engine.connect() as conn:
        actual = manifest(conn)
        assert_manifest(actual, restored.expected)
        assert conn.execute(sa.select(db.forecast_input_stations)).all() == [
            (restored.test.id, restored.test.station_id, DEFAULT_TENANT_ID)
        ]
        classes = conn.execute(
            sa.select(db.forecasts.c.data_use, sa.func.count()).group_by(
                db.forecasts.c.data_use
            )
        ).all()
        assert {data_use: count for data_use, count in classes} == {
            "standard": 3,
            "expired_rating_test": 1,
        }
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(db.forecast_evidence_blobs)
                .where(
                    db.forecast_evidence_blobs.c.sha256
                    == restored.test.evidence.snapshot_sha256
                )
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(db.forecast_evidence)
                .where(
                    db.forecast_evidence.c.artifact_sha256
                    == restored.standard.evidence.artifact_sha256
                )
            )
            == 4
        )
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(db.forecast_evidence_blobs)
                .where(
                    db.forecast_evidence_blobs.c.sha256
                    == restored.standard.evidence.artifact_sha256
                )
            )
            == 1
        )
        # This corrupts the expected manifest, not the dump or protected database.
        corrupt = {table: list(rows) for table, rows in restored.expected.items()}
        row = json.loads(corrupt["provisional_discharges"][0])
        row["fingerprint"] = hashlib.sha256(
            b"deliberately-wrong-expected-content"
        ).hexdigest()
        corrupt["provisional_discharges"][0] = json.dumps(row)
        with pytest.raises(
            AssertionError, match="restored content differs: provisional_discharges"
        ):
            assert_manifest(actual, corrupt)


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker", "sapphire_backup"])
def test_restored_principal_boundaries(restored: Restored, role: str) -> None:
    password = {
        "sapphire_api": "api-mixed",
        "sapphire_worker": "worker-mixed",
        "sapphire_backup": "backup-mixed",
    }[role]
    engine = sa.create_engine(restored.harness.role_url(role, password))
    try:
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT current_user")) == role
            if role == "sapphire_backup":
                assert_manifest(manifest(conn), restored.expected)
                return
            permitted = conn.execute(
                sa.select(
                    db.provisional_discharge_permissions.c.tenant_id,
                    db.provisional_discharge_permissions.c.state,
                )
            ).all()
            assert len(permitted) == 1 and permitted[0].state == "disabled"
            assert (
                str(permitted[0].tenant_id)
                == json.loads(
                    restored.expected["provisional_discharge_permissions"][0]
                )["tenant_id"]
            )
            store = PgForecastStore(conn)
            assert store.fetch_forecast(restored.standard.id) is not None
            assert store.fetch_forecast(restored.test.id) is None
            selected = store.fetch_forecast(restored.publication_id)
            assert selected is not None
            assert selected.status is ForecastStatus.SUPERSEDED
            rows, count = PgRejectedForecastStore(
                conn, transaction_factory=lambda: _transaction(conn)
            ).fetch_rejected_forecasts(
                restored.standard.station_id,
                _NOW - timedelta(days=3650),
                _NOW + timedelta(days=3650),
            )
            assert count == 1 and rows[0].data_use is ForecastDataUse.STANDARD
            for table, projection in (
                ("forecasts", "input_lineage"),
                ("forecasts", "*"),
                ("rejected_forecasts", "input_lineage"),
                ("rejected_forecasts", "*"),
                ("provisional_discharges", "*"),
                ("forecast_input_stations", "*"),
                ("measurement_feed_evidence", "*"),
                ("rating_reference_proofs", "*"),
                ("provisional_discharge_permissions", "permission_reference"),
                ("provisional_discharge_permissions", "inventory_digest"),
            ):
                with (
                    pytest.raises(DBAPIError, match="permission denied"),
                    conn.begin_nested(),
                ):
                    conn.execute(sa.text(f"SELECT {projection} FROM {table}"))
    finally:
        engine.dispose()


@pytest.mark.parametrize("role", ["owner", "sapphire_api"])
def test_test_publication_references_still_refused(
    restored: Restored, role: str
) -> None:
    engine = (
        restored.harness.owner_engine
        if role == "owner"
        else sa.create_engine(restored.harness.role_url(role, "api-mixed"))
    )
    try:
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT current_user")) == (
                "test" if role == "owner" else role
            )
            for table, column in (
                (db.forecast_publication_decisions, "forecast_id"),
                (db.forecast_publication_decisions, "replaced_forecast_id"),
                (db.forecast_publication_events, "forecast_id"),
                (db.forecast_publication_selections, "selected_forecast_id"),
            ):
                original = dict(conn.execute(sa.select(table)).mappings().one())
                original[column] = restored.test.id
                if "id" in original:
                    original["id"] = uuid4()
                with (
                    pytest.raises(
                        DBAPIError,
                        match="test forecast cannot enter normal publication",
                    ),
                    conn.begin_nested(),
                ):
                    conn.execute(sa.insert(table).values(**original))
            publication = PgForecastPublicationStore(
                conn, transaction_factory=lambda: _transaction(conn)
            )
            assert (
                publication.fetch_latest_selected_id(
                    restored.standard.station_id, "discharge"
                )
                is None
            )
            assert (
                publication.fetch_latest_selected_id(
                    restored.publication_station_id, "discharge"
                )
                == restored.publication_id
            )
    finally:
        if role != "owner":
            engine.dispose()


def test_narrow_health_counts_only_legitimate_publication_proofs(
    restored: Restored,
) -> None:
    from sapphire_flow.ops.protected_evidence_backup import (
        BackupHealth,
        ImageArchive,
        ProtectedBackupManifest,
    )
    from sapphire_flow.ops.publication_backup_health import write_health_projection
    from sapphire_flow.store.forecast_preservation_store import (
        PgForecastPreservationStore,
    )
    from sapphire_flow.store.forecast_values_integrity import forecast_values_integrity
    from sapphire_flow.types.forecast_preservation import (
        BackupProofStatus,
        PreservationAttestation,
    )

    published = restored.marker[0]
    backup_id = uuid4()
    image_hash = hashlib.sha256(
        b"synthetic archive descriptor, not real image archive"
    ).hexdigest()
    manifest_record = ProtectedBackupManifest(
        schema_version=2,
        backup_id=backup_id,
        created_at=_NOW,
        restored_at=_NOW,
        database_dump_sha256=restored.dump_hash,
        database_dump_bytes=restored.dump_size,
        image_archives=[
            ImageArchive(
                image_digest=IMAGE_DIGEST, archive_sha256=image_hash, byte_length=1
            )
        ],
        sample_forecast_id=restored.publication_id,
        capture_manifest_sha256=published.capture_manifest_sha256,
        snapshot_sha256=published.snapshot_sha256,
        forecast_values_sha256=published.forecast_values_sha256,
        artifact_sha256=published.artifact_sha256,
        runtime_image_digest=IMAGE_DIGEST,
        published_forecasts=[published],
    )
    manifest_hash = hashlib.sha256(
        manifest_record.model_dump_json().encode()
    ).hexdigest()
    health = BackupHealth(
        status=BackupProofStatus.VERIFIED,
        manifest=manifest_record,
        manifest_sha256=manifest_hash,
        reason=None,
    )
    with restored.harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "ALTER ROLE sapphire_publication_health LOGIN PASSWORD 'health-mixed'"
            )
        )
    health_url = restored.harness.role_url(
        "sapphire_publication_health", "health-mixed"
    )
    health_engine = sa.create_engine(health_url)
    worker_engine = sa.create_engine(
        restored.harness.role_url("sapphire_worker", "worker-mixed")
    )
    try:
        with health_engine.connect() as conn:
            assert (
                conn.scalar(sa.text("SELECT current_user"))
                == "sapphire_publication_health"
            )
            with (
                pytest.raises(DBAPIError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.text("SELECT id FROM forecasts"))
        # Actual worker writer validates the TEST chain, not an owner INSERT.
        with worker_engine.begin() as conn:
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            evidence = PgForecastStore(
                conn, data_use=ForecastDataUse.EXPIRED_RATING_TEST
            ).fetch_evidence(restored.test.id)
            assert evidence is not None and evidence.snapshot_sha256 is not None
            _, values_hash = forecast_values_integrity(conn, restored.test.id)
            PgForecastPreservationStore(conn).append(
                PreservationAttestation(
                    id=uuid4(),
                    forecast_id=restored.test.id,
                    backup_id=backup_id,
                    capture_manifest_sha256=hashlib.sha256(
                        evidence.manifest_json.encode()
                    ).hexdigest(),
                    snapshot_sha256=evidence.snapshot_sha256,
                    forecast_values_sha256=values_hash,
                    artifact_sha256=evidence.artifact_sha256,
                    runtime_image_digest=IMAGE_DIGEST,
                    backup_manifest_sha256=manifest_hash,
                    database_dump_sha256=restored.dump_hash,
                    image_archive_sha256=image_hash,
                    restored_at=_NOW,
                )
            )
        with worker_engine.connect() as conn:
            saved_test = PgForecastPreservationStore(conn).latest(restored.test.id)
            assert saved_test is not None
            assert saved_test.forecast_id == restored.test.id
            assert saved_test.backup_id == backup_id
            assert saved_test.forecast_values_sha256 == values_hash
            assert saved_test.snapshot_sha256 == evidence.snapshot_sha256
        test_item = published.model_copy(
            update={
                "forecast_id": restored.test.id,
                "capture_manifest_sha256": saved_test.capture_manifest_sha256,
                "snapshot_sha256": saved_test.snapshot_sha256,
                "artifact_sha256": saved_test.artifact_sha256,
                "forecast_values_sha256": saved_test.forecast_values_sha256,
            }
        )
        test_health = replace(
            health,
            manifest=manifest_record.model_copy(
                update={"published_forecasts": [test_item]}
            ),
        )
        with pytest.raises(ValueError, match="mismatched decision"):
            write_health_projection(
                database_url=health_url,
                health=test_health,
                checked_at=_NOW,
                retention_ready=True,
            )
        with health_engine.connect() as conn:
            state = conn.execute(sa.select(db.protected_backup_health)).mappings().one()
            assert state["status"] == "invalid" and state["pending_count"] == 1
            assert (
                conn.scalar(
                    sa.select(sa.func.count()).select_from(
                        db.protected_backup_forecast_proofs
                    )
                )
                == 0
            )
        with pytest.raises(ValueError, match="exact attestation"):
            write_health_projection(
                database_url=health_url,
                health=health,
                checked_at=_NOW,
                retention_ready=True,
            )
        with health_engine.connect() as conn:
            state = conn.execute(sa.select(db.protected_backup_health)).mappings().one()
            assert state["status"] == "invalid"
            assert state["pending_count"] == 1
            assert (
                conn.scalar(
                    sa.select(sa.func.count()).select_from(
                        db.protected_backup_forecast_proofs
                    )
                )
                == 0
            )
        with worker_engine.begin() as conn:
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            PgForecastPreservationStore(conn).append(
                PreservationAttestation(
                    id=uuid4(),
                    forecast_id=restored.publication_id,
                    backup_id=backup_id,
                    capture_manifest_sha256=published.capture_manifest_sha256,
                    snapshot_sha256=published.snapshot_sha256,
                    forecast_values_sha256=published.forecast_values_sha256,
                    artifact_sha256=published.artifact_sha256,
                    runtime_image_digest=IMAGE_DIGEST,
                    backup_manifest_sha256=manifest_hash,
                    database_dump_sha256=restored.dump_hash,
                    image_archive_sha256=image_hash,
                    restored_at=_NOW,
                )
            )
        for hours in (1, 2):
            backlog = write_health_projection(
                database_url=health_url,
                health=health,
                checked_at=_NOW + timedelta(hours=hours),
                retention_ready=True,
            )
            assert backlog.pending_count == 0
        with health_engine.connect() as conn:
            state = conn.execute(sa.select(db.protected_backup_health)).mappings().one()
            assert {
                name: state[name]
                for name in (
                    "status",
                    "pending_count",
                    "backup_id",
                    "restored_at",
                    "checked_at",
                    "target_separate",
                    "retention_ready",
                    "manifest_sha256",
                )
            } == {
                "status": "verified",
                "pending_count": 0,
                "backup_id": backup_id,
                "restored_at": _NOW,
                "checked_at": _NOW + timedelta(hours=2),
                "target_separate": True,
                "retention_ready": True,
                "manifest_sha256": manifest_hash,
            }
            rows = conn.execute(
                sa.select(
                    db.protected_backup_forecast_proofs.c.forecast_id,
                    db.protected_backup_forecast_proofs.c.publication_decision_id,
                )
            ).all()
            assert rows == [(restored.publication_id, restored.decision_id)]
        wrong = published.model_copy(update={"publication_decision_ids": [uuid4()]})
        mismatch = replace(
            health,
            manifest=manifest_record.model_copy(
                update={"published_forecasts": [wrong]}
            ),
        )
        with pytest.raises(ValueError, match="mismatched decision"):
            write_health_projection(
                database_url=health_url,
                health=mismatch,
                checked_at=_NOW + timedelta(hours=3),
                retention_ready=True,
            )
        with health_engine.connect() as conn:
            state = conn.execute(sa.select(db.protected_backup_health)).mappings().one()
            assert state["status"] == "invalid" and state["pending_count"] == 0
            assert (
                conn.execute(
                    sa.select(
                        db.protected_backup_forecast_proofs.c.forecast_id,
                        db.protected_backup_forecast_proofs.c.publication_decision_id,
                    )
                ).all()
                == rows
            )
    finally:
        health_engine.dispose()
        worker_engine.dispose()
        with restored.harness.owner_engine.begin() as conn:
            conn.execute(sa.text("ALTER ROLE sapphire_publication_health NOLOGIN"))


@pytest.mark.parametrize(
    "stderr",
    [
        b"Cannot connect to Docker daemon",
        b"permission denied",
        b"docker: command not found",
        b"Error: No such object: other-id",
    ],
)
def test_cleanup_rejects_unverified_docker_failure(stderr: bytes) -> None:
    with pytest.raises(AssertionError, match="absence not verified"):
        assert_container_absent(
            subprocess.CompletedProcess(["docker", "inspect"], 1, b"", stderr),
            "owned-id",
        )


@pytest.mark.parametrize(
    "message",
    [b"Error: No such object: owned-id\n", b"error: no such object: owned-id\n"],
)
def test_cleanup_accepts_only_explicit_owned_absence(message: bytes) -> None:
    assert_container_absent(
        subprocess.CompletedProcess(["docker", "inspect"], 1, b"[]", message),
        "owned-id",
    )


@pytest.mark.parametrize(
    "code,message",
    [
        (0, b"error: no such object: owned-id"),
        (2, b"error: no such object: owned-id"),
        (0, b"existing container"),
    ],
)
def test_cleanup_rejects_wrong_exit_or_present_object(
    code: int, message: bytes
) -> None:
    with pytest.raises(AssertionError, match="absence not verified"):
        assert_container_absent(
            subprocess.CompletedProcess(["docker", "inspect"], code, b"", message),
            "owned-id",
        )


@pytest.mark.parametrize("stop_mode", ["succeed", "fail-first"])
def test_cleanup_record_failures_do_not_mask_readiness_or_skip_owned_handles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_mode: Literal["succeed", "fail-first"],
) -> None:
    stopped: list[str] = []
    inspected: list[str] = []
    disposed: list[str] = []
    readiness = RuntimeError("readiness failed")

    def fail_readiness() -> None:
        raise readiness

    def stop(name: str) -> None:
        stopped.append(name)
        if name == "second" and stop_mode == "fail-first":
            raise RuntimeError("first cleanup stop failed")

    def record_fails(path: Path, content: str) -> int:
        raise OSError("record disk unavailable")

    def inspect_owned(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        assert argv[:2] == ["docker", "inspect"]
        inspected.append(argv[2])
        return subprocess.CompletedProcess(
            argv, 1, b"[]", f"error: no such object: {argv[2]}".encode()
        )

    first = cast(
        "PostgresContainer",
        SimpleNamespace(
            get_wrapped_container=lambda: SimpleNamespace(id="first"),
            stop=lambda: stop("first"),
        ),
    )
    second = cast(
        "PostgresContainer",
        SimpleNamespace(
            start=fail_readiness,
            get_wrapped_container=lambda: SimpleNamespace(id="second"),
            stop=lambda: stop("second"),
        ),
    )
    engine = cast(
        "sa.Engine", SimpleNamespace(dispose=lambda: disposed.append("engine"))
    )
    owned = [first]
    errors: list[Exception] = []
    monkeypatch.setattr(Path, "write_text", record_fails)
    monkeypatch.setattr(subprocess, "run", inspect_owned)
    with pytest.raises(RuntimeError, match="readiness failed") as caught:
        try:
            record_resources(tmp_path / "record.json", ["first"], "created", errors)
            start_owned(owned, second)
        finally:
            cleanup_owned(
                owned, [engine], [], tmp_path / "record.json", sys.exc_info()[1], errors
            )
    assert caught.value is readiness
    assert stopped == ["second", "first"]
    assert inspected == ["first", "second"]
    assert disposed == ["engine"]
    assert len([error for error in errors if isinstance(error, OSError)]) == 3
    assert "record disk unavailable" in " ".join(caught.value.__notes__)
    if stop_mode == "fail-first":
        assert "first cleanup stop failed" in " ".join(caught.value.__notes__)
