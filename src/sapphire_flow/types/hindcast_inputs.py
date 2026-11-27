from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.model import (
    ModelDataRequirements,
    StationInputData,
    StationModelInputs,
)
from sapphire_flow.types.time_grid import PeriodLabel

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
    from sapphire_flow.types.ids import StationId


class HistoricalAvailabilityPolicy(Enum):
    RECORDED_ONLY = "recorded_only"
    RETROSPECTIVE_CLOSURE = "retrospective_closure"


class HistoricalSampling(Enum):
    INTERVAL = "interval"
    SNAPSHOT = "snapshot"


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoricalSourcePeriod:
    station_id: StationId
    source: str
    parameter: str
    period: ForecastPeriodDefinition
    evidence: str
    stored_label_shift: timedelta = timedelta(0)
    input_support_displacement: timedelta = timedelta(0)
    sampling: HistoricalSampling = HistoricalSampling.INTERVAL

    def __post_init__(self) -> None:
        if any(
            offset % timedelta(seconds=1)
            for offset in (self.stored_label_shift, self.input_support_displacement)
        ):
            raise ValueError("historical source offsets require whole seconds")
        if (
            not self.source.strip()
            or not self.parameter.strip()
            or not self.evidence.strip()
        ):
            raise ValueError(
                "historical source declaration requires source, parameter and evidence"
            )
        if abs(self.input_support_displacement) >= self.period.grid.step:
            raise ValueError(
                "input support displacement must be smaller than a source period"
            )
        if (
            self.sampling is HistoricalSampling.SNAPSHOT
            and self.period.label is not PeriodLabel.START
        ):
            raise ValueError(
                "snapshot sampling requires a start-labelled sampling grid"
            )

    @property
    def key(self) -> tuple[StationId, str, str]:
        return self.station_id, self.source, self.parameter


@dataclass(frozen=True, kw_only=True, slots=True)
class PreparedHindcastInputs:
    station_id: StationId
    requirements: ModelDataRequirements
    targets: pl.DataFrame
    forcing: pl.DataFrame
    static: pl.DataFrame | None
    source_periods: tuple[HistoricalSourcePeriod, ...]
    availability_policy: HistoricalAvailabilityPolicy

    def for_issue(
        self, issue_time: UtcDatetime, forecast_horizon_steps: int
    ) -> StationModelInputs:
        if forecast_horizon_steps < 1:
            raise ValueError("hindcast horizon must be positive")
        declared = self.requirements.time_grid
        if declared is None:
            raise ValueError("prepared period inputs require a declared model grid")
        grid = declared.grid
        start, end = grid.lookback_bounds(issue_time, self.requirements.lookback_steps)
        label_offset = grid.step if declared.label is PeriodLabel.END else timedelta(0)

        def window(
            frame: pl.DataFrame, lo: UtcDatetime, hi: UtcDatetime
        ) -> pl.DataFrame:
            return frame.filter(
                (pl.col("timestamp") >= lo + label_offset)
                & (pl.col("timestamp") < hi + label_offset)
            )

        return StationModelInputs(
            station_id=self.station_id,
            data=StationInputData(
                past_targets=window(self.targets, start, end),
                past_dynamic=window(self.forcing, start, end),
                future_dynamic=window(
                    self.forcing,
                    end,
                    ensure_utc(end + forecast_horizon_steps * grid.step),
                ),
                static=self.static,
            ),
            issue_time=issue_time,
            forecast_horizon_steps=forecast_horizon_steps,
            time_step=grid.step,
            time_grid=declared,
        )
