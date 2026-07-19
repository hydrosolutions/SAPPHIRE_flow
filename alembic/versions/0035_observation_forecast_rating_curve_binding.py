"""observation + forecast rating-curve binding (Plan 035 Task 2)

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-19

Catches the database up to the ``Observation``/``RawObservation`` types (which
already carry ``rating_curve_id`` + ``rating_curve_correction_version``) and
binds a forecast to the rating curve active for its station at ``issued_at``:

* ``observations``: add nullable ``rating_curve_id`` +
  ``rating_curve_correction_version``; widen the ``source`` CHECK to admit
  ``'rating_curve_derived'`` and ``'component_derived'`` (Plan 015
  forward-compat); add ``(station_id, source, timestamp)`` index for Flow 12.
* ``forecasts``: add nullable ``rating_curve_id`` + index.
* Same-station integrity: a composite FK ``(station_id, rating_curve_id) ->
  rating_curves(station_id, id)`` on both tables ensures a row can only bind a
  curve for its OWN station. MATCH SIMPLE skips the check when
  ``rating_curve_id IS NULL`` (directly-measured rows). This needs a UNIQUE
  ``(id, station_id)`` on ``rating_curves`` as the FK target.

The v0 ``source`` CHECK was created anonymously (0001_v0_schema.py) so PostgreSQL
named it ``observations_source_check``. We drop by both the Postgres default and
the convention name (IF EXISTS, per the 0002 precedent) before recreating it
named. All existing rows keep NULL provenance and ``source='measured'|'manual_import'``.

Downgrade coerces the newly admitted source values back to ``'measured'`` before
restoring the two-value CHECK (mirrors 0026's runoff_only coercion) so the
migration is reversible even after rating-curve-derived rows exist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # FK target for the composite same-station constraints below.
    op.create_unique_constraint(
        "uq_rating_curves_id_station", "rating_curves", ["id", "station_id"]
    )

    op.add_column(
        "observations",
        sa.Column("rating_curve_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "observations",
        sa.Column("rating_curve_correction_version", sa.Text, nullable=True),
    )
    op.create_foreign_key(
        "fk_observations_rating_curve_station",
        "observations",
        "rating_curves",
        ["station_id", "rating_curve_id"],
        ["station_id", "id"],
    )
    # v0 source CHECK is anonymous -> Postgres default name; drop both possible
    # names defensively before recreating it named (0002 precedent).
    op.execute(
        "ALTER TABLE observations DROP CONSTRAINT IF EXISTS ck_observations_source"
    )
    op.execute(
        "ALTER TABLE observations DROP CONSTRAINT IF EXISTS observations_source_check"
    )
    op.create_check_constraint(
        "ck_observations_source",
        "observations",
        "source IN ('measured', 'manual_import', 'rating_curve_derived', "
        "'component_derived')",
    )
    op.create_index(
        "ix_observations_station_source_ts",
        "observations",
        ["station_id", "source", "timestamp"],
    )

    op.add_column(
        "forecasts",
        sa.Column("rating_curve_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_forecasts_rating_curve_station",
        "forecasts",
        "rating_curves",
        ["station_id", "rating_curve_id"],
        ["station_id", "id"],
    )
    op.create_index(
        "ix_forecasts_rating_curve",
        "forecasts",
        ["rating_curve_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_forecasts_rating_curve", table_name="forecasts")
    op.drop_constraint(
        "fk_forecasts_rating_curve_station", "forecasts", type_="foreignkey"
    )
    op.drop_column("forecasts", "rating_curve_id")

    op.drop_index("ix_observations_station_source_ts", table_name="observations")
    # Rows admitted by the widened CHECK would violate the restored two-value
    # CHECK; coerce them back to 'measured' first (mirrors 0026 runoff_only).
    op.execute(
        sa.text(
            "UPDATE observations SET source = 'measured' "
            "WHERE source IN ('rating_curve_derived', 'component_derived')"
        )
    )
    op.execute(
        "ALTER TABLE observations DROP CONSTRAINT IF EXISTS ck_observations_source"
    )
    op.execute(
        "ALTER TABLE observations DROP CONSTRAINT IF EXISTS observations_source_check"
    )
    # Restore the original anonymous-equivalent name (observations_source_check)
    # so the pre-0035 schema is reproduced exactly.
    op.execute(
        "ALTER TABLE observations ADD CONSTRAINT observations_source_check "
        "CHECK (source IN ('measured', 'manual_import'))"
    )
    op.drop_constraint(
        "fk_observations_rating_curve_station", "observations", type_="foreignkey"
    )
    op.drop_column("observations", "rating_curve_correction_version")
    op.drop_column("observations", "rating_curve_id")

    op.drop_constraint("uq_rating_curves_id_station", "rating_curves", type_="unique")
