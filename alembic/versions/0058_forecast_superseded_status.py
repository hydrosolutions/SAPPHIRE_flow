"""Make `forecasts.status = 'superseded'` reachable (Plan 328 T1)

`uq_forecasts_station_model_issued_param` has been PARTIAL on
`status <> 'superseded'` since migration 0017, but `ForecastStatus` never had
such a member and the CHECK on `forecasts.status` rejected the value outright.
The predicate therefore excluded something the database could not hold and the
index behaved as a FULL unique index. This revision widens the CHECK so the
status can be written; ⛔ the INDEX PREDICATE IS NOT TOUCHED — it becomes
reachable, not replaced.

The CHECK created in 0001 is ANONYMOUS, so PostgreSQL named it itself
(`forecasts_status_check` on a stock server). It is located through
`pg_constraint` rather than by a guessed name, and replaced with the
conventionally named `ck_forecasts_status`.

⛔ NO BACKFILL. Every live row is `raw` (39,825 of them, measured 2026-09-25)
and nothing sets the new value — Plan 328 T2 does that.

Widening is backwards-compatible in the direction that matters: an earlier
image still reads and writes every value it knew. ⚠️ The reverse does not
hold, which is why the downgrade rewrites superseded rows before narrowing.

Revision ID: 0058
Revises: 0057
Create Date: 2026-09-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES_WITH_SUPERSEDED = "'raw', 'reviewed', 'published', 'superseded'"
_STATUSES_BEFORE = "'raw', 'reviewed', 'published'"

_FIND_STATUS_CHECK = sa.text(
    """
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'forecasts'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%status%'
      AND pg_get_constraintdef(oid) NOT LIKE '%qc_status%'
    """
)


def _drop_status_check() -> None:
    bind = op.get_bind()
    for (name,) in bind.execute(_FIND_STATUS_CHECK).fetchall():
        op.drop_constraint(name, "forecasts", type_="check")


def upgrade() -> None:
    _drop_status_check()
    op.create_check_constraint(
        "ck_forecasts_status",
        "forecasts",
        f"status IN ({_STATUSES_WITH_SUPERSEDED})",
    )


def downgrade() -> None:
    # Rows an image carrying this revision wrote would violate the narrowed
    # constraint. A superseded forecast becomes `raw` again — the value it
    # carried before it was replaced — which is honest about the loss: the
    # older image has no way to express "replaced" at all.
    #
    # 🔴 The collision condition, MEASURED against Postgres rather than
    # reasoned about: this UPDATE raises exactly when a natural key
    # `(station_id, model_id, issued_at, parameter)` would end up holding MORE
    # THAN ONE non-superseded row. A supersession pair (original +
    # replacement) collides, and so does a chain of them — every row under the
    # key becomes `raw` at once and the partial unique index refuses them.
    # ⛔ Only a superseded row with NO sibling under its key downgrades
    # cleanly, and `store_forecast` never produces one: it writes the
    # replacement in the same transaction as the mark. ⇒ In practice, a
    # downgrade past this revision fails on any key this code has superseded.
    # Deduplicating is deliberately NOT done here — deleting a forecast would
    # orphan the evidence migration 0057 forbids removing — so the collision
    # is an operator decision and fails loudly rather than discarding a
    # record.
    op.execute("UPDATE forecasts SET status = 'raw' WHERE status = 'superseded'")
    _drop_status_check()
    op.create_check_constraint(
        "ck_forecasts_status",
        "forecasts",
        f"status IN ({_STATUSES_BEFORE})",
    )
