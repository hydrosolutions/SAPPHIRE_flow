from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sapphire_flow.config.daily_evaluation import (
    DailyOutputContractConfig,  # noqa: TC001 -- Pydantic schema
)
from sapphire_flow.types.evaluation_forecast import EvaluationReadSettings
from sapphire_flow.types.ids import ModelId, StationGroupId, TenantId

if TYPE_CHECKING:
    from sapphire_flow.types.daily_output import DailyOutputContract


class EvaluationForecastsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    deployment: str
    group_ids: tuple[UUID, ...]
    daily_model_id: Literal["cmal_small"] = "cmal_small"
    parameters: tuple[str, ...] = ("discharge",)
    plausibility_models: tuple[Literal["cmal_small"], ...] = ()
    data_use: Literal["standard"] = "standard"
    fallback_contracts: dict[str, tuple[DailyOutputContractConfig, ...]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _validate_domain(self) -> Self:
        self.to_domain()
        for model_id, contracts in self.fallback_contracts.items():
            if (
                not model_id.strip()
                or model_id != model_id.strip()
                or model_id == self.daily_model_id
            ):
                raise ValueError(
                    "fallback declarations require a non-daily model identity"
                )
            parameters = [contract.parameter for contract in contracts]
            if not parameters or len(parameters) != len(set(parameters)):
                raise ValueError(
                    "fallback declarations require unique nonempty parameters"
                )
            for contract in contracts:
                contract.to_domain()
        return self

    def declared_fallback_contracts(
        self,
    ) -> dict[ModelId, tuple[DailyOutputContract, ...]]:
        return {
            ModelId(model_id): tuple(c.to_domain() for c in contracts)
            for model_id, contracts in self.fallback_contracts.items()
        }

    def to_domain(self) -> EvaluationReadSettings:
        return EvaluationReadSettings(
            tenant_id=TenantId(self.tenant_id),
            deployment=self.deployment,
            group_ids=tuple(StationGroupId(g) for g in self.group_ids),
            daily_model_id=ModelId(self.daily_model_id),
            parameters=self.parameters,
            plausibility_models=frozenset(ModelId(m) for m in self.plausibility_models),
        )
