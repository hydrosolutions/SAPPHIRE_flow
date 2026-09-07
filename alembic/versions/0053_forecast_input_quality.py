"""forecasts.input_quality + input_quality_flags columns (Plan 253 T1a)

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-04

`services/input_quality.py` assesses, for every operational forecast,
whether it ran on stale observations, an aged warm-up state, or an old NWP
cycle — and both forecast paths (`services/run_station_forecast.py`,
`services/run_group_forecast.py`) call it and attach the result. It was then
never persisted: `forecasts` carried no columns for it, so the read path
always reconstructed `OperationalForecast.input_quality` at its dataclass
default of `FULL`, discarding the real assessment on every read.

Adds two NULLABLE columns with NO server default, deliberately: a server
default of `'full'` (the shape Plan 023 originally suggested) would make
every pre-migration row read back as `FULL` — a confident, false answer, the
exact failure `docs/standards/cicd.md`'s additive-only migration rule and
this plan's exit gate 2 forbid. A legacy row must read as *unknown*, not as
*full quality*. `store/forecast_store.py` (T1b) reads a `NULL` in
`input_quality` as `None` on `OperationalForecast`, distinct from a stored
`InputQualityLevel.FULL`.

`input_quality` mirrors `qc_status` (`Text`, one of the `InputQualityLevel`
values written by name). `input_quality_flags` mirrors `qc_flags` (`JSONB`
array of `{category, level, detail}` objects) but stays nullable rather than
defaulting to `'[]'`, so "no assessment" (`NULL`) and "assessed with zero
flags" (`[]`) stay distinguishable on a legacy row — the same unknown-vs-FULL
distinction extended to the paired flags.

Scoped to `OperationalForecast`/`forecasts` only, per Plan 023 —
`hindcast_forecasts` is out of scope.

Downgrade drops both columns; a subsequent read of a pre-migration-shaped
row goes back through the pre-T1b default (unchanged code path once the
column and its use are reverted alongside it).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "forecasts",
        sa.Column("input_quality", sa.Text, nullable=True),
    )
    op.add_column(
        "forecasts",
        sa.Column("input_quality_flags", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forecasts", "input_quality_flags")
    op.drop_column("forecasts", "input_quality")
