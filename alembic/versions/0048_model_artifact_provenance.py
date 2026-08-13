"""model_artifact_provenance table (Plan 157 T3)

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-13

Records that a `model_artifacts` row was EXTERNALLY IMPORTED, not trained
by SAP3 (G4/G7 — no path previously registered an externally-trained
artifact, and nowhere recorded what such an artifact *is*). Presence of a
row keyed by `artifact_id` is the provenance signal; absence means
SAP3-trained. Deliberately additive-only: `model_artifacts.
training_period_start/training_period_end/trained_at` stay non-nullable —
an import supplies the artifact's real external training-completion
instant for all three rather than widening the schema (see
`services/model_import.py`). No FK cascade behaviour beyond the plain
`REFERENCES` — a provenance row is meaningless without its artifact and
should never outlive it, but nothing in this migration deletes
`model_artifacts` rows, so no ON DELETE clause is needed yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_artifact_provenance",
        sa.Column(
            "artifact_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("model_artifacts.id"),
            primary_key=True,
        ),
        sa.Column("source_repository", sa.Text, nullable=True),
        sa.Column("source_commit", sa.Text, nullable=True),
        sa.Column("config_hash", sa.Text, nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("model_artifact_provenance")
