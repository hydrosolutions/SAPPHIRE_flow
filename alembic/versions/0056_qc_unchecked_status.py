"""Add QC_UNCHECKED status to observations (Plan 272 T2b)

A group for which no QC rule could be selected was stored `qc_passed` with
empty flags — indistinguishable from a group that genuinely passed every
rule. `qc_unchecked` makes "no rule ran" a stored, queryable fact.

Additive and backwards-compatible in the direction that matters: the
constraint is WIDENED, so an earlier image can still read and write every
value it knew. ⚠️ The reverse does not hold — an image without this
revision raises on `qc_unchecked` when reading one, which is why the
downgrade rewrites those rows rather than dropping the value from under
them.

Revision ID: 0056
Revises: 0055
Create Date: 2026-09-23

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES_WITH_UNCHECKED = (
    "'raw', 'qc_passed', 'qc_failed', 'qc_suspect', 'missing', 'qc_unchecked'"
)
_STATUSES_BEFORE = "'raw', 'qc_passed', 'qc_failed', 'qc_suspect', 'missing'"


def upgrade() -> None:
    op.drop_constraint("ck_observations_qc_status", "observations")
    op.create_check_constraint(
        "ck_observations_qc_status",
        "observations",
        f"qc_status IN ({_STATUSES_WITH_UNCHECKED})",
    )


def downgrade() -> None:
    # Rows written by the newer image would violate the narrowed constraint.
    # They become `raw` — the only pre-272 value that still means "QC has not
    # run on this row". Rewriting them to `qc_passed` would reinstate the very
    # false pass this revision exists to remove, and would do it silently.
    # `raw` also leaves them eligible for a later re-check.
    op.execute(
        "UPDATE observations SET qc_status = 'raw' WHERE qc_status = 'qc_unchecked'"
    )
    op.drop_constraint("ck_observations_qc_status", "observations")
    op.create_check_constraint(
        "ck_observations_qc_status",
        "observations",
        f"qc_status IN ({_STATUSES_BEFORE})",
    )
