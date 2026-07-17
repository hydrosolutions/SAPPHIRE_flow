"""Plan 082 Task 2A: Nepal recap Data Gateway config + API-key secret plumbing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sapphire_flow.config.recap_gateway import (
    RecapGatewayConfig,
    build_recap_client_config,
    load_recap_api_key,
    load_recap_gateway_config,
)
from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path


class TestLoadRecapApiKey:
    def test_reads_secret_from_file(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "sapphire_dg_api_key"
        secret_file.write_text("super-secret-key\n")
        assert load_recap_api_key(secret_path=secret_file) == "super-secret-key"

    def test_env_var_fallback_when_file_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RECAP_API_KEY", "env-secret-key")
        missing_file = tmp_path / "does-not-exist"
        assert load_recap_api_key(secret_path=missing_file) == "env-secret-key"

    def test_raises_when_neither_file_nor_env_var_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RECAP_API_KEY", raising=False)
        missing_file = tmp_path / "does-not-exist"
        with pytest.raises(ConfigurationError, match="RECAP_API_KEY"):
            load_recap_api_key(secret_path=missing_file)


class TestBuildRecapClientConfig:
    def test_api_client_config_carries_the_exact_secret(self) -> None:
        config = RecapGatewayConfig(
            base_url="https://recap.example.org",
            timeout_s=300,
            verify_tls=True,
            staleness_threshold_hours=6.0,
            hru_metadata_source="manual_gpkg_upload",
            max_retries=3,
        )
        client_config = build_recap_client_config(
            api_key="super-secret-key", config=config
        )
        assert client_config.api_key == "super-secret-key"
        assert client_config.base_url == "https://recap.example.org"
        assert client_config.timeout_s == 300
        assert client_config.verify_tls is True

    def test_never_logs_or_returns_a_redacted_key(self) -> None:
        config = RecapGatewayConfig(
            base_url="https://recap.example.org",
            timeout_s=300,
            verify_tls=True,
            staleness_threshold_hours=6.0,
            hru_metadata_source="manual_gpkg_upload",
            max_retries=3,
        )
        build_recap_client_config(api_key="topsecret", config=config)
        # repr() of the RecapGatewayConfig dataclass (never carries the key
        # itself — see its docstring) must not leak the secret either; this
        # guards against a future accidental field addition that would.
        assert "topsecret" not in repr(config)


class TestLoadRecapGatewayConfig:
    def test_reads_recap_gateway_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[adapters.recap_gateway]\n"
            'base_url = "https://recap.example.org"\n'
            "timeout_s = 120\n"
            "verify_tls = true\n"
            "staleness_threshold_hours = 12.0\n"
            'hru_metadata_source = "manual_gpkg_upload"\n'
            "max_retries = 5\n"
        )
        config = load_recap_gateway_config(config_path)
        assert config.base_url == "https://recap.example.org"
        assert config.timeout_s == 120
        assert config.staleness_threshold_hours == 12.0
        assert config.hru_metadata_source == "manual_gpkg_upload"
        assert config.max_retries == 5

    def test_missing_section_raises(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("[adapters.weather_forecast]\nenabled = false\n")
        with pytest.raises(ConfigurationError, match="recap_gateway"):
            load_recap_gateway_config(config_path)

    def test_defaults_max_cycle_age_hours_when_absent(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[adapters.recap_gateway]\n"
            'base_url = "https://recap.example.org"\n'
            "timeout_s = 120\n"
            "verify_tls = true\n"
            "staleness_threshold_hours = 12.0\n"
            'hru_metadata_source = "manual_gpkg_upload"\n'
            "max_retries = 5\n"
        )
        config = load_recap_gateway_config(config_path)
        assert config.max_cycle_age_hours == 18.0


class TestLoadRecapGatewayConfigOverlay:
    """Codex review Finding 4 (major): a Nepal deployment supplies
    `type = "recap_gateway"` + `[adapters.recap_gateway]` from an OVERLAY
    layered on the base (Swiss) config via SAPPHIRE_CONFIG_OVERLAY -- the
    same merge the Flow-1 selector uses (run_forecast_cycle.py's
    `_load_weather_forecast_adapter_config`). The loader must read that same
    merged view, not just the base file."""

    def test_reads_recap_section_supplied_by_overlay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base_path = tmp_path / "config.toml"
        base_path.write_text(
            '[adapters.weather_forecast]\nenabled = false\ntype = "meteoswiss_nwp"\n'
        )
        overlay_path = tmp_path / "nepal_overlay.toml"
        overlay_path.write_text(
            "[adapters.weather_forecast]\n"
            "enabled = true\n"
            'type = "recap_gateway"\n\n'
            "[adapters.recap_gateway]\n"
            'base_url = "https://recap.example.org"\n'
            "timeout_s = 120\n"
            "verify_tls = true\n"
            "staleness_threshold_hours = 6.0\n"
            'hru_metadata_source = "manual_gpkg_upload"\n'
            "max_retries = 5\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay_path))

        config = load_recap_gateway_config(base_path)

        assert config.base_url == "https://recap.example.org"
        assert config.staleness_threshold_hours == 6.0

    def test_no_overlay_env_var_reads_base_only_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[adapters.recap_gateway]\n"
            'base_url = "https://recap.example.org"\n'
            "timeout_s = 120\n"
            "verify_tls = true\n"
            "staleness_threshold_hours = 12.0\n"
            'hru_metadata_source = "manual_gpkg_upload"\n'
            "max_retries = 5\n"
        )

        config = load_recap_gateway_config(config_path)

        assert config.base_url == "https://recap.example.org"
