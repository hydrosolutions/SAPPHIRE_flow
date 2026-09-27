from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import protected_backup_health
from sapphire_flow.types.forecast_preservation import BackupProofStatus

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from sapphire_flow.ops.protected_evidence_backup import BackupHealth


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
) -> None:
    if sa.engine.make_url(database_url).username != "sapphire_publication_health":
        raise ValueError("publication health writer requires its dedicated role")
    manifest = health.manifest
    verified = health.status is BackupProofStatus.VERIFIED and retention_ready
    status = health.status.value if retention_ready else "invalid"
    values = {
        "id": 1,
        "status": status,
        "backup_id": manifest.backup_id if manifest else None,
        "restored_at": manifest.restored_at if manifest else None,
        "checked_at": checked_at,
        "target_separate": health.status is BackupProofStatus.VERIFIED,
        "retention_ready": retention_ready,
        "manifest_sha256": health.manifest_sha256 if verified else None,
    }
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                pg_insert(protected_backup_health)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[protected_backup_health.c.id],
                    set_={key: value for key, value in values.items() if key != "id"},
                )
            )
    finally:
        engine.dispose()
