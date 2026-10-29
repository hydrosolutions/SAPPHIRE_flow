from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sapphire_flow.config.dhm import DhmConfig
from sapphire_flow.config.river_stations import (
    HydroScraperConfig,
    load_river_station_config,
)
from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path


class TestLoadRiverStationConfig:
    def test_no_config_preserves_bafu_default(self) -> None:
        assert load_river_station_config(None, []) == HydroScraperConfig()

    def test_overlay_selects_dhm_and_retains_base_settings(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "base.toml"
        base.write_text("""[adapters.river_stations]
type = "hydro_scraper"
endpoint = "https://lindas.admin.ch/query"
timeout_s = 12
""")
        overlay = tmp_path / "nepal.toml"
        overlay.write_text("""[adapters.river_stations]
type = "dhm"
endpoint = "https://dhm.example/api/v1/"
max_requests_per_batch = 100
max_bytes_per_batch = 10000000
max_batch_seconds = 10000.0
[[adapters.river_stations.bindings]]
network = "dhm"
station_code = "DHM-1"
api_station_id = 168
level_reference = "unknown"
""")
        result = load_river_station_config(base, [overlay])
        assert isinstance(result, DhmConfig)
        assert result.timeout_s == 12
        assert result.endpoint == "https://dhm.example/api/v1/"
        assert result.bindings[0].api_station_id == 168

    @pytest.mark.parametrize(
        "body",
        [
            'type = "dhm"',
            'type = "typo"',
            "type = true",
            'type = "dhm"\nendpoint = "https://dhm.example"\nbindings = "bad"',
        ],
    )
    def test_invalid_selection_never_falls_back_to_bafu(
        self, tmp_path: Path, body: str
    ) -> None:
        path = tmp_path / "bad.toml"
        path.write_text("[adapters.river_stations]\n" + body)
        with pytest.raises(ConfigurationError, match="Invalid"):
            load_river_station_config(path, [])

    def test_missing_type_preserves_configured_bafu_endpoint(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "config.toml"
        path.write_text(
            '[adapters.river_stations]\nendpoint = "https://bafu.example/query"'
        )
        assert load_river_station_config(path, []) == HydroScraperConfig(
            endpoint="https://bafu.example/query"
        )

    def test_only_selected_dhm_requires_batch_bounds(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        body = (
            '[adapters.river_stations]\ntype = "hydro_scraper"\n'
            'endpoint = "https://dhm.example/api/v1/"\nbindings = []\n'
        )
        path.write_text(body)
        assert isinstance(load_river_station_config(path, []), HydroScraperConfig)
        path.write_text(body.replace('"hydro_scraper"', '"dhm"'))
        with pytest.raises(ConfigurationError, match="Invalid DHM"):
            load_river_station_config(path, [])
