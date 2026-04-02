from sapphire_flow.types.enums import GaugingStatus
from tests.conftest import make_station_config


class TestStationConfigGaugingStatus:
    def test_defaults_to_gauged(self) -> None:
        station = make_station_config()
        assert station.gauging_status is GaugingStatus.GAUGED

    def test_accepts_ungauged(self) -> None:
        station = make_station_config(gauging_status=GaugingStatus.UNGAUGED)
        assert station.gauging_status is GaugingStatus.UNGAUGED

    def test_accepts_calculated(self) -> None:
        station = make_station_config(gauging_status=GaugingStatus.CALCULATED)
        assert station.gauging_status is GaugingStatus.CALCULATED
