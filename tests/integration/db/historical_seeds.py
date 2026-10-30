"""SQL seeds for migration-era tests; never use current stores on old schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import sqlalchemy as sa

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


def seed_station(conn: sa.Connection, *, tenant_id: UUID | None = None) -> UUID:
    station_id = uuid4()
    columns = ",tenant_id" if tenant_id is not None else ""
    values = ",:tenant" if tenant_id is not None else ""
    conn.execute(
        sa.text(
            "INSERT INTO stations (id,code,name,location,station_kind,network,timezone,"
            "measured_parameters,ownership" + columns + ") VALUES "
            "(:id,:code,'Historical fixture',ST_SetSRID(ST_MakePoint(8.5,47.4),4326),"
            "'river','bafu','UTC',ARRAY['discharge'],'own'" + values + ")"
        ),
        {"id": station_id, "code": station_id.hex[:8], "tenant": tenant_id},
    )
    return station_id


def seed_rating_curve(conn: sa.Connection, station_id: UUID, now: datetime) -> UUID:
    curve_id = uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO rating_curves "
            "(id,station_id,version,valid_from,points,interpolation) "
            "VALUES (:id,:station,1,:now,:points,'linear')"
        ),
        {
            "id": curve_id,
            "station": station_id,
            "now": now,
            "points": (
                '[{"water_level":1.0,"discharge":10.0},'
                '{"water_level":2.0,"discharge":45.0}]'
            ),
        },
    )
    return curve_id


def seed_tenant_station(conn: sa.Connection) -> UUID:
    from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

    return seed_station(conn, tenant_id=DEFAULT_TENANT_ID)


def assert_revision(engine: sa.Engine, revision: str) -> None:
    with engine.connect() as conn:
        assert (
            conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == revision
        )
