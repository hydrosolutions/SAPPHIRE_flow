# pyright: reportUnknownMemberType=false
from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.db.metadata import flow_regime_configs
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.skill import FlowRegimeConfig


class PgFlowRegimeConfigStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_config(self, config: FlowRegimeConfig) -> None:
        self._conn.execute(
            sa.insert(flow_regime_configs).values(
                id=config.id,
                station_id=config.station_id,
                parameter=config.parameter,
                p50=config.p50,
                p90=config.p90,
                computed_at=config.computed_at,
                observation_count=config.observation_count,
                version=config.version,
                created_at=config.created_at,
            )
        )

    def fetch_latest(
        self, station_id: StationId, parameter: str
    ) -> FlowRegimeConfig | None:
        stmt = (
            sa.select(flow_regime_configs)
            .where(flow_regime_configs.c.station_id == station_id)
            .where(flow_regime_configs.c.parameter == parameter)
            .order_by(flow_regime_configs.c.version.desc())
            .limit(1)
        )
        row = self._conn.execute(stmt).mappings().one_or_none()
        return _row_to_domain(row) if row is not None else None


def _row_to_domain(row: sa.engine.row.RowMapping) -> FlowRegimeConfig:
    return FlowRegimeConfig(
        id=row["id"],
        station_id=StationId(row["station_id"]),
        parameter=row["parameter"],
        p50=row["p50"],
        p90=row["p90"],
        computed_at=utc_from_row(row["computed_at"]),
        observation_count=row["observation_count"],
        version=row["version"],
        created_at=utc_from_row(row["created_at"]),
    )
