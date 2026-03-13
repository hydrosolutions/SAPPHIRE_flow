from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import polars as pl  # noqa: TC002

from sapphire_flow.types.enums import EnsembleRepresentation

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId


class ForecastEnsemble(NamedTuple):
    representation: EnsembleRepresentation
    values: pl.DataFrame
    station_id: StationId
    issued_at: UtcDatetime
    parameter: str
    units: str
    forecast_horizon_steps: int
    time_step: timedelta


def _from_members(
    cls,
    station_id: StationId,
    issued_at: UtcDatetime,
    parameter: str,
    units: str,
    time_step: timedelta,
    values: pl.DataFrame,
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
    )


ForecastEnsemble.from_members = classmethod(_from_members)  # type: ignore[attr-defined]


def _from_quantiles(
    cls,
    station_id: StationId,
    issued_at: UtcDatetime,
    parameter: str,
    units: str,
    time_step: timedelta,
    values: pl.DataFrame,
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
    )


ForecastEnsemble.from_quantiles = classmethod(_from_quantiles)  # type: ignore[attr-defined]
