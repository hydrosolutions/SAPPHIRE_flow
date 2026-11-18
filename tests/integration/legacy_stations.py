"""Seed stations at a schema older than migration 0087.

``PgStationStore`` selects and writes the two 0087 gauge columns, which do not
exist at older revisions. Inside this context they exist as plain columns, and
they are dropped again on exit so the next ``alembic upgrade`` can add them.
At head the context does nothing.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import sqlalchemy as sa

if TYPE_CHECKING:
    from collections.abc import Iterator

_COLUMNS = ("water_level_gauge_type", "water_level_sensor_masl")


@contextmanager
def legacy_station_columns(conn: sa.Connection) -> Iterator[None]:
    present = conn.scalar(
        sa.text(
            "SELECT count(*) FROM information_schema.columns WHERE "
            "table_schema = current_schema() AND table_name = 'stations' "
            "AND column_name = ANY(:names)"
        ),
        {"names": list(_COLUMNS)},
    )
    if present:
        yield
        return
    conn.execute(sa.text("ALTER TABLE stations ADD COLUMN water_level_gauge_type text"))
    conn.execute(
        sa.text(
            "ALTER TABLE stations ADD COLUMN water_level_sensor_masl double precision"
        )
    )
    try:
        yield
    finally:
        conn.execute(
            sa.text("ALTER TABLE stations DROP COLUMN water_level_sensor_masl")
        )
        conn.execute(sa.text("ALTER TABLE stations DROP COLUMN water_level_gauge_type"))
