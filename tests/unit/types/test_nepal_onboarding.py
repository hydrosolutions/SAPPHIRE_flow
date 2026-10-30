from uuid import uuid4

import pytest

from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.nepal_onboarding import (
    OperationalWeatherScope,
    WeatherStationPolygon,
)
from sapphire_flow.types.weather import GatewayHruName


class TestOperationalWeatherScope:
    def test_scope_is_runtime_defined_not_fixed_to_six_or_twenty_one(self) -> None:
        scope = OperationalWeatherScope(
            group_id=StationGroupId(uuid4()),
            gateway_hru_name=GatewayHruName("registered_other"),
            station_polygons=(
                WeatherStationPolygon(
                    station_id=StationId(uuid4()), polygon_name="other"
                ),
            ),
        )
        assert len(scope.station_polygons) == 1

    @pytest.mark.parametrize(
        "fault", ["empty", "station_duplicate", "polygon_duplicate", "hru_blank"]
    )
    def test_invalid_scope_rejected(self, fault: str) -> None:
        p = WeatherStationPolygon(station_id=StationId(uuid4()), polygon_name="g_1")
        polygons = {
            "empty": (),
            "station_duplicate": (p, p),
            "polygon_duplicate": (
                p,
                WeatherStationPolygon(
                    station_id=StationId(uuid4()), polygon_name="g_1"
                ),
            ),
            "hru_blank": (p,),
        }[fault]
        with pytest.raises(ValueError, match="nonempty|duplicate"):
            OperationalWeatherScope(
                group_id=StationGroupId(uuid4()),
                gateway_hru_name=GatewayHruName(
                    "" if fault == "hru_blank" else "registered"
                ),
                station_polygons=polygons,
            )

    def test_polygon_rejects_blank(self) -> None:
        with pytest.raises(ValueError, match="polygon_name"):
            WeatherStationPolygon(station_id=StationId(uuid4()), polygon_name=" ")
