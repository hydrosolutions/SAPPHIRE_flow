from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.daily_forecast_outcome import DailyOutputReference
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import QcStatus

from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.enums import EnsembleRepresentation


class DailyGridAnchor(Enum):
    ISSUE_TIME = "issue_time"
    NEXT_UTC_MIDNIGHT = "next_utc_midnight"


class DailyOutputEligibility(Enum):
    USABLE = "usable"
    PLAUSIBILITY_FLAGGED = "plausibility_flagged"
    INTEGRITY_REJECTED = "integrity_rejected"
    QC_UNASSESSED = "qc_unassessed"


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyOutputContract:
    parameter: str
    units: str
    representation: EnsembleRepresentation
    time_step: timedelta
    first_lead: timedelta
    horizon_steps: int
    series_keys: tuple[float, ...]
    minimum_horizon_steps: int | None = None
    grid_anchor: DailyGridAnchor = DailyGridAnchor.ISSUE_TIME

    def __post_init__(self) -> None:
        if not self.parameter.strip() or not self.units.strip():
            raise ValueError("daily output parameter and units must be explicit")
        if self.time_step <= timedelta(0) or self.first_lead < timedelta(0):
            raise ValueError(
                "daily output cadence must be positive and lead nonnegative"
            )
        if self.time_step.total_seconds() % 1 or self.first_lead.total_seconds() % 1:
            raise ValueError("daily output timing requires whole seconds")
        if (
            self.grid_anchor is DailyGridAnchor.NEXT_UTC_MIDNIGHT
            and self.time_step != timedelta(days=1)
        ):
            raise ValueError("UTC-midnight output anchor requires daily cadence")
        if self.horizon_steps <= 0:
            raise ValueError("daily output horizon must be positive")
        if self.minimum_horizon_steps is not None and not (
            1 <= self.minimum_horizon_steps <= self.horizon_steps
        ):
            raise ValueError("daily minimum horizon must be within the maximum horizon")
        keys = self.series_keys
        if (
            not keys
            or len(set(keys)) != len(keys)
            or any(not math.isfinite(k) for k in keys)
        ):
            raise ValueError("daily series keys must be nonempty, finite and unique")
        if self.representation is EnsembleRepresentation.QUANTILES:
            if (
                len(keys) < 7
                or min(keys) > 0.05
                or max(keys) < 0.95
                or any(not 0 <= k <= 1 for k in keys)
            ):
                raise ValueError(
                    "daily quantile levels must cover the forecast contract"
                )
        elif any(k < 0 or int(k) != k for k in keys):
            raise ValueError("daily member keys must be nonnegative integers")


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyOutputEvidence:
    reference: DailyOutputReference
    ensemble: ForecastEnsemble
    qc_status: QcStatus
    qc_flags: tuple[QcFlag, ...]
    provenance: DailyInputProvenance = DailyInputProvenance()
