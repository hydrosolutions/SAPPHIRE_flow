"""Seed stations at a schema older than migration 0087 or 0096.

``PgStationStore`` selects and writes the 0087 gauge columns and the 0096
``ingest_paused`` column, which do not exist at older revisions. Inside this
context any missing one exists as a plain column, and is dropped again on exit
so the next ``alembic upgrade`` can add it. At head the context does nothing.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import sqlalchemy as sa

if TYPE_CHECKING:
    from collections.abc import Iterator

# Column -> definition added by 0087 (first two) and 0096 (last).
_COLUMNS = {
    "water_level_gauge_type": "text",
    "water_level_sensor_masl": "double precision",
    "ingest_paused": "boolean NOT NULL DEFAULT false",
}


@contextmanager
def legacy_station_columns(conn: sa.Connection) -> Iterator[None]:
    present = set(
        conn.scalars(
            sa.text(
                "SELECT column_name FROM information_schema.columns WHERE "
                "table_schema = current_schema() AND table_name = 'stations' "
                "AND column_name = ANY(:names)"
            ),
            {"names": list(_COLUMNS)},
        )
    )
    added = [name for name in _COLUMNS if name not in present]
    for name in added:
        conn.execute(
            sa.text(f"ALTER TABLE stations ADD COLUMN {name} {_COLUMNS[name]}")
        )
    try:
        yield
    finally:
        for name in reversed(added):
            conn.execute(sa.text(f"ALTER TABLE stations DROP COLUMN {name}"))
