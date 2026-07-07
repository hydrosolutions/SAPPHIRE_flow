from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.db.metadata import model_artifacts, station_group_members
from sapphire_flow.types.enums import ModelArtifactStatus, ModelTier
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    FALLBACK_MODEL_IDS,
    ModelId,
    StationId,
)


def model_tier_for_model_id(model_id: str | ModelId | None) -> ModelTier:
    if model_id is None:
        return ModelTier.SKILL
    return (
        ModelTier.FALLBACK
        if ModelId(str(model_id)) in FALLBACK_MODEL_IDS
        else ModelTier.SKILL
    )


def station_has_active_floor(
    *,
    station_id: StationId,
    stores: dict[str, Any],
    conn: sa.Connection | None = None,
) -> bool:
    if conn is not None:
        try:
            return _station_has_active_floor_sql(conn, station_id)
        except (AttributeError, SQLAlchemyError):
            pass

    artifact_store = stores.get("artifact_store")
    if artifact_store is None:
        return False
    return (
        artifact_store.fetch_active_artifact_for_station(
            station_id, CLIMATOLOGY_FALLBACK_MODEL_ID
        )
        is not None
    )


def _station_has_active_floor_sql(conn: sa.Connection, station_id: StationId) -> bool:
    group_subq = sa.select(station_group_members.c.group_id).where(
        station_group_members.c.station_id == station_id
    )
    stmt = (
        sa.select(model_artifacts.c.id)
        .where(
            sa.and_(
                model_artifacts.c.model_id == CLIMATOLOGY_FALLBACK_MODEL_ID,
                model_artifacts.c.status == ModelArtifactStatus.ACTIVE.value,
                sa.or_(
                    model_artifacts.c.station_id == station_id,
                    model_artifacts.c.group_id.in_(group_subq),
                ),
            )
        )
        .limit(1)
    )
    return conn.execute(stmt).first() is not None
