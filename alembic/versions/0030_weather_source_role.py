"""weather-source role — forecast vs reanalysis identity

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-14

Plan 115a. Adds a NULL-tolerant ``role`` column to ``station_weather_sources``
and backfills it from ``nwp_source``. NULL-tolerant so a previous-image
container can still write rows during the rollback window (`cicd.md` §
Rollback: backwards-compatible for one version). Tightening to NOT NULL is
Plan 115c (revision 0032 — 0031 is taken by Plan 115b1's parameter seed).

The backfill allowlist below MUST match the one in
``store/station_store.py``'s ``_row_to_weather_source`` NULL-role shim — both
implement the same identity rule and must be kept in sync until 115c deletes
the shim.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The only nwp_source values ever written (verified against staging,
# Plan 115a audit A2). An unknown name is a human decision, never a CASE
# fallthrough — the guard below raises rather than guessing.
_KNOWN_FORECAST_SOURCES = frozenset({"icon_ch2_eps"})
_KNOWN_REANALYSIS_SOURCES = frozenset({"camels-ch"})
_KNOWN_SOURCES = _KNOWN_FORECAST_SOURCES | _KNOWN_REANALYSIS_SOURCES


def upgrade() -> None:
    op.add_column(
        "station_weather_sources",
        sa.Column("role", sa.Text(), nullable=True),
    )

    # ── Pre-flight allowlist guard — raise on any nwp_source outside the
    # known set rather than silently defaulting it to REANALYSIS.
    unknown = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT DISTINCT nwp_source FROM station_weather_sources"
                " WHERE nwp_source NOT IN :known"
            ).bindparams(sa.bindparam("known", expanding=True)),
            {"known": sorted(_KNOWN_SOURCES)},
        )
        .scalars()
        .all()
    )
    if unknown:
        raise RuntimeError(
            "migration 0030: unknown nwp_source value(s) found in "
            f"station_weather_sources: {sorted(unknown)!r}. This is a human "
            "decision (forecast or reanalysis?) — extend _KNOWN_FORECAST_SOURCES "
            "or _KNOWN_REANALYSIS_SOURCES in this migration and re-run."
        )

    op.execute(
        sa.text(
            "UPDATE station_weather_sources"
            " SET role = CASE WHEN nwp_source = 'icon_ch2_eps'"
            "   THEN 'forecast' ELSE 'reanalysis' END"
        )
    )

    op.create_check_constraint(
        "ck_station_weather_sources_role",
        "station_weather_sources",
        "role IS NULL OR role IN ('forecast', 'reanalysis')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_station_weather_sources_role",
        "station_weather_sources",
        type_="check",
    )
    op.drop_column("station_weather_sources", "role")
