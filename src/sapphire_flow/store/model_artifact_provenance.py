# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Plan 157 T3 (G4/G7) — records that a `model_artifacts` row was
EXTERNALLY IMPORTED, not trained by SAP3.

A standalone helper + thin store class, mirroring
`store/model_artifact_lineage.py`'s pattern: NOT a widening of the
cross-cutting `ModelArtifactStore` Protocol — provenance recording is only
ever called from the import path (`services/model_import.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifact_provenance
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.model import ModelArtifactProvenance

if TYPE_CHECKING:
    from sapphire_flow.types.ids import ArtifactId


def record_artifact_provenance(
    conn: sa.Connection, provenance: ModelArtifactProvenance
) -> None:
    conn.execute(
        sa.insert(model_artifact_provenance).values(
            artifact_id=provenance.artifact_id,
            source_repository=provenance.source_repository,
            source_commit=provenance.source_commit,
            config_hash=provenance.config_hash,
            imported_at=provenance.imported_at,
            imported_by=provenance.imported_by,
            notes=provenance.notes,
        )
    )


def fetch_artifact_provenance(
    conn: sa.Connection, artifact_id: ArtifactId
) -> ModelArtifactProvenance | None:
    row = (
        conn.execute(
            sa.select(model_artifact_provenance).where(
                model_artifact_provenance.c.artifact_id == artifact_id
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return ModelArtifactProvenance(
        artifact_id=row["artifact_id"],
        source_repository=row["source_repository"],
        source_commit=row["source_commit"],
        config_hash=row["config_hash"],
        imported_at=utc_from_row(row["imported_at"]),
        imported_by=row["imported_by"],
        notes=row["notes"],
    )


class PgArtifactProvenanceStore:
    """Thin flow-facing adapter, mirrors `PgAuditLogStore`'s shape — the
    object `import_external_artifact` calls `.record(...)` on when running
    inside a real transaction (`AuditedWriter`, see
    `store/audited_writer.py::make_audited_stores`)."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def record(self, provenance: ModelArtifactProvenance) -> None:
        record_artifact_provenance(self._conn, provenance)

    def fetch(self, artifact_id: ArtifactId) -> ModelArtifactProvenance | None:
        return fetch_artifact_provenance(self._conn, artifact_id)
