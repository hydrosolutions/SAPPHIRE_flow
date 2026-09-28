from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.db.metadata import (
    forecast_evidence,
    forecast_preservation_attestations,
    forecast_publication_decisions,
    protected_backup_forecast_proofs,
    protected_backup_health,
)
from sapphire_flow.ops.protected_evidence_backup import (
    BackupHealth,
    ImageArchive,
    ProtectedBackupManifest,
    PublishedForecastProof,
)
from sapphire_flow.ops.publication_backup_health import write_health_projection
from sapphire_flow.store.forecast_values_integrity import forecast_values_integrity
from sapphire_flow.types.forecast_preservation import BackupProofStatus
from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness
from tests.integration.store.test_forecast_publication_store import (
    _NOW,
    _publish,
    _seed,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


def test_host_projection_is_attributable_idempotent_and_fail_closed(
    monkeypatch: MonkeyPatch,
) -> None:
    image_digest = "sha256:" + "a" * 64
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire",
    ) as postgres:
        owner_url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", owner_url)
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", owner_url)
        command.upgrade(config, "head")
        owner = sa.create_engine(owner_url)
        try:
            with owner.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(sa.text("CREATE DATABASE prefect"))
            harness = _RoleBootstrapHarness(postgres, owner)
            bootstrap = harness.run_bootstrap("api-pw", "worker-pw", "backup-pw")
            assert bootstrap.returncode == 0, bootstrap.stderr
            with owner.begin() as conn:
                store, principal, forecast_id, _ = _seed(conn)
                decision = _publish(store, principal, forecast_id)
                evidence = (
                    conn.execute(
                        sa.select(forecast_evidence).where(
                            forecast_evidence.c.forecast_id == forecast_id
                        )
                    )
                    .mappings()
                    .one()
                )
                _, values_hash = forecast_values_integrity(conn, forecast_id)
                capture_hash = hashlib.sha256(
                    evidence["manifest_json"].encode()
                ).hexdigest()
                item = PublishedForecastProof(
                    forecast_id=forecast_id,
                    publication_decision_ids=[decision.id],
                    published_at=decision.created_at,
                    capture_manifest_sha256=capture_hash,
                    snapshot_sha256=evidence["snapshot_sha256"],
                    forecast_values_sha256=values_hash,
                    artifact_sha256=evidence["artifact_sha256"],
                    runtime_image_digest=image_digest,
                )
            backup_id = uuid4()
            manifest_hash = "b" * 64
            manifest = ProtectedBackupManifest(
                schema_version=2,
                backup_id=backup_id,
                created_at=_NOW,
                restored_at=_NOW,
                database_dump_sha256="c" * 64,
                database_dump_bytes=1,
                image_archives=[
                    ImageArchive(
                        image_digest=image_digest,
                        archive_sha256="d" * 64,
                        byte_length=1,
                    )
                ],
                sample_forecast_id=forecast_id,
                capture_manifest_sha256=item.capture_manifest_sha256,
                snapshot_sha256=item.snapshot_sha256,
                forecast_values_sha256=item.forecast_values_sha256,
                artifact_sha256=item.artifact_sha256,
                runtime_image_digest=image_digest,
                published_forecasts=[item],
            )
            health = BackupHealth(
                status=BackupProofStatus.VERIFIED,
                manifest=manifest,
                manifest_sha256=manifest_hash,
                reason=None,
            )
            with owner.begin() as conn:
                conn.execute(
                    sa.text(
                        "ALTER ROLE sapphire_publication_health LOGIN "
                        "PASSWORD 'host-health-test'"
                    )
                )
            host_url = harness.role_url(
                "sapphire_publication_health", "host-health-test"
            )
            with pytest.raises(ValueError, match="exact attestation"):
                write_health_projection(
                    database_url=host_url,
                    health=health,
                    checked_at=_NOW,
                    retention_ready=True,
                )
            with owner.connect() as conn:
                invalid = (
                    conn.execute(sa.select(protected_backup_health)).mappings().one()
                )
                assert invalid["status"] == "invalid"
                assert invalid["pending_count"] == 1
                assert invalid["failed_forecasts"] == [str(forecast_id)]

            with owner.begin() as conn:
                conn.execute(
                    sa.insert(forecast_preservation_attestations).values(
                        id=uuid4(),
                        forecast_id=forecast_id,
                        backup_id=backup_id,
                        capture_manifest_sha256=item.capture_manifest_sha256,
                        snapshot_sha256=item.snapshot_sha256,
                        forecast_values_sha256=item.forecast_values_sha256,
                        artifact_sha256=item.artifact_sha256,
                        runtime_image_digest=image_digest,
                        backup_manifest_sha256=manifest_hash,
                        database_dump_sha256=manifest.database_dump_sha256,
                        image_archive_sha256="d" * 64,
                        restored_at=manifest.restored_at,
                    )
                )
            for hours in (1, 2):
                backlog = write_health_projection(
                    database_url=host_url,
                    health=health,
                    checked_at=_NOW + timedelta(hours=hours),
                    retention_ready=True,
                )
                assert backlog.pending_count == 0
            with owner.connect() as conn:
                assert (
                    conn.scalar(
                        sa.select(sa.func.count()).select_from(
                            protected_backup_forecast_proofs
                        )
                    )
                    == 1
                )

            with owner.begin() as conn:
                old = (
                    conn.execute(
                        sa.select(forecast_publication_decisions).where(
                            forecast_publication_decisions.c.id == decision.id
                        )
                    )
                    .mappings()
                    .one()
                )
                later = dict(old)
                later.update(
                    id=uuid4(),
                    created_at=_NOW + timedelta(hours=3),
                    idempotency_key="same-forecast-after-dump",
                    selection_version=2,
                    request_sha256=hashlib.sha256(b"later").hexdigest(),
                )
                conn.execute(sa.insert(forecast_publication_decisions).values(**later))
            backlog = write_health_projection(
                database_url=host_url,
                health=health,
                checked_at=_NOW + timedelta(hours=4),
                retention_ready=True,
            )
            assert backlog.pending_count == 1
            assert backlog.overdue_count == 0
            assert backlog.failed_forecasts == ()
            attempted = write_health_projection(
                database_url=host_url,
                health=health,
                checked_at=_NOW + timedelta(hours=5),
                retention_ready=True,
                attempted_at=_NOW + timedelta(hours=5),
            )
            retry_at = attempted.next_retry_at
            assert attempted.last_attempt_at == _NOW + timedelta(hours=5)
            assert retry_at == _NOW + timedelta(hours=29)
            overdue = write_health_projection(
                database_url=host_url,
                health=health,
                checked_at=_NOW + timedelta(hours=40),
                retention_ready=True,
            )
            assert overdue.pending_count == 1
            assert overdue.overdue_count == 1
            assert overdue.failed_forecasts == (forecast_id,)
            assert overdue.next_retry_at == retry_at
            with owner.connect() as conn:
                assert (
                    conn.scalar(
                        sa.select(sa.func.count()).select_from(
                            protected_backup_forecast_proofs
                        )
                    )
                    == 1
                )
        finally:
            owner.dispose()
