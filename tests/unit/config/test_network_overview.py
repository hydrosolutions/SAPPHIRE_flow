import pytest
from pydantic import ValidationError

from sapphire_flow.config.network_overview import (
    NetworkOverviewConfig,
    OverviewFreshnessPolicyConfig,
)


def policy() -> OverviewFreshnessPolicyConfig:
    return OverviewFreshnessPolicyConfig.model_validate(
        {
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "network": "dhm",
            "model_id": "cmal_small",
            "parameter": "discharge",
            "data_use": "provisional_rating",
            "timezone": "Asia/Kathmandu",
            "issue_time": "05:45",
            "delivery_deadline": "07:00",
        }
    )


class TestNetworkOverviewConfig:
    def test_nepal_keeps_actual_issue_separate_from_deadline(self) -> None:
        result = policy().to_domain()
        assert (result.issue_time.hour, result.issue_time.minute) == (5, 45)
        assert result.delivery_deadline.hour == 7

    def test_duplicate_output_policy_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ambiguous"):
            NetworkOverviewConfig(freshness_policies=(policy(), policy()))

    def test_issue_timezone_defaults_to_product_timezone(self) -> None:
        result = policy().to_domain()
        assert result.issue_timezone == result.timezone

    def test_swiss_issue_timezone_is_separate_from_deadline_timezone(self) -> None:
        data = policy().model_dump()
        data.update(
            network="bafu",
            data_use="standard",
            timezone="Europe/Zurich",
            issue_timezone="UTC",
            issue_time="00:00",
        )
        result = OverviewFreshnessPolicyConfig.model_validate(data).to_domain()
        assert (str(result.timezone), str(result.issue_timezone)) == (
            "Europe/Zurich",
            "UTC",
        )

    def test_unknown_issue_timezone_is_rejected(self) -> None:
        data = policy().model_dump()
        data["issue_timezone"] = "Invalid/Zone"
        with pytest.raises(ValidationError, match="unknown issue timezone"):
            OverviewFreshnessPolicyConfig.model_validate(data)

    def test_unknown_timezone_is_rejected(self) -> None:
        data = policy().model_dump()
        data["timezone"] = "Invalid/Zone"
        with pytest.raises(ValidationError, match="unknown product timezone"):
            OverviewFreshnessPolicyConfig.model_validate(data)
