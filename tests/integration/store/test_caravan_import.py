"""Plan 155 T1/T1b — additive Caravan-attributes merge, DB-backed.

Locks the storage-layer contract the pure `services/caravan_statics.py`
tests cannot reach: `merge_namespaced_attributes` must NOT create a new
`basin_versions` row (no supersede, no `material_change`), must NOT touch a
non-namespaced (incumbent) attribute, and `import_caravan_attributes` must
join correctly on station identity end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pandas as pd
import pytest
import sqlalchemy as sa
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.db.metadata import basin_versions
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.caravan_import import import_caravan_attributes
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.ids import BasinId
from tests.conftest import make_station_config

_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)
_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def _make_basin(*, code: str = "2009", attributes: dict | None = None) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code=code,
        name="Test Basin",
        geometry=_GEOM,
        area_km2=123.4,
        attributes=attributes or {"area": 100.0},  # CAMELS-CH's own bare "area"
        band_geometries=None,
        created_at=_CREATED_AT,
        network="bafu",
    )


class TestMergeNamespacedAttributes:
    def test_additive_merge_leaves_incumbent_attributes_untouched(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin(attributes={"area": 100.0, "elevation": 500.0})
        store.store_basin(basin)

        store.merge_namespaced_attributes(
            basin.id, attributes={"caravan:area": 250.0, "caravan:slp_dg_sav": 12.5}
        )

        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["area"] == 100.0  # untouched
        assert fetched.attributes["elevation"] == 500.0  # untouched
        assert fetched.attributes["caravan:area"] == 250.0
        assert fetched.attributes["caravan:slp_dg_sav"] == 12.5

    def test_does_not_create_a_new_basin_version(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin()
        store.store_basin(basin)

        version_count_before = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_versions)
            .where(basin_versions.c.basin_id == basin.id)
        ).scalar_one()

        store.merge_namespaced_attributes(basin.id, attributes={"caravan:area": 250.0})

        version_count_after = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_versions)
            .where(basin_versions.c.basin_id == basin.id)
        ).scalar_one()

        assert version_count_after == version_count_before

    def test_rejects_a_key_without_the_prefix(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin()
        store.store_basin(basin)

        with pytest.raises(ValueError, match="caravan:"):
            store.merge_namespaced_attributes(basin.id, attributes={"area": 999.0})

        # Structurally guarded (Plan 155 T1b): the rejected call must not
        # have touched the incumbent attribute either.
        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["area"] == 100.0

    def test_raises_for_a_basin_that_does_not_exist(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgBasinStore(db_connection)
        with pytest.raises(ValueError, match="not found"):
            store.merge_namespaced_attributes(
                BasinId(uuid.uuid4()), attributes={"caravan:area": 1.0}
            )

    def test_second_merge_is_idempotent(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin()
        store.store_basin(basin)

        store.merge_namespaced_attributes(basin.id, attributes={"caravan:area": 250.0})
        store.merge_namespaced_attributes(basin.id, attributes={"caravan:area": 250.0})

        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["caravan:area"] == 250.0


def _write_parquet(tmp_path, rows: list[dict]):
    path = tmp_path / "data.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return path


class TestImportCaravanAttributes:
    def test_joins_on_station_identity_and_merges_additively(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)

        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station = make_station_config(code="2009", network="bafu", basin_id=basin.id)
        station_store.store_station(station)

        path = _write_parquet(
            tmp_path,
            [
                {
                    "gauge_id": "caravan_camels_ch_2009",
                    "area": 250.0,
                    "slp_dg_sav": 12.5,
                },
                # No matching station for this one -- an unmatched code.
                {
                    "gauge_id": "caravan_camels_ch_9999",
                    "area": 1.0,
                    "slp_dg_sav": 2.0,
                },
            ],
        )

        result = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )

        assert result.matched_codes == frozenset({"2009"})
        assert result.unmatched_codes == frozenset({"9999"})
        assert result.stations_without_basin == frozenset()

        fetched = basin_store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["area"] == 100.0  # incumbent untouched
        assert fetched.attributes["caravan:area"] == 250.0
        assert fetched.attributes["caravan:slp_dg_sav"] == 12.5

    def test_station_without_basin_is_reported_not_silently_dropped(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)

        station = make_station_config(code="2011", network="bafu", basin_id=None)
        station_store.store_station(station)

        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2011", "area": 1.0}]
        )

        result = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )

        assert result.stations_without_basin == frozenset({"2011"})
        assert result.matched_codes == frozenset()
