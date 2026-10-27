from dataclasses import replace
from datetime import timedelta

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.daily_model_contracts import validate_daily_model_contracts
from sapphire_flow.types.daily_output import DailyOutputContract
from sapphire_flow.types.enums import EnsembleRepresentation, SpatialRepresentation
from sapphire_flow.types.model import ModelDataRequirements


@pytest.fixture
def declaration() -> tuple[ModelDataRequirements, DailyOutputContract]:
    requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(days=1)}),
        lookback_steps=1,
        forecast_horizon_steps=10,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        declared_horizon_semantics="at_most",
        declared_min_future_steps=1,
    )
    contract = DailyOutputContract(
        parameter="discharge",
        units="m³/s",
        representation=EnsembleRepresentation.MEMBERS,
        time_step=timedelta(days=1),
        first_lead=timedelta(days=1),
        horizon_steps=10,
        minimum_horizon_steps=1,
        series_keys=(0.0, 1.0),
    )
    return requirements, contract


class TestValidateDailyModelContracts:
    def test_accepts_matching_fi_bounds(
        self, declaration: tuple[ModelDataRequirements, DailyOutputContract]
    ) -> None:
        requirements, contract = declaration
        validate_daily_model_contracts(requirements, (contract,))

    @pytest.mark.parametrize("minimum", [None, 2])
    def test_configuration_cannot_override_fi_floor(
        self,
        declaration: tuple[ModelDataRequirements, DailyOutputContract],
        minimum: int | None,
    ) -> None:
        requirements, contract = declaration
        with pytest.raises(ConfigurationError, match="horizon"):
            validate_daily_model_contracts(
                requirements, (replace(contract, minimum_horizon_steps=minimum),)
            )

    def test_exact_model_cannot_be_relaxed(
        self, declaration: tuple[ModelDataRequirements, DailyOutputContract]
    ) -> None:
        requirements, contract = declaration
        exact = replace(
            requirements,
            declared_horizon_semantics="exact",
            declared_min_future_steps=None,
        )
        with pytest.raises(ConfigurationError, match="horizon"):
            validate_daily_model_contracts(exact, (contract,))
        validate_daily_model_contracts(
            exact, (replace(contract, minimum_horizon_steps=None),)
        )

    def test_missing_target_is_rejected(
        self, declaration: tuple[ModelDataRequirements, DailyOutputContract]
    ) -> None:
        requirements, _ = declaration
        with pytest.raises(ConfigurationError, match="parameters"):
            validate_daily_model_contracts(requirements, ())

    def test_wrong_maximum_is_rejected(
        self, declaration: tuple[ModelDataRequirements, DailyOutputContract]
    ) -> None:
        requirements, contract = declaration
        with pytest.raises(ConfigurationError, match="horizon"):
            validate_daily_model_contracts(
                requirements, (replace(contract, horizon_steps=9),)
            )
