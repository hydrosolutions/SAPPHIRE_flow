from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.types.observation_read import ObservationReadPurpose

# This owner-rights view contains only disposition booleans and a scientific hash.
# It is deliberately not metadata-registered for generic table browsing.
unit_read_state = sa.table(
    "bafu_unit_observation_read_state",
    sa.column("observation_id", sa.Uuid),
    sa.column("held", sa.Boolean),
    sa.column("evidence_required", sa.Boolean),
    sa.column("canonical", sa.Boolean),
    sa.column("unit_fingerprint", sa.Text),
    sa.column("state_sha256", sa.Text),
    sa.column("station_id", sa.Uuid),
    sa.column("parameter", sa.Text),
    sa.column("timestamp", sa.DateTime(timezone=True)),
    sa.column("source", sa.Text),
    schema="public",
)


def unit_read_predicate(
    observations: sa.FromClause,
    purpose: ObservationReadPurpose = ObservationReadPurpose.TRUSTED,
) -> sa.ColumnElement[bool]:
    if purpose not in tuple(ObservationReadPurpose):
        raise ValueError("observation read purpose must be typed")
    if purpose is ObservationReadPurpose.SOURCE_AUDIT:
        raise ValueError("SOURCE_AUDIT requires a restricted D2 capability")
    state = unit_read_state.alias()
    eligible = sa.or_(~state.c.evidence_required, state.c.canonical)
    if purpose is ObservationReadPurpose.TRUSTED:
        eligible = sa.and_(eligible, ~state.c.held)
    else:
        eligible = sa.and_(eligible, sa.or_(~state.c.held, state.c.canonical))
    return (
        sa.select(state.c.observation_id)
        .where(state.c.observation_id == observations.c.id, eligible)
        .correlate(observations)
        .exists()
    )


def trusted_unit_predicate(observations: sa.FromClause) -> sa.ColumnElement[bool]:
    return unit_read_predicate(observations)
