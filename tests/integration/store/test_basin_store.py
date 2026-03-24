from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from shapely.geometry import MultiPolygon, Polygon

if TYPE_CHECKING:
    import sqlalchemy as sa

from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.ids import BasinId

_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)


def _make_basin(
    *,
    code: str = "TEST-01",
    network: str = "ch-bafu",
    name: str = "Test Basin",
) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code=code,
        name=name,
        geometry=_GEOM,
        area_km2=123.4,
        attributes={"source": "test"},
        band_geometries=None,
        created_at=__import__("datetime").datetime(
            2024, 1, 1, tzinfo=__import__("datetime").timezone.utc
        ),
        network=network,
    )


class TestPgBasinStore:
    def test_store_and_fetch(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin()

        returned_id = store.store_basin(basin)
        assert returned_id == basin.id

        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.id == basin.id
        assert fetched.code == basin.code
        assert fetched.name == basin.name
        assert fetched.network == basin.network
        assert fetched.area_km2 == pytest.approx(123.4)
        assert fetched.attributes == {"source": "test"}
        assert fetched.band_geometries is None
        assert fetched.geometry.geom_type == "MultiPolygon"
        assert fetched.geometry.equals_exact(_GEOM, tolerance=1e-6)

    def test_fetch_by_code(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin(code="MYCODE", network="ch-bafu")
        store.store_basin(basin)

        fetched = store.fetch_basin_by_code("MYCODE", "ch-bafu")
        assert fetched is not None
        assert fetched.id == basin.id
        assert fetched.code == "MYCODE"
        assert fetched.network == "ch-bafu"

    def test_fetch_by_code_not_found(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        result = store.fetch_basin_by_code("DOES-NOT-EXIST", "ch-bafu")
        assert result is None

    def test_fetch_all(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        b1 = _make_basin(code="A1", network="ch-bafu", name="Basin A")
        b2 = _make_basin(code="B2", network="ch-bafu", name="Basin B")
        store.store_basin(b1)
        store.store_basin(b2)

        all_basins = store.fetch_all_basins()
        ids = {b.id for b in all_basins}
        assert b1.id in ids
        assert b2.id in ids
