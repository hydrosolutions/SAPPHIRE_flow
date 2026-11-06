from __future__ import annotations

from pathlib import Path

import pytest

from sapphire_flow.config.dhm import DhmConfig
from sapphire_flow.config.river_stations import (
    HydroScraperConfig,
    load_river_station_config,
)
from sapphire_flow.exceptions import ConfigurationError


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


class TestForecastProviderSnapshot:
    def test_joint_snapshot_replaces_entire_provider_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.config import river_stations as source

        base = tmp_path / "base.toml"
        base.write_text(
            'max_retention_days = 600\n[adapters.river_stations]\ntype="hydro_scraper"\nendpoint="https://example.invalid/old"\ntimeout_s=11\nmax_cache_age_hours=24\n[adapters.river_stations.monitoring]\nexpected_interval_hours=0.17\n'
        )
        worker = tmp_path / "worker.toml"
        worker.write_text("max_retention_days=700\n")
        provider = tmp_path / "provider.toml"
        provider.write_text(
            '[adapters.river_stations]\ntype="dhm"\nendpoint="https://example.invalid/levels"\nmax_requests_per_batch=1\nmax_bytes_per_batch=1000\nmax_batch_seconds=30.0\n[[adapters.river_stations.bindings]]\nnetwork="dhm"\nstation_code="synthetic"\napi_station_id=1\nlevel_reference="gauge_zero"\n'
        )
        monkeypatch.setattr(source, "FORECAST_PROVIDER_PATH", provider)
        selection = source.ForecastConfigOverlay(paths=(worker, provider))
        reads: list[Path] = []
        actual_read = Path.read_text

        def read(path: Path, *args: object, **kwargs: object) -> str:
            reads.append(path)
            if path == provider:
                worker.write_text("max_retention_days=900\n")
            return actual_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
        config, dhm = source.load_forecast_provider_config(base, (worker,), selection)
        assert reads == [base, worker, provider]
        assert config.max_retention_days == 700
        assert dhm.timeout_s == 30.0
        assert dhm.endpoint == "https://example.invalid/levels"

    @pytest.mark.parametrize(
        "failure", ["scope", "malformed", "missing", "stale", "wrong_target"]
    )
    def test_invalid_selection_is_value_free(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        from sapphire_flow.config import river_stations as source
        from sapphire_flow.exceptions import ConfigurationError

        base = tmp_path / "base.toml"
        base.write_text("max_retention_days=600\n")
        worker = tmp_path / "worker.toml"
        worker.write_text("")
        provider = tmp_path / "provider.toml"
        monkeypatch.setattr(source, "FORECAST_PROVIDER_PATH", provider)
        provider.write_text(
            'secret="DO_NOT_PRINT"\n' if failure == "scope" else "bad = DO_NOT_PRINT"
        )
        if failure == "missing":
            provider.unlink()
        if failure == "wrong_target":
            with pytest.raises(ValueError, match="reviewed provider target"):
                source.ForecastConfigOverlay(paths=(worker, tmp_path / "other.toml"))
            return
        selection = source.ForecastConfigOverlay(paths=(worker, provider))
        with pytest.raises(ConfigurationError, match="forecast provider") as raised:
            source.load_forecast_provider_config(
                base, () if failure == "stale" else (worker,), selection
            )
        assert "DO_NOT_PRINT" not in str(raised.value)
        assert str(tmp_path) not in str(raised.value)
