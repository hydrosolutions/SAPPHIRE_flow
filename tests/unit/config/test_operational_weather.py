from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from sapphire_flow.config.operational_weather import (
    OperationalPastWeatherRoute,
    OperationalWeatherConfig,
)


class TestOperationalWeatherConfig:
    def test_automatic_issue_calendar_is_opt_in_and_round_trips(self) -> None:
        assert OperationalWeatherConfig().automatic_issue_timezone is None
        configured = OperationalWeatherConfig.model_validate(
            {"automatic_issue_timezone": "Asia/Kathmandu"}
        )
        assert configured.automatic_issue_timezone == ZoneInfo("Asia/Kathmandu")
        assert (
            OperationalWeatherConfig.model_validate_json(configured.model_dump_json())
            == configured
        )
        assert (
            configured.model_dump(mode="json")["automatic_issue_timezone"]
            == "Asia/Kathmandu"
        )

    def test_invalid_calendar_is_rejected_at_configuration_boundary(self) -> None:
        with pytest.raises(ValidationError, match="zoneinfo|timezone|ZoneInfo"):
            OperationalWeatherConfig.model_validate(
                {"automatic_issue_timezone": "not/a-timezone"}
            )

    def test_default_does_not_select_combined_route(self) -> None:
        assert (
            OperationalWeatherConfig().route
            is OperationalPastWeatherRoute.STORED_REANALYSIS
        )

    def test_evidence_budget_uses_measured_32_mib_ceiling(self) -> None:
        assert OperationalWeatherConfig().max_evidence_bytes == 32 * 1024 * 1024
        assert (
            OperationalWeatherConfig(max_evidence_bytes=1024).max_evidence_bytes == 1024
        )

    def test_explicit_combined_selector(self) -> None:
        config = OperationalWeatherConfig.model_validate({"route": "recap_combined"})
        assert config.route is OperationalPastWeatherRoute.RECAP_COMBINED

    @pytest.mark.parametrize(
        "settings",
        [
            {"provider_min_history_days": True},
            {"provider_max_history_days": 371},
            {"provider_min_history_days": 15, "provider_max_history_days": 14},
            {"rollover_buffer_days": 2},
            {"max_future_receipts": 33},
            {"max_response_bytes": 0},
            {"max_evidence_bytes": 32 * 1024 * 1024 + 1},
            {"route": "typo"},
            {"source": "ifs"},
        ],
    )
    def test_rejects_invalid_boundaries(self, settings: dict[str, object]) -> None:
        with pytest.raises(ValidationError, match="Input|input|minimum"):
            OperationalWeatherConfig.model_validate(settings)
