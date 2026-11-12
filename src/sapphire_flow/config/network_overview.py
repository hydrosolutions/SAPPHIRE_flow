# ruff: noqa: TC003 -- Pydantic resolves boundary fields at runtime.
from __future__ import annotations

from datetime import time
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.ids import ModelId, TenantId
from sapphire_flow.types.network_overview import OverviewFreshnessPolicy


class OverviewFreshnessPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    network: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    model_id: str = Field(min_length=1)
    parameter: Literal["water_level", "discharge"]
    data_use: Literal["standard", "provisional_rating"]
    timezone: str
    issue_timezone: str | None = None
    issue_time: time
    delivery_deadline: time

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> OverviewFreshnessPolicy:
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown product timezone") from exc
        try:
            issue_zone = (
                zone if self.issue_timezone is None else ZoneInfo(self.issue_timezone)
            )
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown issue timezone") from exc
        return OverviewFreshnessPolicy(
            tenant_id=TenantId(self.tenant_id),
            network=self.network,
            model_id=ModelId(self.model_id),
            parameter=self.parameter,
            data_use=ForecastDataUse(self.data_use),
            timezone=zone,
            issue_timezone=issue_zone,
            issue_time=self.issue_time,
            delivery_deadline=self.delivery_deadline,
        )


class NetworkOverviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    freshness_policies: tuple[OverviewFreshnessPolicyConfig, ...] = ()

    @model_validator(mode="after")
    def unique_outputs(self) -> Self:
        keys = [
            (p.tenant_id, p.network, p.model_id, p.parameter, p.data_use)
            for p in self.freshness_policies
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("ambiguous overview freshness policy")
        return self
