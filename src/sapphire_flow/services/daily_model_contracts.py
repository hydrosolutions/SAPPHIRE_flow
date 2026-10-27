from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from sapphire_flow.types.daily_output import DailyOutputContract
    from sapphire_flow.types.model import ModelDataRequirements


def validate_daily_model_contracts(
    requirements: ModelDataRequirements,
    contracts: tuple[DailyOutputContract, ...],
) -> None:
    parameters = [contract.parameter for contract in contracts]
    if (
        len(parameters) != len(set(parameters))
        or frozenset(parameters) != requirements.target_parameters
    ):
        raise ConfigurationError(
            "daily output parameters must match the model declaration"
        )
    minimum = (
        requirements.declared_min_future_steps
        if requirements.declared_horizon_semantics == "at_most"
        else None
    )
    for contract in contracts:
        if (
            contract.time_step != timedelta(days=1)
            or contract.time_step not in requirements.supported_time_steps
        ):
            raise ConfigurationError(
                "daily output cadence must match the model declaration"
            )
        if (
            contract.horizon_steps != requirements.forecast_horizon_steps
            or contract.minimum_horizon_steps != minimum
        ):
            raise ConfigurationError(
                "daily output horizon must match the model declaration"
            )
