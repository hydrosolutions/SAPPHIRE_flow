from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.domain import GeoCoord, StationThreshold
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    SpatialRepresentation,
    StationKind,
    StationOwnership,
    ThresholdSource,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.station import ModelAssignment, StationWeatherSource
from tests.conftest import make_station_config

_NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _seed_model(conn: sa.Connection, model_id: str = "linreg_v1") -> None:
    from sapphire_flow.db.metadata import models

    conn.execute(
        sa.insert(models).values(
            id=model_id,
            display_name="Linear Regression v1",
            artifact_scope="station",
            description="Test model",
        )
    )


class TestStoreAndFetchStation:
    def test_round_trip(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="STA-001",
            name="River Alpha",
            lon=8.55,
            lat=47.37,
            network="bafu",
        )

        returned_id = store.store_station(station)
        assert returned_id == station.id

        fetched = store.fetch_station(station.id)
        assert fetched is not None
        assert fetched.id == station.id
        assert fetched.code == "STA-001"
        assert fetched.name == "River Alpha"
        assert fetched.network == "bafu"
        assert fetched.location.lon == pytest.approx(8.55, abs=1e-6)
        assert fetched.location.lat == pytest.approx(47.37, abs=1e-6)
        assert fetched.location.altitude_masl is None
        assert fetched.measured_parameters == frozenset({"discharge"})
        assert fetched.ownership == StationOwnership.OWN

    def test_altitude_round_trip(self, db_connection: sa.Connection) -> None:
        from sapphire_flow.types.enums import StationStatus
        from sapphire_flow.types.station import StationConfig

        store = PgStationStore(db_connection)
        sid = StationId(uuid.uuid4())
        station = StationConfig(
            id=sid,
            code="ALT-001",
            name="High Station",
            location=GeoCoord(lon=7.5, lat=46.5, altitude_masl=1234.5),
            station_kind=StationKind.WEATHER,
            basin_id=None,
            timezone="Europe/Zurich",
            regulation_type=None,
            forecast_target=None,
            measured_parameters=frozenset({"temperature"}),
            station_status=StationStatus.OPERATIONAL,
            created_at=_NOW,
            updated_at=_NOW,
            network="meteoswiss",
            ownership=StationOwnership.OWN,
            wigos_id=None,
        )
        store.store_station(station)
        fetched = store.fetch_station(sid)
        assert fetched is not None
        assert fetched.location.altitude_masl == pytest.approx(1234.5)

    def test_fetch_missing_returns_none(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        result = store.fetch_station(StationId(uuid.uuid4()))
        assert result is None


class TestFetchStationByCode:
    def test_fetch_by_code(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="CH-0042", network="bafu"
        )
        store.store_station(station)

        fetched = store.fetch_station_by_code("CH-0042", "bafu")
        assert fetched is not None
        assert fetched.id == station.id
        assert fetched.code == "CH-0042"

    def test_wrong_network_returns_none(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="CH-0043", network="bafu"
        )
        store.store_station(station)

        result = store.fetch_station_by_code("CH-0043", "other")
        assert result is None


class TestFetchAllWithKindFilter:
    def test_filter_by_kind(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        river = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="R-001",
            station_kind=StationKind.RIVER,
            network="bafu",
        )
        weather = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="W-001",
            station_kind=StationKind.WEATHER,
            network="meteoswiss",
        )
        store.store_station(river)
        store.store_station(weather)

        rivers = store.fetch_all_stations(kind=StationKind.RIVER)
        river_ids = {s.id for s in rivers}
        assert river.id in river_ids
        assert weather.id not in river_ids

        all_stations = store.fetch_all_stations()
        all_ids = {s.id for s in all_stations}
        assert river.id in all_ids
        assert weather.id in all_ids

    def test_empty_returns_empty_list(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        result = store.fetch_all_stations(kind=StationKind.RIVER)
        assert result == []


class TestFetchByOwnership:
    def test_own_vs_foreign(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        own = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="OWN-001",
            ownership=StationOwnership.OWN,
            network="bafu",
        )
        foreign = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="FOR-001",
            ownership=StationOwnership.FOREIGN,
            network="external",
        )
        store.store_station(own)
        store.store_station(foreign)

        own_results = store.fetch_stations_by_ownership(StationOwnership.OWN)
        own_ids = {s.id for s in own_results}
        assert own.id in own_ids
        assert foreign.id not in own_ids

        foreign_results = store.fetch_stations_by_ownership(StationOwnership.FOREIGN)
        foreign_ids = {s.id for s in foreign_results}
        assert foreign.id in foreign_ids
        assert own.id not in foreign_ids

    def test_ownership_with_kind_filter(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        own_river = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="OR-001",
            ownership=StationOwnership.OWN,
            station_kind=StationKind.RIVER,
            network="bafu",
        )
        own_weather = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="OW-001",
            ownership=StationOwnership.OWN,
            station_kind=StationKind.WEATHER,
            network="meteoswiss",
        )
        store.store_station(own_river)
        store.store_station(own_weather)

        results = store.fetch_stations_by_ownership(
            StationOwnership.OWN, kind=StationKind.RIVER
        )
        ids = {s.id for s in results}
        assert own_river.id in ids
        assert own_weather.id not in ids


