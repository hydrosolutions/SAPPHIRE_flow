"""orphan header cleanup — delete forecast and hindcast headers with no value rows

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-10

IMPORTANT — destructive data migration.  Before running in production:
1. Run the dry-run queries below to confirm blast radius.
2. Take a full database backup.
3. Pause all flows.
4. Run `alembic upgrade head` during the maintenance window.
5. Restart flows.

Dry-run (run manually before backup):
    SELECT count(*) FROM forecasts f
    WHERE NOT EXISTS (
        SELECT 1 FROM forecast_values fv WHERE fv.forecast_id = f.id
    );

    SELECT count(*) FROM hindcast_forecasts hf
    WHERE NOT EXISTS (
        SELECT 1 FROM hindcast_values hv WHERE hv.hindcast_forecast_id = hf.id
    );

station_groups is EXCLUDED — empty groups may be intentional.
Check manually if needed:
    SELECT sg.id, sg.name, sg.created_at
    FROM station_groups sg
    WHERE NOT EXISTS (
        SELECT 1 FROM station_group_members sgm WHERE sgm.group_id = sg.id
    );
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM forecasts f
        WHERE NOT EXISTS (
            SELECT 1 FROM forecast_values fv WHERE fv.forecast_id = f.id
        )
        """
    )
    op.execute(
        """
        DELETE FROM hindcast_forecasts hf
        WHERE NOT EXISTS (
            SELECT 1 FROM hindcast_values hv WHERE hv.hindcast_forecast_id = hf.id
        )
        """
    )


def downgrade() -> None:
    # Irreversible data delete — orphan rows cannot be reconstructed.
    # Recovery is via DB restore + previous image tag (see docs/standards/cicd.md),
    # not via Alembic downgrade.
    pass
