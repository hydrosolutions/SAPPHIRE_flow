"""seed relative_sunshine_duration parameter

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-16

Plan 115b1 §1A. ``relative_sunshine_duration`` (SrelD) becomes the fifth
canonical forcing parameter — the ``parameters`` catalog table enumerates
valid parameter names (seeded in 0001), so a new canonical parameter requires
a seed row here, not just a code-side schema change.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARAMETERS_TABLE = sa.table(
    "parameters",
    sa.column("name", sa.Text),
    sa.column("display_name", sa.Text),
    sa.column("unit", sa.Text),
    sa.column("parameter_domain", sa.Text),
    sa.column("aggregation_method", sa.Text),
)


def upgrade() -> None:
    op.bulk_insert(
        _PARAMETERS_TABLE,
        [
            {
                "name": "relative_sunshine_duration",
                "display_name": "Relative Sunshine Duration",
                "unit": "%",
                "parameter_domain": "weather",
                "aggregation_method": "mean",
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM parameters WHERE name = 'relative_sunshine_duration'"
        )
    )
