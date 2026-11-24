from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl  # noqa: TC002

from sapphire_flow.types.enums import EnsembleRepresentation

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
    from sapphire_flow.types.ids import ModelId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastEnsemble:
    representation: EnsembleRepresentation
    values: pl.DataFrame
    station_id: StationId
    issued_at: UtcDatetime
    parameter: str
    units: str
    forecast_horizon_steps: int
    time_step: timedelta
    model_id: ModelId | None = None
    period_definition: ForecastPeriodDefinition | None = None

    def __post_init__(self) -> None:
        period = self.period_definition
        if period is None:
            return
        from sapphire_flow.types.time_grid import PeriodLabel

        if period.grid.step != self.time_step:
            raise ValueError("forecast period step differs from ensemble step")
        if self.values.schema.get("valid_time") != pl.Datetime("us", "UTC"):
            raise ValueError("period forecast timestamps must be UTC microseconds")
        first = period.grid.floor(self.issued_at)
        if period.label is PeriodLabel.END:
            first += self.time_step
        expected = [
            first + i * self.time_step for i in range(self.forecast_horizon_steps)
        ]
        if self.values["valid_time"].unique().sort().to_list() != expected:
            raise ValueError("forecast values differ from declared period grid")

    @property
    def member_count(self) -> int:
        match self.representation:
            case EnsembleRepresentation.MEMBERS:
                return self.values["member_id"].n_unique()
            case EnsembleRepresentation.QUANTILES:
                return self.values["quantile"].n_unique()
            case _:
                raise ValueError(f"Unknown representation: {self.representation}")

    @classmethod
    def from_members(
        cls,
        station_id: StationId,
        issued_at: UtcDatetime,
        parameter: str,
        units: str,
        time_step: timedelta,
        values: pl.DataFrame,
        model_id: ModelId | None = None,
        period_definition: ForecastPeriodDefinition | None = None,
    ) -> ForecastEnsemble:
        if "member_id" not in values.columns:
            raise ValueError("members DataFrame must have 'member_id' column")
        if "quantile" in values.columns:
            raise ValueError("members DataFrame must not have 'quantile' column")
        if "valid_time" not in values.columns:
            raise ValueError("DataFrame must have 'valid_time' column")
        if "value" not in values.columns:
            raise ValueError("DataFrame must have 'value' column")
        if values.is_empty():
            raise ValueError("DataFrame must not be empty")
        n_members = values["member_id"].n_unique()
        if n_members < 1:
            raise ValueError("Must have at least 1 member")
        horizon = values["valid_time"].n_unique()
        return ForecastEnsemble(
            representation=EnsembleRepresentation.MEMBERS,
            values=values,
            station_id=station_id,
            issued_at=issued_at,
            parameter=parameter,
            units=units,
            forecast_horizon_steps=horizon,
            time_step=time_step,
            model_id=model_id,
            period_definition=period_definition,
        )

    @classmethod
    def from_quantiles(
        cls,
        station_id: StationId,
        issued_at: UtcDatetime,
        parameter: str,
        units: str,
        time_step: timedelta,
        values: pl.DataFrame,
        model_id: ModelId | None = None,
        period_definition: ForecastPeriodDefinition | None = None,
    ) -> ForecastEnsemble:
        if "quantile" not in values.columns:
            raise ValueError("quantiles DataFrame must have 'quantile' column")
        if "member_id" in values.columns:
            raise ValueError("quantiles DataFrame must not have 'member_id' column")
        if "valid_time" not in values.columns:
            raise ValueError("DataFrame must have 'valid_time' column")
        if "value" not in values.columns:
            raise ValueError("DataFrame must have 'value' column")
        if values.is_empty():
            raise ValueError("DataFrame must not be empty")
        quantile_levels = values["quantile"].unique().sort()
        if len(quantile_levels) < 7:
            n = len(quantile_levels)
            raise ValueError(f"Must have at least 7 quantile levels, got {n}")
        if quantile_levels.min() > 0.05:  # type: ignore[operator]
            mn = quantile_levels.min()
            raise ValueError(f"Must have at least one quantile <= 0.05, min is {mn}")
        if quantile_levels.max() < 0.95:  # type: ignore[operator]
            mx = quantile_levels.max()
            raise ValueError(f"Must have at least one quantile >= 0.95, max is {mx}")
        horizon = values["valid_time"].n_unique()
        return ForecastEnsemble(
            representation=EnsembleRepresentation.QUANTILES,
            values=values,
            station_id=station_id,
            issued_at=issued_at,
            parameter=parameter,
            units=units,
            forecast_horizon_steps=horizon,
            time_step=time_step,
            model_id=model_id,
            period_definition=period_definition,
        )
