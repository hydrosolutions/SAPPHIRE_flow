from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingDaySupport:
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
        if self.day.hour or self.day.minute or self.day.second or self.day.microsecond:
            raise ValueError("rating support requires UTC period-start day")
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
class RatingInputLineage:
    issue_time: UtcDatetime
    weather_cycle_time: UtcDatetime
    days: tuple[RatingDaySupport, ...]
    artifact_id: ArtifactId | None = None
    weather_record_ids: tuple[str, ...] = ()
    forcing_versions: tuple[tuple[str, str], ...] = ()
    policy: str = "utc-day-sample-first-mean-measured-precedence-v1"

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
