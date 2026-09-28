from __future__ import annotations

import stat
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import (
    forecast_preservation_attestations,
    forecast_publication_decisions,
    protected_backup_forecast_proofs,
    protected_backup_health,
)
from sapphire_flow.types.forecast_preservation import BackupProofStatus

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from sapphire_flow.ops.protected_evidence_backup import BackupHealth


@dataclass(frozen=True, kw_only=True, slots=True)
class PublicationBacklog:
    pending_count: int
    overdue_count: int
    oldest_pending_at: datetime | None
    next_retry_at: datetime | None
    last_attempt_at: datetime | None
    failed_forecasts: tuple[UUID, ...]


def read_health_database_url(path: Path) -> str:
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PermissionError("publication health credential must be owner-only")
    url = path.read_text().strip()
    if sa.engine.make_url(url).username != "sapphire_publication_health":
        raise ValueError("publication health credential must use its dedicated role")
    return url


def write_health_projection(
    *,
    database_url: str,
    health: BackupHealth,
    checked_at: datetime,
    retention_ready: bool,
    retry_hours: int = 24,
    proof_window_hours: int = 36,
    attempted_at: datetime | None = None,
    failed_forecasts: tuple[UUID, ...] = (),
) -> PublicationBacklog:
    if sa.engine.make_url(database_url).username != "sapphire_publication_health":
        raise ValueError("publication health writer requires its dedicated role")
    manifest = health.manifest
    verified = health.status is BackupProofStatus.VERIFIED and retention_ready
    status = health.status.value if retention_ready else "invalid"
    if retry_hours < 1 or proof_window_hours < 1:
        raise ValueError("publication proof intervals must be positive")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            if verified and manifest is not None:
                _record_proofs(connection, health, checked_at)
            backlog = _read_backlog(
                connection,
                checked_at,
                retry_hours,
                proof_window_hours,
                attempted_at,
                failed_forecasts,
                mark_pending_failed=not verified,
            )
            values = {
                "id": 1,
                "status": status,
                "backup_id": manifest.backup_id if manifest else None,
                "restored_at": manifest.restored_at if manifest else None,
                "checked_at": checked_at,
                "target_separate": health.status is BackupProofStatus.VERIFIED,
                "retention_ready": retention_ready,
                "manifest_sha256": health.manifest_sha256 if verified else None,
                "pending_count": backlog.pending_count,
                "overdue_count": backlog.overdue_count,
                "oldest_pending_at": backlog.oldest_pending_at,
                "next_retry_at": backlog.next_retry_at,
                "last_attempt_at": backlog.last_attempt_at,
                "failed_forecasts": [str(item) for item in backlog.failed_forecasts],
            }
            connection.execute(
                pg_insert(protected_backup_health)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[protected_backup_health.c.id],
                    set_={key: value for key, value in values.items() if key != "id"},
                )
            )
            return backlog
    except Exception:
        with engine.begin() as connection:
            backlog = _read_backlog(
                connection,
                checked_at,
                retry_hours,
                proof_window_hours,
                attempted_at,
                failed_forecasts,
                mark_pending_failed=True,
            )
            connection.execute(
                pg_insert(protected_backup_health)
                .values(
                    id=1,
                    status="invalid",
                    backup_id=None,
                    restored_at=None,
                    checked_at=checked_at,
                    target_separate=False,
                    retention_ready=False,
                    manifest_sha256=None,
                    pending_count=backlog.pending_count,
                    overdue_count=backlog.overdue_count,
                    oldest_pending_at=backlog.oldest_pending_at,
                    next_retry_at=backlog.next_retry_at,
                    last_attempt_at=backlog.last_attempt_at,
                    failed_forecasts=[str(item) for item in backlog.failed_forecasts],
                )
                .on_conflict_do_update(
                    index_elements=[protected_backup_health.c.id],
                    set_={
                        "status": "invalid",
                        "backup_id": None,
                        "restored_at": None,
                        "checked_at": checked_at,
                        "target_separate": False,
                        "retention_ready": False,
                        "manifest_sha256": None,
                        "pending_count": backlog.pending_count,
                        "overdue_count": backlog.overdue_count,
                        "oldest_pending_at": backlog.oldest_pending_at,
                        "next_retry_at": backlog.next_retry_at,
                        "last_attempt_at": backlog.last_attempt_at,
                        "failed_forecasts": [
                            str(item) for item in backlog.failed_forecasts
                        ],
                    },
                )
            )
        raise
    finally:
        engine.dispose()


