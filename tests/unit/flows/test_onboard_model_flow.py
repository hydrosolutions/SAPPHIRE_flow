from __future__ import annotations

from sapphire_flow.flows.onboard_model import onboard_model_flow


class TestOnboardModelFlow:
    def test_flow_is_callable(self) -> None:
        assert callable(onboard_model_flow)

    def test_flow_has_prefect_decorator(self) -> None:
        assert hasattr(onboard_model_flow, "fn")

    def test_flow_name(self) -> None:
        assert onboard_model_flow.name == "onboard-model"
