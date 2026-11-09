from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sapphire_flow.types.ids import (  # noqa: TC001 — boundary schema annotations
    ArtifactId,
    ForecastId,
    RatingCurveId,
    StationId,
    TenantId,
)

HistoryWarning = Literal[
    "owner_assumed_same_zero_unverified",
    "rating_expired_at_measurement",
    "qc_unassessed",
    "extrapolated_below",
    "extrapolated_above",
    "partial_daily_support",
    "accepted_measured_discharge_absent",
    "rating_expired_at_issue",
    "rating_unreconciled_sources_omitted",
    "measured_discharge_rejected",
]
RejectionCode = Literal[
    "source_qc_rejected", "missing_source", "nonfinite_source", "conversion_rejected"
]


def require_utc(value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError("rating history requires UTC timestamps")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingHistorySample:
    timestamp: datetime
    day: datetime
    discharge: float | None
    rejection: RejectionCode | None
    warnings: tuple[HistoryWarning, ...]
    curve_id: RatingCurveId
    curve_digest: str
    valid_from: datetime
    valid_to: datetime | None

    def __post_init__(self) -> None:
        for value in (self.timestamp, self.day, self.valid_from):
            require_utc(value)
        if self.valid_to is not None:
            require_utc(self.valid_to)
        if not (self.day <= self.timestamp < self.day + timedelta(days=1)):
            raise ValueError("sample outside rating day")
        if (self.discharge is None) != (self.rejection is not None):
            raise ValueError("rating result disagreement")
        if self.discharge is not None and (
            not math.isfinite(self.discharge) or self.discharge < 0
        ):
            raise ValueError("invalid rating discharge")
        if len(self.curve_digest) != 64 or any(
            c not in "0123456789abcdef" for c in self.curve_digest
        ):
            raise ValueError("invalid frozen curve digest")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingHistorySupport:
    day: datetime
    accepted: int
    rejected: int
    extrapolated: int
    assessed: int
    unassessed: int
    measured_rejected: int
    first: datetime
    last: datetime
    warnings: tuple[HistoryWarning, ...]

    def __post_init__(self) -> None:
        for value in (self.day, self.first, self.last):
            require_utc(value)
        counts = (
            self.accepted,
            self.rejected,
            self.extrapolated,
            self.assessed,
            self.unassessed,
            self.measured_rejected,
        )
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError("invalid rating support count")
        if self.accepted == 0 or self.assessed + self.unassessed != self.accepted:
            raise ValueError("invalid accepted support")
        if self.extrapolated > self.accepted:
            raise ValueError("invalid extrapolation support")
        if not self.day <= self.first <= self.last < self.day + timedelta(days=1):
            raise ValueError("invalid support timestamps")
        if len(set(self.warnings)) != len(self.warnings):
            raise ValueError("duplicate support warnings")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingHistoryEvidence:
    forecast_id: ForecastId
    station_id: StationId
    tenant_id: TenantId
    issued_at: datetime
    artifact_id: ArtifactId
    start: datetime
    end: datetime
    days: tuple[RatingHistorySupport, ...]
    samples: tuple[RatingHistorySample, ...]
    omissions: tuple[datetime, ...]

    def __post_init__(self) -> None:
        for value in (self.issued_at, self.start, self.end, *self.omissions):
            require_utc(value)
        if self.end != self.issued_at.replace(
            hour=0, minute=0, second=0, microsecond=0
        ):
            raise ValueError("rating window differs from issue")
        if self.start != self.end - timedelta(days=7):
            raise ValueError("invalid rating window")
        if len(self.days) > 7 or len(self.samples) > 10000:
            raise ValueError("rating history exceeds bounds")
        if len({day.day for day in self.days}) != len(self.days):
            raise ValueError("duplicate rating days")
        if len({s.timestamp for s in self.samples}) != len(self.samples):
            raise ValueError("duplicate rating source timestamp")
        if any(
            not self.start <= d.day < self.end
            or d.day != d.day.replace(hour=0, minute=0, second=0, microsecond=0)
            for d in self.days
        ):
            raise ValueError("invalid retained rating day")
        if any(not self.start <= t < self.end for t in self.omissions):
            raise ValueError("invalid rating omission timestamp")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingHistoryCurve:
    curve_id: RatingCurveId
    content_sha256: str
    valid_from: datetime
    valid_to: datetime | None
    warnings: tuple[HistoryWarning, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingHistoryDay:
    day: datetime
    status: Literal["available", "no_retained_derived_day"]
    daily_discharge: float | None
    support: RatingHistorySupport | None
    curves: tuple[RatingHistoryCurve, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastRatingHistory:
    forecast_id: ForecastId
    station_id: StationId
    issued_at: datetime
    artifact_id: ArtifactId
    start: datetime
    end: datetime
    days: tuple[RatingHistoryDay, ...]
    data_use: Literal["provisional_rating"] = "provisional_rating"
    resolution: Literal["P1D"] = "P1D"
    unit: Literal["m3/s"] = "m3/s"
    provenance: Literal["sap_assembly_derived_daily"] = "sap_assembly_derived_daily"
    label: Literal["rating-derived daily discharge used in SAP input assembly"] = (
        "rating-derived daily discharge used in SAP input assembly"
    )
    final_model_tensor: Literal["not_retained"] = "not_retained"