def _record_proofs(
    connection: sa.Connection, health: BackupHealth, checked_at: datetime
) -> None:
    manifest = health.manifest
    if manifest is None or health.manifest_sha256 is None:
        raise ValueError("verified backup is missing its manifest")
    for item in manifest.published_forecasts:
        attestation = (
            connection.execute(
                sa.select(forecast_preservation_attestations).where(
                    forecast_preservation_attestations.c.forecast_id
                    == item.forecast_id,
                    forecast_preservation_attestations.c.backup_id
                    == manifest.backup_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        image = next(
            archive
            for archive in manifest.image_archives
            if archive.image_digest == item.runtime_image_digest
        )
        expected = {
            "capture_manifest_sha256": item.capture_manifest_sha256,
            "snapshot_sha256": item.snapshot_sha256,
            "forecast_values_sha256": item.forecast_values_sha256,
            "artifact_sha256": item.artifact_sha256,
            "runtime_image_digest": item.runtime_image_digest,
            "backup_manifest_sha256": health.manifest_sha256,
            "database_dump_sha256": manifest.database_dump_sha256,
            "image_archive_sha256": image.archive_sha256,
            "restored_at": manifest.restored_at,
        }
        if attestation is None or any(
            attestation[key] != value for key, value in expected.items()
        ):
            raise ValueError(
                f"published forecast {item.forecast_id} lacks its exact attestation"
            )
        for decision_id in item.publication_decision_ids:
            decision = connection.execute(
                sa.select(
                    forecast_publication_decisions.c.forecast_id,
                    forecast_publication_decisions.c.action,
                    forecast_publication_decisions.c.preservation_at_publish,
                ).where(forecast_publication_decisions.c.id == decision_id)
            ).one_or_none()
            if (
                decision is None
                or decision.forecast_id != item.forecast_id
                or decision.action != "publish"
                or decision.preservation_at_publish != "backup_pending"
            ):
                raise ValueError(
                    f"published forecast {item.forecast_id} has mismatched decision"
                )
            connection.execute(
                pg_insert(protected_backup_forecast_proofs)
                .values(
                    id=uuid4(),
                    forecast_id=item.forecast_id,
                    attestation_id=attestation["id"],
                    backup_id=manifest.backup_id,
                    verified_at=checked_at,
                    publication_decision_id=decision_id,
                )
                .on_conflict_do_nothing(
                    constraint="uq_protected_proof_decision_attestation"
                )
            )


def _read_backlog(
    connection: sa.Connection,
    checked_at: datetime,
    retry_hours: int,
    proof_window_hours: int,
    attempted_at: datetime | None,
    failed_forecasts: tuple[UUID, ...],
    *,
    mark_pending_failed: bool,
) -> PublicationBacklog:
    decision = forecast_publication_decisions.alias("pending_decision")
    proof_exists = sa.exists(
        sa.select(protected_backup_forecast_proofs.c.id).where(
            protected_backup_forecast_proofs.c.publication_decision_id == decision.c.id,
            protected_backup_forecast_proofs.c.verified_at <= checked_at,
        )
    )
    unproved = (
        sa.select(
            decision.c.forecast_id,
            sa.func.min(decision.c.created_at).label("published_at"),
        )
        .where(
            decision.c.action == "publish",
            decision.c.preservation_at_publish == "backup_pending",
            ~proof_exists,
        )
        .group_by(decision.c.forecast_id)
        .subquery()
    )
    rows = connection.execute(
        sa.select(unproved.c.forecast_id, unproved.c.published_at).order_by(
            unproved.c.published_at, unproved.c.forecast_id
        )
    ).all()
    count = len(rows)
    previous = connection.execute(
        sa.select(
            protected_backup_health.c.failed_forecasts,
            protected_backup_health.c.last_attempt_at,
        ).where(protected_backup_health.c.id == 1)
    ).one_or_none()
    last_attempt_at = attempted_at or (
        previous.last_attempt_at if previous is not None else None
    )
    pending_ids = {row.forecast_id for row in rows}
    overdue_ids = {
        row.forecast_id
        for row in rows
        if row.published_at < checked_at - timedelta(hours=proof_window_hours)
    }
    previous_failures = (
        cast("list[str]", previous.failed_forecasts) if previous is not None else []
    )
    failures = set(failed_forecasts).intersection(pending_ids) | {
        parsed for item in previous_failures if (parsed := UUID(item)) in pending_ids
    }
    failures.update(overdue_ids)
    if mark_pending_failed:
        failures.update(row.forecast_id for row in rows)
    return PublicationBacklog(
        pending_count=count,
        overdue_count=len(overdue_ids),
        oldest_pending_at=rows[0].published_at if rows else None,
        next_retry_at=(
            max(last_attempt_at or rows[0].published_at, rows[0].published_at)
            + timedelta(hours=retry_hours)
            if rows
            else None
        ),
        last_attempt_at=last_attempt_at,
        failed_forecasts=tuple(sorted(failures)),
    )
