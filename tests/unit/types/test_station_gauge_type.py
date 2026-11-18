from __future__ import annotations

import pytest

from sapphire_flow.types.enums import WaterLevelGaugeType
from tests.conftest import make_station_config


class TestStationConfigGaugeRules:
    def test_defaults_keep_legacy_stations_valid(self) -> None:
        station = make_station_config(water_level_datum_masl=1480.0)
        assert station.water_level_gauge_type is None
        assert station.water_level_sensor_masl is None

    def test_bridge_sensor_with_both_elevations_is_valid(self) -> None:
        station = make_station_config(
            water_level_datum_masl=1480.0,
            water_level_gauge_type=WaterLevelGaugeType.BRIDGE_SENSOR,
            water_level_sensor_masl=1490.0,
        )
        assert station.water_level_sensor_masl == 1490.0

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            (
                {"water_level_gauge_type": WaterLevelGaugeType.STAFF_OR_PRESSURE},
                "datum",
            ),
            (
                {
                    "water_level_datum_masl": 1480.0,
                    "water_level_gauge_type": WaterLevelGaugeType.BRIDGE_SENSOR,
                },
                "sensor elevation",
            ),
            (
                {
                    "water_level_datum_masl": 1480.0,
                    "water_level_gauge_type": WaterLevelGaugeType.STAFF_OR_PRESSURE,
                    "water_level_sensor_masl": 1490.0,
                },
                "bridge sensors only",
            ),
            (
                {
                    "water_level_datum_masl": 1480.0,
                    "water_level_gauge_type": WaterLevelGaugeType.BRIDGE_SENSOR,
                    "water_level_sensor_masl": 1480.0,
                },
                "above the gauge zero",
            ),
        ],
    )
    def test_contradictions_refused(
        self, kwargs: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            make_station_config(**kwargs)  # pyright: ignore[reportArgumentType]