class TestStoreAndFetchThresholds:
    def test_round_trip(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="THR-001", network="bafu"
        )
        store.store_station(station)

        t = StationThreshold(
            station_id=station.id,
            danger_level="moderate",
            parameter="discharge",
            value=150.0,
            source=ThresholdSource.AUTHORITY,
            created_at=_NOW,
            updated_at=_NOW,
        )
        store.store_thresholds([t])

        fetched = store.fetch_thresholds(station.id)
        assert len(fetched) == 1
        assert fetched[0].station_id == station.id
        assert fetched[0].danger_level == "moderate"
        assert fetched[0].parameter == "discharge"
        assert fetched[0].value == pytest.approx(150.0)
        assert fetched[0].source == ThresholdSource.AUTHORITY

    def test_fetch_empty_returns_empty(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="NOTR-001", network="bafu"
        )
        store.store_station(station)
        assert store.fetch_thresholds(station.id) == []


class TestThresholdUpsert:
    def test_upsert_updates_value(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="UPS-001", network="bafu"
        )
        store.store_station(station)

        t_v1 = StationThreshold(
            station_id=station.id,
            danger_level="high",
            parameter="discharge",
            value=200.0,
            source=ThresholdSource.AUTHORITY,
            created_at=_NOW,
            updated_at=_NOW,
        )
        store.store_thresholds([t_v1])

        updated = _NOW.replace(hour=12)
        t_v2 = StationThreshold(
            station_id=station.id,
            danger_level="high",
            parameter="discharge",
            value=250.0,
            source=ThresholdSource.INFERRED,
            created_at=_NOW,
            updated_at=updated,
        )
        store.store_thresholds([t_v2])

        fetched = store.fetch_thresholds(station.id)
        assert len(fetched) == 1
        assert fetched[0].value == pytest.approx(250.0)
        assert fetched[0].source == ThresholdSource.INFERRED

    def test_multiple_thresholds_same_station(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="MULTI-001", network="bafu"
        )
        store.store_station(station)

        thresholds = [
            StationThreshold(
                station_id=station.id,
                danger_level="moderate",
                parameter="discharge",
                value=100.0,
                source=ThresholdSource.AUTHORITY,
                created_at=_NOW,
                updated_at=_NOW,
            ),
            StationThreshold(
                station_id=station.id,
                danger_level="high",
                parameter="discharge",
                value=200.0,
                source=ThresholdSource.AUTHORITY,
                created_at=_NOW,
                updated_at=_NOW,
            ),
        ]
        store.store_thresholds(thresholds)

        fetched = store.fetch_thresholds(station.id)
        assert len(fetched) == 2


class TestStoreAndFetchModelAssignment:
    def test_round_trip(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="MA-001", network="bafu"
        )
        store.store_station(station)
        _seed_model(db_connection, "linreg_v1")

        assignment = ModelAssignment(
            station_id=station.id,
            model_id=ModelId("linreg_v1"),
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_NOW,
        )
        store.store_model_assignment(assignment)

        fetched = store.fetch_model_assignments(station.id)
        assert len(fetched) == 1
        assert fetched[0].station_id == station.id
        assert fetched[0].model_id == ModelId("linreg_v1")
        assert fetched[0].time_step == timedelta(hours=1)
        assert fetched[0].status == ModelAssignmentStatus.ACTIVE
        assert fetched[0].priority == 1

    def test_upsert_updates_status(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="MA-002", network="bafu"
        )
        store.store_station(station)
        _seed_model(db_connection, "linreg_v2")

        a1 = ModelAssignment(
            station_id=station.id,
            model_id=ModelId("linreg_v2"),
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=0,
            created_at=_NOW,
        )
        store.store_model_assignment(a1)

        a2 = ModelAssignment(
            station_id=station.id,
            model_id=ModelId("linreg_v2"),
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.INACTIVE,
            priority=5,
            created_at=_NOW,
        )
        store.store_model_assignment(a2)

        fetched = store.fetch_model_assignments(station.id)
        assert len(fetched) == 1
        assert fetched[0].status == ModelAssignmentStatus.INACTIVE
        assert fetched[0].priority == 5

    def test_fetch_empty_returns_empty(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="NOMA-001", network="bafu"
        )
        store.store_station(station)
        assert store.fetch_model_assignments(station.id) == []


class TestStoreAndFetchWeatherSource:
    def test_round_trip(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="WS-001", network="bafu"
        )
        store.store_station(station)

        source = StationWeatherSource(
            station_id=station.id,
            nwp_source="icon_ch2_eps",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
        )
        store.store_weather_source(source)

        fetched = store.fetch_weather_sources(station.id)
        assert len(fetched) == 1
        assert fetched[0].station_id == station.id
        assert fetched[0].nwp_source == "icon_ch2_eps"
        assert fetched[0].extraction_type == SpatialRepresentation.BASIN_AVERAGE
        assert fetched[0].status == WeatherSourceStatus.ACTIVE

    def test_upsert_updates_status(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="WS-002", network="bafu"
        )
        store.store_station(station)

        s1 = StationWeatherSource(
            station_id=station.id,
            nwp_source="icon_ch2_eps",
            extraction_type=SpatialRepresentation.POINT,
            status=WeatherSourceStatus.ACTIVE,
        )
        store.store_weather_source(s1)

        s2 = StationWeatherSource(
            station_id=station.id,
            nwp_source="icon_ch2_eps",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.INACTIVE,
        )
        store.store_weather_source(s2)

        fetched = store.fetch_weather_sources(station.id)
        assert len(fetched) == 1
        assert fetched[0].status == WeatherSourceStatus.INACTIVE
        assert fetched[0].extraction_type == SpatialRepresentation.BASIN_AVERAGE

    def test_fetch_empty_returns_empty(self, db_connection: sa.Connection) -> None:
        store = PgStationStore(db_connection)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()), code="NOWS-001", network="bafu"
        )
        store.store_station(station)
        assert store.fetch_weather_sources(station.id) == []
