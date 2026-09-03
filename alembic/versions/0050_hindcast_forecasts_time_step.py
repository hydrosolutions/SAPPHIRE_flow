"""hindcast_forecasts.time_step_seconds column (Plan 228 review fixer round)

Revision ID: 0050
Revises: 0049
Create Date: 2026-09-01

`hindcast_forecasts` never persisted the ensemble's own `time_step`
(`types/ensemble.py`) — the value known and set at construction time by
whoever built the `HindcastForecast`. On read, `PgHindcastStore.
_reconstruct_ensemble` (`store/hindcast_store.py`) instead INFERRED it from
the gap between stored `valid_time`s, defaulting to a hardcoded
`timedelta(hours=1)` whenever a hindcast has only ONE lead step (the normal
shape for any horizon-1 model) — silently wrong for a horizon-1 DAILY model,
whose true `time_step` is 1 day, not 1 hour.

Adds `time_step_seconds` (`Integer`, `NOT NULL`) and makes it the
authoritative value on read, replacing the gap-inference entirely. Backfills
every existing row to `86400` (1 day): every hindcast row in this system
today is daily (`linear_regression_daily`, `nwp_regression`), and every
pre-Plan-228 row is already being marked superseded regardless (D3) — the
backfill value only has to be a safe placeholder, not a per-row-accurate
reconstruction.

Downgrade drops the column; a subsequent read of a pre-migration-shaped row
would go back through gap-inference (unchanged code path once the column and
its use are reverted alongside it).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_TIME_STEP_SECONDS = 86400  # 1 day — every hindcast row today is daily


def upgrade() -> None:
    op.add_column(
        "hindcast_forecasts",
        sa.Column(
            "time_step_seconds",
            sa.Integer,
            nullable=False,
            server_default=str(_DEFAULT_TIME_STEP_SECONDS),
        ),
    )
    op.create_check_constraint(
        "ck_hindcast_forecasts_time_step_seconds_positive",
        "hindcast_forecasts",
        "time_step_seconds > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_hindcast_forecasts_time_step_seconds_positive",
        "hindcast_forecasts",
        type_="check",
    )
    op.drop_column("hindcast_forecasts", "time_step_seconds")
