"""forecasts.time_step_seconds column (Plan 241 T4)

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-04

The SAME defect Plan 228 fixed for `hindcast_forecasts` in revision 0050, left
unfixed on the operational `forecasts` table.

`PgForecastStore` never persisted the ensemble's own `time_step` — the value
known and set at construction time by whoever built the forecast. On read it
INFERRED it from the gap between stored `valid_time`s
(`store/forecast_store.py`), defaulting to a hardcoded `timedelta(hours=1)`
whenever a forecast has only ONE step:

    time_step = (
        timedelta(seconds=int(valid_times[1] - valid_times[0]))
        if len(valid_times) >= 2
        else timedelta(hours=1)
    )

...which is silently wrong for a one-step DAILY forecast, whose true
`time_step` is 1 day. The API and the Forecast Lab export then report the
fabricated hourly cadence as truth.

WHY NOW. The defect was latent while every model was held to a multi-step
horizon: with >= 2 valid_times the inference is correct. Plan 241 lets a model
declare `horizon_semantics=AT_MOST` with `min_future_steps=1`, so a one-step
forecast becomes reachable and the bug becomes live. An independent review of
the Plan 241 diff caught it before it shipped.

Adds `time_step_seconds` (`Integer`, `NOT NULL`) and makes it the authoritative
value on read, replacing the gap-inference entirely — mirroring 0050.

BACKFILL. `86400` (1 day) as a server default, matching 0050's reasoning: every
operational forecast row in this system today is daily
(`linear_regression_daily`, `nwp_regression`, `climatology_fallback`,
`persistence_fallback`, `nwp_rainfall_runoff`,
`seasonal_precip_runoff_regression` — all daily-stepped), so the placeholder is
correct for existing rows rather than merely safe. Any future sub-daily model
writes its own value from construction time.

Downgrade drops the column; a subsequent read of a pre-migration-shaped row
would go back through gap-inference (unchanged code path once the column and
its use are reverted alongside it).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_TIME_STEP_SECONDS = 86400  # 1 day — every forecast row today is daily


def upgrade() -> None:
    op.add_column(
        "forecasts",
        sa.Column(
            "time_step_seconds",
            sa.Integer,
            nullable=False,
            server_default=str(_DEFAULT_TIME_STEP_SECONDS),
        ),
    )
    op.create_check_constraint(
        "ck_forecasts_time_step_seconds_positive",
        "forecasts",
        "time_step_seconds > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_forecasts_time_step_seconds_positive", "forecasts", type_="check"
    )
    op.drop_column("forecasts", "time_step_seconds")
