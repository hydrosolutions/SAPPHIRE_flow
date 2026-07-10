"""Hindcast deduplication constraint: unique index + values FK index.

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-10

IMPORTANT — destructive data migration. Before running in production:
1. Run the dry-run queries below to confirm blast radius.
2. Take a full database backup.
3. Pause all flows.
4. Run `alembic upgrade head` during the maintenance window.
5. Restart flows.

Dry-run (run manually before backup):
    -- Count duplicate hindcast_forecasts that will be deleted:
    SELECT count(*) FROM hindcast_forecasts hf
    WHERE EXISTS (
        SELECT 1 FROM hindcast_forecasts hf2
        WHERE hf2.station_id = hf.station_id
          AND hf2.model_id = hf.model_id
          AND hf2.hindcast_step = hf.hindcast_step
          AND hf2.parameter = hf.parameter
          AND hf2.hindcast_run_id = hf.hindcast_run_id
          AND hf2.forcing_type = hf.forcing_type
          AND (hf2.created_at < hf.created_at
               OR (hf2.created_at = hf.created_at AND hf2.id < hf.id))
    );

    -- Count duplicate hindcast_values that will be deleted (cascade from above):
    SELECT count(*) FROM hindcast_values hv
    WHERE hv.hindcast_forecast_id IN (
        SELECT hf.id FROM hindcast_forecasts hf
        WHERE EXISTS (
            SELECT 1 FROM hindcast_forecasts hf2
            WHERE hf2.station_id = hf.station_id
              AND hf2.model_id = hf.model_id
              AND hf2.hindcast_step = hf.hindcast_step
              AND hf2.parameter = hf.parameter
              AND hf2.hindcast_run_id = hf.hindcast_run_id
              AND hf2.forcing_type = hf.forcing_type
              AND (hf2.created_at < hf.created_at
                   OR (hf2.created_at = hf.created_at AND hf2.id < hf.id))
        )
    );

The row deletes are irreversible by design (as in migration 0028). Recovery
from the deletes is via DB restore, not Alembic downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Count duplicates before deleting (visible to operator, unlike RAISE NOTICE)
    n = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM hindcast_forecasts hf WHERE EXISTS ("
                "  SELECT 1 FROM hindcast_forecasts hf2 WHERE "
                "  hf2.station_id = hf.station_id AND hf2.model_id = hf.model_id "
                "  AND hf2.hindcast_step = hf.hindcast_step "
                "  AND hf2.parameter = hf.parameter "
                "  AND hf2.hindcast_run_id = hf.hindcast_run_id "
                "  AND hf2.forcing_type = hf.forcing_type "
                "  AND (hf2.created_at < hf.created_at "
                "       OR (hf2.created_at = hf.created_at AND hf2.id < hf.id)))"
            )
        )
        .scalar_one()
    )
    print(f"plan-040: {n} duplicate hindcast_forecasts rows will be deleted")  # noqa: T201

    # ── 1. Delete duplicate hindcast_values (cascade from duplicate headers)
    op.execute(
        sa.text(
            "DELETE FROM hindcast_values"
            " WHERE hindcast_forecast_id IN ("
            "   SELECT hf.id FROM hindcast_forecasts hf"
            "   WHERE EXISTS ("
            "     SELECT 1 FROM hindcast_forecasts hf2"
            "     WHERE hf2.station_id = hf.station_id"
            "       AND hf2.model_id = hf.model_id"
            "       AND hf2.hindcast_step = hf.hindcast_step"
            "       AND hf2.parameter = hf.parameter"
            "       AND hf2.hindcast_run_id = hf.hindcast_run_id"
            "       AND hf2.forcing_type = hf.forcing_type"
            "       AND (hf2.created_at < hf.created_at"
            "            OR (hf2.created_at = hf.created_at AND hf2.id < hf.id))"
            "   )"
            " )"
        )
    )

    # ── 2. Delete duplicate hindcast_forecasts
    # Keep earliest created_at, tie-broken by id.
    op.execute(
        sa.text(
            "DELETE FROM hindcast_forecasts hf"
            " WHERE EXISTS ("
            "   SELECT 1 FROM hindcast_forecasts hf2"
            "   WHERE hf2.station_id = hf.station_id"
            "     AND hf2.model_id = hf.model_id"
            "     AND hf2.hindcast_step = hf.hindcast_step"
            "     AND hf2.parameter = hf.parameter"
            "     AND hf2.hindcast_run_id = hf.hindcast_run_id"
            "     AND hf2.forcing_type = hf.forcing_type"
            "     AND (hf2.created_at < hf.created_at"
            "          OR (hf2.created_at = hf.created_at AND hf2.id < hf.id))"
            " )"
        )
    )

    # ── 3. Create unique index on hindcast_forecasts (6-col natural key)
    op.create_index(
        "uq_hindcast_forecasts_station_model_step_param_run",
        "hindcast_forecasts",
        [
            "station_id",
            "model_id",
            "hindcast_step",
            "parameter",
            "hindcast_run_id",
            "forcing_type",
        ],
        unique=True,
        if_not_exists=True,
    )

    # ── 4. Create FK index on hindcast_values for efficient fetch-by-id
    op.create_index(
        "ix_hindcast_values_forecast_id",
        "hindcast_values",
        ["hindcast_forecast_id"],
        if_not_exists=True,
    )

    # ── 5. Drop now-redundant non-unique indexes
    # Both are strict prefixes of the new 6-col unique index.
    op.drop_index(
        "ix_hindcast_forecasts_station_model_step",
        table_name="hindcast_forecasts",
        if_exists=True,
    )
    op.drop_index(
        "ix_hindcast_forecasts_station_model_step_param",
        table_name="hindcast_forecasts",
        if_exists=True,
    )


def downgrade() -> None:
    # Drop the indexes added in upgrade
    op.drop_index(
        "uq_hindcast_forecasts_station_model_step_param_run",
        table_name="hindcast_forecasts",
        if_exists=True,
    )
    op.drop_index(
        "ix_hindcast_values_forecast_id",
        table_name="hindcast_values",
        if_exists=True,
    )

    # Recreate the two non-unique indexes that upgrade dropped
    op.create_index(
        "ix_hindcast_forecasts_station_model_step",
        "hindcast_forecasts",
        ["station_id", "model_id", "hindcast_step"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_hindcast_forecasts_station_model_step_param",
        "hindcast_forecasts",
        ["station_id", "model_id", "hindcast_step", "parameter"],
        if_not_exists=True,
    )

    # The row deletes in upgrade are irreversible by design.
    # Recovery from the deletes is via DB restore, not Alembic downgrade.
