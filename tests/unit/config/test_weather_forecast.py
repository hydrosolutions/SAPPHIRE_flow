from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import NoReturn

import pytest

from sapphire_flow.config.weather_forecast import (
    parse_require_nwp,
    parse_weather_forecast_adapter_config,
)
from sapphire_flow.exceptions import ConfigurationError


class TestWeatherForecastParser:
    @pytest.mark.parametrize("enabled", [True, False])
    @pytest.mark.parametrize(
        "field,value",
        [
            ("enabled", 1),
            ("type", "unknown"),
            ("max_files", True),
            ("grid_extractor", "unknown"),
            ("disk_guard_scratch_soft_gb", False),
            ("monitoring", {"expected_delivery_offset_hours": False}),
        ],
    )
    def test_invalid_fields_are_checked_even_when_disabled(
        self, enabled: bool, field: str, value: object
    ) -> None:
        section: dict[str, object] = {
            "enabled": enabled,
            "stac_base_url": "https://example.invalid",
            "stac_collection": "synthetic",
            "scratch_path": "/tmp/not-created",
            field: value,
        }
        with pytest.raises(ConfigurationError, match="adapters.weather_forecast"):
            parse_weather_forecast_adapter_config(
                {"adapters": {"weather_forecast": section}}, require_nwp=False
            )

    @pytest.mark.parametrize(
        "value,expected", [(None, False), ("", False), (" true ", True), ("OFF", False)]
    )
    def test_explicit_require_nwp(self, value: str | None, expected: bool) -> None:
        assert parse_require_nwp(value) is expected

    def test_invalid_require_nwp_does_not_echo_value(self) -> None:
        with pytest.raises(ConfigurationError, match="SAPPHIRE_REQUIRE_NWP") as error:
            parse_require_nwp("secret-canary")
        assert "secret-canary" not in str(error.value)

    def test_pure_parser_does_not_read_environment_or_touch_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw: dict[str, object] = {"adapters": {"weather_forecast": {"enabled": False}}}
        snapshot = copy.deepcopy(raw)

        def forbidden(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Pure parser must not access ambient inputs or filesystem")

        with monkeypatch.context() as patch:
            patch.setattr(type(os.environ), "get", forbidden)
            patch.setattr(Path, "read_text", forbidden)
            patch.setattr(Path, "mkdir", forbidden)
            result = parse_weather_forecast_adapter_config(raw, require_nwp=True)
        assert result.require_nwp is True
        assert result.enabled is False
        assert raw == snapshot
