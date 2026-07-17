"""Plan 082 Task 2D: store-backed GatewayPolygonResolver over the §5a table.

Unit-tested against a fixture (fake store) — no real GeoPackage or DB needed.
"""

from __future__ import annotations

from uuid import UUID

from sapphire_flow.adapters.recap_gateway import (
    GatewayHruName,
    GatewayPolygonName,
    StoreBackedGatewayPolygonResolver,
)
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.station import GatewayPolygonBindingRow, StationWeatherSource

_SID_MAPPED = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_SID_UNMAPPED = StationId(UUID("00000000-0000-0000-0000-000000000002"))
_SID_BAND_ONLY = StationId(UUID("00000000-0000-0000-0000-000000000003"))
_BASIN_ID = BasinId(UUID("00000000-0000-0000-0000-0000000000ba"))


class _FakeBindingStore:
    def __init__(self, rows: list[GatewayPolygonBindingRow]) -> None:
        self._rows = rows

    def fetch_bindings_for_station(
        self, station_id: StationId
    ) -> list[GatewayPolygonBindingRow]:
        return [r for r in self._rows if r.station_id == station_id]


def _ws(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="ifs_ecmwf",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.FORECAST,
    )


class TestStoreBackedGatewayPolygonResolver:
    def test_returns_ref_for_seeded_fixture_row(self) -> None:
        rows = [
            GatewayPolygonBindingRow(
                station_id=_SID_MAPPED,
                basin_id=_BASIN_ID,
                gateway_hru_name="hru_dhm_west_v001",
                name="g_15013",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
            )
        ]
        resolver = StoreBackedGatewayPolygonResolver(_FakeBindingStore(rows))

        ref = resolver.resolve(_ws(_SID_MAPPED))

        assert ref is not None
        assert ref.station_id == _SID_MAPPED
        assert ref.hru_name == GatewayHruName("hru_dhm_west_v001")
        assert ref.polygon_name == GatewayPolygonName("g_15013")
        assert ref.spatial_type is SpatialRepresentation.BASIN_AVERAGE
        assert ref.band_id is None

    def test_returns_none_for_unmapped_station(self) -> None:
        rows = [
            GatewayPolygonBindingRow(
                station_id=_SID_MAPPED,
                basin_id=_BASIN_ID,
                gateway_hru_name="hru_dhm_west_v001",
                name="g_15013",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
            )
        ]
        resolver = StoreBackedGatewayPolygonResolver(_FakeBindingStore(rows))

        assert resolver.resolve(_ws(_SID_UNMAPPED)) is None

    def test_elevation_band_only_rows_do_not_resolve(self) -> None:
        """Recap v1 is basin-average-only — a station with ONLY band rows in
        the §5a table must resolve to None, not an ELEVATION_BAND ref."""
        rows = [
            GatewayPolygonBindingRow(
                station_id=_SID_BAND_ONLY,
                basin_id=_BASIN_ID,
                gateway_hru_name="hru_dhm_west_bands_v001",
                name="g_test01_band_1",
                spatial_type=SpatialRepresentation.ELEVATION_BAND,
                band_id=1,
            )
        ]
        resolver = StoreBackedGatewayPolygonResolver(_FakeBindingStore(rows))

        assert resolver.resolve(_ws(_SID_BAND_ONLY)) is None
