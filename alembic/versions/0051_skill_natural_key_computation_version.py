"""restore computation_version to the skill natural keys (Plan 228 review fixer round)

Revision ID: 0051
Revises: 0050
Create Date: 2026-09-01

BLOCKER found reviewing Plan 228: `skill_scores`/`skill_diagrams` are meant
to hold a VERSIONED history — `fetch_latest_scores`/`fetch_latest_diagrams`
(`store/skill_store.py`) pick the row(s) at `max(computation_version)` as
"latest", explicitly designed to let a corrected recompute supersede an
older one without deleting it. That design requires `computation_version`
to be part of the natural key, so two rows differing ONLY by version are
DISTINCT and can both exist.

Migration 0016 ("Add parameter column...") dropped and recreated both
`uq_skill_scores_natural_key` and `uq_skill_diagrams_natural_key` to add the
new `parameter` column — and silently DROPPED `computation_version` from
both in the process (present in the original 0001/0008 definition, absent
from 0016 onward). `db/metadata.py`'s Python `sa.Index` objects were never
updated to match — they still declare `computation_version` as part of the
key, which has been FALSE of the live schema since 0016. The result,
proved by an integration test storing a corrupted v1 score, marking it
stale, and recomputing at v2 for the identical stratum: the CORRECTED row's
`INSERT ... ON CONFLICT DO NOTHING` collides with the STALE v1 row (every
column the live index actually checks is identical) and is silently
dropped — the exact blocker Plan 228's D3 recompute depends on not
happening, except it is unconditional and has nothing to do with Plan 228
specifically: ANY versioned recompute of an existing stratum has been
silently a no-op since 0016.

Adds `computation_version` back to both unique indexes, matching what
`db/metadata.py` already (and, until now, wrongly) claimed. A duplicate can
exist in the live table already (two rows same-key-old-shape, differing
only by version, both currently permitted to coexist is not the failure
mode — the failure mode is the OPPOSITE, an attempted second version being
REJECTED — so no pre-existing row can violate the new, less restrictive
constraint; adding a column to a unique index only shrinks the set of rows
considered duplicates).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Column order matches `db/metadata.py`'s `sa.Index` objects exactly —
    # those were never wrong about which columns belong, only the live
    # schema (since 0016) was missing `computation_version`.
    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "parameter",
            "skill_source",
            sa.text("COALESCE(forcing_type, '')"),
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "metric",
        ],
        unique=True,
    )

    op.drop_index("uq_skill_diagrams_natural_key", table_name="skill_diagrams")
    op.create_index(
        "uq_skill_diagrams_natural_key",
        "skill_diagrams",
        [
            "station_id",
            "model_artifact_id",
            "parameter",
            "skill_source",
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "diagram_type",
            sa.text("COALESCE(threshold_level, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_skill_diagrams_natural_key", table_name="skill_diagrams")
    op.create_index(
        "uq_skill_diagrams_natural_key",
        "skill_diagrams",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "parameter",
            "lead_time_hours",
            "diagram_type",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(threshold_level, '')"),
        ],
        unique=True,
    )

    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "parameter",
            "lead_time_hours",
            "metric",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(forcing_type, '')"),
        ],
        unique=True,
    )
