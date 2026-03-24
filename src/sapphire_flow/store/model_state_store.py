# pyright: reportUnknownMemberType=false
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import model_states
from sapphire_flow.store._helpers import utc_from_row

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationId


class PgModelStateStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_state(
        self,
        station_id: StationId,
        model_id: ModelId,
        issue_time: UtcDatetime,
        state_bytes: bytes,
    ) -> None:
        self._conn.execute(
            sa.insert(model_states).values(
                id=uuid.uuid4(),
                station_id=station_id,
                model_id=model_id,
                issue_time=issue_time,
                state_bytes=state_bytes,
            )
        )

    def fetch_latest_state(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[UtcDatetime, bytes] | None:
        stmt = (
            sa.select(model_states.c.issue_time, model_states.c.state_bytes)
            .where(
                sa.and_(
                    model_states.c.station_id == station_id,
                    model_states.c.model_id == model_id,
                )
            )
            .order_by(model_states.c.issue_time.desc())
            .limit(1)
        )
        row = self._conn.execute(stmt).one_or_none()
        if row is None:
            return None
        return (utc_from_row(row[0]), bytes(row[1]))
