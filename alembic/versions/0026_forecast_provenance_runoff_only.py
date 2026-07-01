"""forecast provenance: nullable nwp_cycle_reference_time + runoff_only source

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-01

epic-088 M4 forecast provenance. Runoff-only forecasts have no NWP cycle, so
``forecasts.nwp_cycle_reference_time`` becomes NULLABLE and the
``nwp_cycle_source`` CHECK admits the third value ``'runoff_only'`` alongside
``'primary'`` and ``'fallback'``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "forecasts",
        "nwp_cycle_reference_time",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.drop_constraint("ck_forecasts_nwp_cycle_source", "forecasts")
    op.create_check_constraint(
        "ck_forecasts_nwp_cycle_source",
        "forecasts",
        "nwp_cycle_source IN ('primary', 'fallback', 'runoff_only')",
    )


def downgrade() -> None:
    # Runoff-only rows admitted by the upgrade have no NWP cycle
    # (nwp_cycle_reference_time IS NULL, nwp_cycle_source = 'runoff_only').
    # Coerce them back into the pre-0026 two-value world BEFORE restoring the
    # old CHECK / NOT NULL, else re-creating those constraints fails on the very
    # data this migration made representable. issued_at (NOT NULL) is the nominal
    # forecast issue time, the closest stand-in for a missing cycle reference.
    op.execute(
        sa.text(
            "UPDATE forecasts SET nwp_cycle_reference_time = issued_at "
            "WHERE nwp_cycle_reference_time IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE forecasts SET nwp_cycle_source = 'primary' "
            "WHERE nwp_cycle_source = 'runoff_only'"
        )
    )
    op.drop_constraint("ck_forecasts_nwp_cycle_source", "forecasts")
    op.create_check_constraint(
        "ck_forecasts_nwp_cycle_source",
        "forecasts",
        "nwp_cycle_source IN ('primary', 'fallback')",
    )
    op.alter_column(
        "forecasts",
        "nwp_cycle_reference_time",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
