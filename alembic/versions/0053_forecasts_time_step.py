"""forecasts.time_step_seconds column (Plan 241 T4)

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-04

The SAME defect Plan 228 fixed for `hindcast_forecasts` in revision 0050, left
unfixed on the operational `forecasts` table.

`PgForecastStore` never persisted the ensemble's own `time_step` — the value
known and set at construction time by whoever built the forecast. On read it
INFERRED it from the gap between stored `valid_time`s, defaulting to a hardcoded
`timedelta(hours=1)` whenever a forecast has only ONE step, which is silently
wrong for a one-step DAILY forecast. Measured against `main` (a20e38b3) before
this change: store a one-step daily forecast, read it back, get `1:00:00`.

WHY NOW. Plan 241 lets a model declare `horizon_semantics=AT_MOST` with
`min_future_steps=1`, so a one-step forecast becomes routinely reachable.

NULLABLE-FIRST — deliberately, and this differs from 0050.
====================================================================
`docs/standards/cicd.md` (§Rollback) requires a migration to be
backwards-compatible for one version — "additive only: new columns nullable" —
so the previous image tag can run against the new schema during the migration
window. `station_weather_sources.role` (Plan 115a/115c) is the worked precedent:
add nullable with a NULL-tolerant check, tighten in a LATER release.

So this revision adds NO server default and performs NO backfill:

  * existing rows keep `NULL`, and the reader goes on inferring their cadence
    from their own timestamps exactly as it does today;
  * every row written from now on carries its true, declared cadence;
  * a later release backfills and tightens to NOT NULL, once the rollback
    window has closed.

⛔ NO BACKFILL, DELIBERATELY. An earlier draft stamped every row `86400` on the
reasoning that all operational models are daily. That value was asserted from
the model list and never measured — the staging host was off-LAN — while
non-daily forecasts are a fully supported shape. A computed backfill was then
considered and rejected too: `forecast_values` holds one row per member per
`valid_time` so a naive gap query double-counts, an intersection may be
non-uniform, and a single-timestamp row has no derivable delta at all. Leaving
those rows NULL is the only honest option; inventing a cadence for them is the
very defect this revision exists to remove.

The check constraint is NULL-tolerant for the same reason, and is NAMED to match
`db/metadata.py` exactly — there is no `naming_convention` on that MetaData, so
an unnamed constraint would emit an anonymous CHECK that Postgres auto-names,
whereupon this revision's own downgrade would fail against a `create_all` schema
and autogenerate would see a permanent phantom diff (the drift class revision
0051 exists to repair).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "forecasts",
        sa.Column("time_step_seconds", sa.Integer, nullable=True),
    )
    op.create_check_constraint(
        "ck_forecasts_time_step_seconds_positive",
        "forecasts",
        "time_step_seconds IS NULL OR time_step_seconds > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_forecasts_time_step_seconds_positive", "forecasts", type_="check"
    )
    op.drop_column("forecasts", "time_step_seconds")
