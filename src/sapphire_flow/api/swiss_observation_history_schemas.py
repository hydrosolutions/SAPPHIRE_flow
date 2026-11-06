from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.observation_history import HistoryGrid, HistoryWindow

Count = Annotated[int, Field(strict=True, ge=0, le=2500)]
CoarseTuple = tuple[FiniteFloat | None, FiniteFloat | None, FiniteFloat | None, Count]
NativeTuple = tuple[datetime, FiniteFloat]


class HistoryStation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    network: str = Field(min_length=1)
    code: str = Field(min_length=1)


class UnknownCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["unknown"] = "unknown"


class HistoryCoarseGrid(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grid_start: datetime
    step_seconds: Literal[10800] = 10800
    block_count: int = Field(ge=0, le=56, strict=True)
    first_start: datetime
    first_block_clipped: bool


class ObservationHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station: HistoryStation
    coverage: UnknownCoverage = Field(default_factory=UnknownCoverage)
    coarse: list[CoarseTuple] = Field(max_length=56)
    native: list[NativeTuple] = Field(max_length=2500)

    @model_validator(mode="after")
    def consistent_cohort(self) -> Self:
        for average, low, high, count in self.coarse:
            if count == 0:
                if (average, low, high) != (None, None, None):
                    raise ValueError("empty block requires null statistics")
            elif (
                average is None
                or low is None
                or high is None
                or not low <= average <= high
            ):
                raise ValueError("nonempty block requires ordered statistics")
            elif count == 1 and not low == average == high:
                raise ValueError("history singleton requires equal statistics")
        if sum(block[3] for block in self.coarse) + len(self.native) > 2500:
            raise ValueError("history station cohort exceeds bound")
        return self


class SwissObservationHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    generation_id: UUID
    generated_at: datetime
    as_of: datetime
    expires_at: datetime
    window_hours: Literal[72, 168]
    window_start: datetime
    native_start: datetime
    parameter: Literal["discharge"] = "discharge"
    observation_source: Literal["measured"] = "measured"
    observation_display_unit: Literal["m3/s"] = "m3/s"
    coarse: HistoryCoarseGrid
    station_count: int = Field(ge=0, le=200, strict=True)
    items: list[ObservationHistoryItem] = Field(max_length=200)

    @model_validator(mode="after")
    def consistent_snapshot(self) -> Self:
        timestamps = (
            self.generated_at,
            self.as_of,
            self.expires_at,
            self.window_start,
            self.native_start,
            self.coarse.grid_start,
            self.coarse.first_start,
        )
        if any(t.utcoffset() != timedelta(0) for t in timestamps):
            raise ValueError("history timestamps must be UTC")
        grid = HistoryGrid(
            as_of=ensure_utc(self.as_of), window=HistoryWindow(self.window_hours)
        )
        if self.generated_at != self.as_of or self.expires_at != self.as_of + timedelta(
            minutes=20
        ):
            raise ValueError("history generation age metadata is inconsistent")
        if (
            self.window_start,
            self.native_start,
            self.coarse.grid_start,
            self.coarse.first_start,
            self.coarse.block_count,
            self.coarse.first_block_clipped,
        ) != (
            grid.start,
            grid.native_start,
            grid.grid_start,
            grid.start,
            grid.block_count,
            grid.start != grid.grid_start,
        ):
            raise ValueError("history time grid is inconsistent")
        if self.station_count != len(self.items) or len(
            {item.station.id for item in self.items}
        ) != len(self.items):
            raise ValueError("history station identities must be unique and counted")
        for item in self.items:
            if len(item.coarse) != grid.block_count:
                raise ValueError("history block length differs from grid")
            times = [point[0] for point in item.native]
            if any(
                t.utcoffset() != timedelta(0)
                or not self.native_start <= t <= self.as_of
                for t in times
            ) or any(a >= b for a, b in zip(times, times[1:], strict=False)):
                raise ValueError(
                    "history native timestamps must be ordered within bounds"
                )
        return self
