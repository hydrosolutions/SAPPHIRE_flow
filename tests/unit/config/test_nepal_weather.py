from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sapphire_flow.config.nepal_weather import load_weather_scope
from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

HEADER = """[weather_history.scopes.test]
group_id = "00000000-0000-0000-0000-000000000001"
original_gateway_hru_name = "original"
runtime_gateway_hru_name = "runtime"
"""
ROW = '{station_id="00000000-0000-0000-0000-000000000002", polygon_name="g_1"}'


class TestLoadWeatherScope:
    def test_overlay_replaces_whole_roster(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "base.toml"
        overlay = tmp_path / "overlay.toml"
        base.write_text(HEADER + "station_polygons = [" + ROW + "]")
        overlay.write_text(
            "[weather_history.scopes.test]\nstation_polygons = ["
            + ROW.replace("000002", "000003").replace("g_1", "g_2")
            + "]"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))
        result = load_weather_scope(base, "test")
        assert result.original_gateway_hru_name == "original"
        assert result.scope.gateway_hru_name == "runtime"
        assert [p.polygon_name for p in result.scope.station_polygons] == ["g_2"]

    @pytest.mark.parametrize(
        "roster",
        [
            "[]",
            "[" + ROW + "," + ROW + "]",
            '[{station_id="invalid", polygon_name="g"}]',
            "[" + ROW.replace("g_1", " ") + "]",
        ],
    )
    def test_invalid_roster(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, roster: str
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        base = tmp_path / "base.toml"
        base.write_text(HEADER + "station_polygons = " + roster)
        with pytest.raises(ConfigurationError, match="invalid weather history scope"):
            load_weather_scope(base, "test")
