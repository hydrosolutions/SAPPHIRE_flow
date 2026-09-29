import tomllib
from pathlib import Path


def test_nepal_station_metadata_is_complete_and_unpromoted() -> None:
    path = Path(__file__).resolve().parents[2] / "fixtures/dhm/stations.toml"
    data = tomllib.loads(path.read_text())

    assert data["tenant"] == {"code": "chwrr", "display_name": "CHWRR Nepal"}
    stations = data["stations"]
    assert {station["code"] for station in stations} == {
        "447",
        "450",
        "604.5",
        "647",
        "670",
        "684",
    }
    for station in stations:
        assert station["name"]
        assert -90 <= station["latitude"] <= 90
        assert -180 <= station["longitude"] <= 180
        assert station["published_area_km2"] > 0
        assert station["station_kind"] == "river"
        assert station["network"] == "dhm"
        assert station["timezone"] == "Asia/Kathmandu"
        assert station["measured_parameters"] == ["discharge"]
        assert station["station_status"] == "onboarding"
        assert station["ownership"] == "foreign"
        assert station["gauging_status"] == "gauged"
        assert "tenant_id" not in station
        assert "altitude_masl" not in station
        assert "forecast_targets" not in station
