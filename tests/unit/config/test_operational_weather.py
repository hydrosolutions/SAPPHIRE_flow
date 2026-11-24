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

    def test_retention_and_original_lead_are_separate_bounded_settings(self) -> None:
        config = OperationalWeatherConfig()
        assert config.rollover_buffer_days == 15
        assert config.max_retained_past_lead_hours == 72
        assert (
            OperationalWeatherConfig(rollover_buffer_days=2).rollover_buffer_days == 2
        )

    def test_evidence_budget_keeps_default_and_accepts_explicit_larger_capture(
        self,
    ) -> None:
        assert OperationalWeatherConfig().max_evidence_bytes == 32 * 1024 * 1024
        assert (
            OperationalWeatherConfig(max_evidence_bytes=134217728).max_evidence_bytes
            == 134217728
        )
        assert (
            OperationalWeatherConfig(max_evidence_bytes=402653184).max_evidence_bytes
            == 402653184
        )
        assert (
            OperationalWeatherConfig(max_evidence_bytes=301989888).max_evidence_bytes
            == 301989888
        )
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
            {"rollover_buffer_days": 16},
            {"max_retained_past_lead_hours": 0},
            {"max_retained_past_lead_hours": 361},
            {"max_retained_past_lead_hours": True},
            {"max_future_receipts": 33},
            {"max_response_bytes": 0},
            {"max_evidence_bytes": 402653185},
            {"max_evidence_bytes": True},
            {"max_evidence_bytes": 0},
            {"route": "typo"},
            {"source": "ifs"},
        ],
    )
    def test_rejects_invalid_boundaries(self, settings: dict[str, object]) -> None:
        with pytest.raises(ValidationError, match="Input|input|minimum"):
            OperationalWeatherConfig.model_validate(settings)


@pytest.mark.parametrize("value", [0, 16, True, 11.5])
def test_negotiated_horizon_minimum_is_bounded_integer(value: object) -> None:
    with pytest.raises(ValidationError):
        OperationalWeatherConfig.model_validate(
            {"minimum_negotiated_horizon_days": value}
        )


def test_cmal_overlay_selects_long_cycles_and_eleven_day_minimum() -> None:
    import tomllib
    from pathlib import Path

    raw = tomllib.loads(Path("config/overlays/nepal-cmal-daily.toml").read_text())
    assert raw["adapters"]["recap_gateway"]["cycle_cadence_hours"] == 12
    assert (
        OperationalWeatherConfig.model_validate(
            raw["operational_weather"]
        ).minimum_negotiated_horizon_days
        == 11
    )
    assert OperationalWeatherConfig().minimum_negotiated_horizon_days == 1


def test_negotiation_receives_typed_configured_minimum() -> None:
    from sapphire_flow.types.forcing_track import FutureSteps

    assert OperationalWeatherConfig(
        minimum_negotiated_horizon_days=11
    ).negotiated_horizon_minimum == FutureSteps(value=11)
