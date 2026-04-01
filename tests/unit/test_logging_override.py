from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest

from sapphire_flow.logging import configure_api_logging


def _resolve(env_suffix: str) -> str:
    """Simulate the env var → module path resolution."""
    return env_suffix.lower().replace("__", "\x00").replace("_", ".").replace("\x00", "_")


class TestModuleResolver:
    """Unit tests for the env var → module name encoding convention."""

    def test_single_word_module(self) -> None:
        assert _resolve("ADAPTERS_METEOSWISS") == "adapters.meteoswiss"

    def test_underscore_module_name(self) -> None:
        assert _resolve("ADAPTERS_FORECAST__INTERFACE") == "adapters.forecast_interface"

    def test_underscore_module_name_camelsch(self) -> None:
        assert _resolve("ADAPTERS_CAMELSCH__ADAPTER") == "adapters.camelsch_adapter"

    def test_multi_underscore_module_name(self) -> None:
        assert _resolve("STORES_OBSERVATION__STORE") == "stores.observation_store"

    def test_multi_word_underscore_module(self) -> None:
        assert _resolve("STORES_WEATHER__FORECAST__STORE") == "stores.weather_forecast_store"

    def test_triple_underscore_greedy(self) -> None:
        # Greedy left-to-right: first __ consumed, remaining _ becomes .
        assert _resolve("FOO___BAR") == "foo_.bar"

    def test_no_double_underscore_unchanged(self) -> None:
        assert _resolve("ADAPTERS") == "adapters"


class TestModuleOverrideIntegration:
    """Integration test: env var sets the correct stdlib logger level."""

    def test_override_applies_to_correct_module(self) -> None:
        env = {"SAPPHIRE_LOG_ADAPTERS_FORECAST__INTERFACE": "DEBUG"}
        with patch.dict(os.environ, env, clear=False):
            configure_api_logging("INFO")

        logger = logging.getLogger("sapphire_flow.adapters.forecast_interface")
        assert logger.level == logging.DEBUG

    def test_single_word_override_still_works(self) -> None:
        env = {"SAPPHIRE_LOG_ADAPTERS_METEOSWISS": "DEBUG"}
        with patch.dict(os.environ, env, clear=False):
            configure_api_logging("INFO")

        logger = logging.getLogger("sapphire_flow.adapters.meteoswiss")
        assert logger.level == logging.DEBUG
