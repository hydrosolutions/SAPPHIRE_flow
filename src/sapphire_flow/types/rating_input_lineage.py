from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from sapphire_flow.types.enums import AggregationMethod

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
    from sapphire_flow.types.ids import ArtifactId, ObservationId
    from sapphire_flow.types.native_capture import Sha256Digest


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingSampleSupport:
    day: UtcDatetime
    fingerprints: tuple[str, ...]
    accepted: int
    rejected: int
    extrapolated: int
    assessed: int
    unassessed: int
    first: UtcDatetime | None
    last: UtcDatetime | None
    warnings: tuple[str, ...]
    measured_rejected: int = 0

    def __post_init__(self) -> None:
        counts = (
            self.accepted,
            self.rejected,
            self.extrapolated,
            self.assessed,
            self.unassessed,
            self.measured_rejected,
        )
        if (
            any(count < 0 for count in counts)
            or self.assessed + self.unassessed != self.accepted
        ):
            raise ValueError("invalid rating support counts")
        if self.accepted == 0 or self.extrapolated > self.accepted:
            raise ValueError("rating support requires accepted samples")
        if (
            self.first is None
            or self.last is None
            or not (self.day <= self.first <= self.last < self.day + timedelta(days=1))
        ):
            raise ValueError("rating support timestamps are outside the day")
        if len(set(self.fingerprints)) != len(self.fingerprints):
            raise ValueError("rating support has duplicate fingerprints")
        if len(self.fingerprints) != self.accepted + self.rejected or any(
            re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
            for fingerprint in self.fingerprints
        ):
            raise ValueError("invalid rating support fingerprints")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingDaySupport(RatingSampleSupport):
    def __post_init__(self) -> None:
        RatingSampleSupport.__post_init__(self)
        if self.day.hour or self.day.minute or self.day.second or self.day.microsecond:
            raise ValueError("rating support requires UTC period-start day")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingUnreconciledSource:
    observation_id: ObservationId
    timestamp: UtcDatetime
    source_snapshot_sha256: Sha256Digest


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingInputLineage:
    issue_time: UtcDatetime
    weather_cycle_time: UtcDatetime
    days: tuple[RatingDaySupport, ...]
    artifact_id: ArtifactId | None = None
    weather_record_ids: tuple[str, ...] = ()
    forcing_versions: tuple[tuple[str, str], ...] = ()
    policy: str = "utc-day-sample-first-mean-measured-precedence-v1"
    unreconciled_sources: tuple[RatingUnreconciledSource, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.days
            or self.policy != "utc-day-sample-first-mean-measured-precedence-v1"
        ):
            raise ValueError("rating lineage requires consumed daily evidence")
        if any(day.day + timedelta(days=1) > self.issue_time for day in self.days):
            raise ValueError("rating lineage contains nonhistorical day")
        if len({day.day for day in self.days}) != len(self.days):
            raise ValueError("rating lineage has duplicate days")

        if len({source.observation_id for source in self.unreconciled_sources}) != len(
            self.unreconciled_sources
        ):
            raise ValueError("rating lineage contains duplicate omitted sources")
        if any(
            source.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
            > self.issue_time
            for source in self.unreconciled_sources
        ):
            raise ValueError("rating lineage contains nonhistorical omitted source")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingPeriodSupport(RatingSampleSupport):
    available_at: UtcDatetime

    def __post_init__(self) -> None:
        RatingSampleSupport.__post_init__(self)
        if self.available_at < self.day + timedelta(days=1):
            raise ValueError("rating availability precedes period closure")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingPeriodInputLineage:
    issue_time: UtcDatetime
    period_definition: ForecastPeriodDefinition
    availability_basis: Literal["database_recording_upper_bound"] = (
        "database_recording_upper_bound"
    )
    weather_cycle_time: UtcDatetime
    days: tuple[RatingPeriodSupport, ...]
    artifact_id: ArtifactId | None = None
    weather_record_ids: tuple[str, ...] = ()
    forcing_versions: tuple[tuple[str, str], ...] = ()
    policy: str = "declared-day-sample-first-measured-precedence-v2"
    unreconciled_sources: tuple[RatingUnreconciledSource, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.days
            or self.policy != "declared-day-sample-first-measured-precedence-v2"
        ):
            raise ValueError("rating lineage requires consumed daily evidence")
        if self.period_definition.grid.step != timedelta(
            days=1
        ) or self.period_definition.aggregation not in (
            AggregationMethod.MEAN,
            AggregationMethod.MAX,
        ):
            raise ValueError("rating periods require daily mean or max")
        if self.availability_basis != "database_recording_upper_bound":
            raise ValueError("unknown rating availability basis")
        if any(
            self.period_definition.grid.floor(day.day) != day.day
            or day.available_at > self.issue_time
            for day in self.days
        ):
            raise ValueError("rating period grid or availability differs from issue")
        if any(day.day + timedelta(days=1) > self.issue_time for day in self.days):
            raise ValueError("rating lineage contains nonhistorical day")
        if len({day.day for day in self.days}) != len(self.days):
            raise ValueError("rating lineage has duplicate days")

        if len({source.observation_id for source in self.unreconciled_sources}) != len(
            self.unreconciled_sources
        ):
            raise ValueError("rating lineage contains duplicate omitted sources")
        if any(
            self.period_definition.grid.floor(source.timestamp) + timedelta(days=1)
            > self.issue_time
            for source in self.unreconciled_sources
        ):
            raise ValueError("rating lineage contains nonhistorical omitted source")
