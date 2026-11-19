"""Plan 540 M4: station readiness report types (facts only, no single verdict)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import QcStatus
    from sapphire_flow.types.ids import StationId


class CheckStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


CheckName = Literal[
    "station",
    "datum",
    "basin",
    "statics",
    "weather_bindings",
    "weather_history",
    "observations",
    "rating_curve",
    "discharge_series",
    "group_and_model",
    "discharge_lookback",
    "naming",
]


@dataclass(frozen=True, kw_only=True, slots=True)
class ReadinessCheck:
    name: CheckName
    status: CheckStatus
    detail: str


@dataclass(frozen=True, kw_only=True, slots=True)
class StationReadinessReport:
    station_id: StationId
    code: str
    network: str
    checks: tuple[ReadinessCheck, ...]

    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.FAIL)

    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.PASS)


@dataclass(frozen=True, kw_only=True, slots=True)
class ObservationSummaryRow:
    parameter: str
    source: str
    qc_status: QcStatus
    qc_rule_version: str | None
    count: int
    first: UtcDatetime
    last: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class ForcingSummaryRow:
    parameter: str
    source: str
    count: int
    first: UtcDatetime
    last: UtcDatetime
