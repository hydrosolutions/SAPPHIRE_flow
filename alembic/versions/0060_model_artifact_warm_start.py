"""Warm-start (fine-tune) provenance: what an artifact was derived from (Plan 399 T4).

A SIDE TABLE, not columns on `model_artifacts` — the same reasoning as
`model_artifact_basin_versions` (Plan 120): `model_artifacts` gains no new column.

The donor FK is RESTRICT, deliberately. CASCADE would delete this row when a base
artifact is deleted, which passes a naive "no orphan remains" check while
destroying the only answer to "what was this fine-tuned from?" — the question the
table exists to answer. Supersession is unaffected: it marks status, not deletion.

Revision ID: 0060
Revises: 0059
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_artifact_warm_start",
        sa.Column(
            "model_artifact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("model_artifacts.id"),
            primary_key=True,
        ),
        sa.Column(
            "base_artifact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("model_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Resolved from the DONOR's own provenance — never by hashing whatever
        # template is installed today. NULL carries a reason.
        sa.Column("base_config_path", sa.Text(), nullable=True),
        sa.Column("base_config_sha256", sa.Text(), nullable=True),
        sa.Column("base_config_unknown_reason", sa.Text(), nullable=True),
        # UNKNOWN is a real state here, distinct from known-absent: an imported
        # donor's external training params are not recoverable from SAP3.
        sa.Column("base_params_path", sa.Text(), nullable=True),
        sa.Column("base_params_unknown_reason", sa.Text(), nullable=True),
        # The config THIS run was given, verbatim, so "which strategy produced
        # this artifact?" is answerable later.
        sa.Column(
            "run_config",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # "What was fine-tuned FROM this artifact?" keys on the donor, which the PK
    # cannot serve.
    op.create_index(
        "ix_model_artifact_warm_start_base",
        "model_artifact_warm_start",
        ["base_artifact_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_artifact_warm_start_base", "model_artifact_warm_start")
    op.drop_table("model_artifact_warm_start")
