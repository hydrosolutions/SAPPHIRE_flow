from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from sapphire_flow.api.deps import get_evaluation_contracts
from sapphire_flow.config.daily_evaluation import DailyOutputContractConfig
from sapphire_flow.config.evaluation_forecasts import EvaluationForecastsConfig
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.ids import ModelId
from tests.conftest import make_deployment_config


def native_contract() -> DailyOutputContractConfig:
    return DailyOutputContractConfig(
        parameter="discharge",
        units="m³/s",
        representation=EnsembleRepresentation.MEMBERS,
        first_lead_hours=24,
        horizon_steps=5,
        series_keys=tuple(float(i) for i in range(50)),
    )


class TestEvaluationFallbackDeclarations:
    def test_configured_source_declaration_reaches_api_without_scheduling(self) -> None:
        settings = EvaluationForecastsConfig(
            tenant_id=UUID(int=1),
            deployment="test",
            group_ids=(UUID(int=2),),
            fallback_contracts={"linear_regression_daily": (native_contract(),)},
        )
        config = make_deployment_config(evaluation_forecasts=settings)
        declarations = get_evaluation_contracts(config)
        contract = declarations[ModelId("linear_regression_daily")][0]
        assert contract.series_keys == tuple(float(i) for i in range(50))
        assert contract.first_lead == timedelta(days=1)
        assert contract.horizon_steps == 5
        assert config.daily_evaluation is None

    def test_disabled_reads_expose_no_declarations(self) -> None:
        assert get_evaluation_contracts(None) == {}
        assert get_evaluation_contracts(make_deployment_config()) == {}

    @pytest.mark.parametrize("model_id", ["", " padded ", "cmal_small"])
    def test_identity_is_explicit_and_cannot_bypass_daily_authority(
        self, model_id: str
    ) -> None:
        with pytest.raises(ValidationError, match="non-daily model identity"):
            EvaluationForecastsConfig(
                tenant_id=UUID(int=1),
                deployment="test",
                group_ids=(UUID(int=2),),
                fallback_contracts={model_id: (native_contract(),)},
            )

    @pytest.mark.parametrize("contracts", [(), (native_contract(), native_contract())])
    def test_parameters_are_nonempty_and_unique(
        self, contracts: tuple[DailyOutputContractConfig, ...]
    ) -> None:
        with pytest.raises(ValidationError, match="unique nonempty parameters"):
            EvaluationForecastsConfig(
                tenant_id=UUID(int=1),
                deployment="test",
                group_ids=(UUID(int=2),),
                fallback_contracts={"linear_regression_daily": contracts},
            )
