from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import NwpCycleSource

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import InputQualityFlag
    from sapphire_flow.types.enums import InputQualityLevel


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyInputProvenance:
    nwp_source: NwpCycleSource | None = None
    nwp_reference_time: UtcDatetime | None = None
    nwp_age_hours: float | None = None
    observation_staleness_hours: float | None = None
    input_quality: InputQualityLevel | None = None
    input_quality_flags: tuple[InputQualityFlag, ...] = ()

    def __post_init__(self) -> None:
        ages = (self.nwp_age_hours, self.observation_staleness_hours)
        if any(age is not None and (not math.isfinite(age) or age < 0) for age in ages):
            raise ValueError("daily input age must be finite and nonnegative")
        if self.nwp_reference_time is not None:
            if self.nwp_source in (None, NwpCycleSource.RUNOFF_ONLY):
                raise ValueError("daily weather reference requires a weather source")
            if self.nwp_reference_time.utcoffset() != timedelta(0):
                raise ValueError("daily weather reference must be aware UTC")
        elif self.nwp_age_hours is not None:
            raise ValueError("daily weather age requires its original reference")
        if self.input_quality_flags and self.input_quality is None:
            raise ValueError("daily quality flags require a recorded verdict")
        if len(self.input_quality_flags) > 32 or any(
            len(flag.detail) > 1024 for flag in self.input_quality_flags
        ):
            raise ValueError("daily quality details exceed their bounded summary")
