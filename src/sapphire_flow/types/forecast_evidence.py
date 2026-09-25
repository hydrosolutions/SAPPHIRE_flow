from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.domain import StationThreshold
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.weather import WeatherForecastRecord


class EvidenceStatus(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "evidence_incomplete"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationSourceEvidence:
    observations: tuple[Observation, ...] = ()
    freshness_observations: tuple[Observation, ...] = ()
    historical_forcing: tuple[RawHistoricalForcing, ...] = ()
    future_weather: tuple[WeatherForecastRecord, ...] = ()
    tail_weather: tuple[WeatherForecastRecord, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastEvidence:
    status: EvidenceStatus
    manifest_json: str
    snapshot: bytes | None
    snapshot_sha256: str | None
    artifact: bytes | None
    artifact_sha256: str | None
    reason: str | None = None
    thresholds: tuple[StationThreshold, ...] | None = None

    def __post_init__(self) -> None:
        if self.status is EvidenceStatus.COMPLETE and (
            self.snapshot is None or self.snapshot_sha256 is None
        ):
            raise ValueError("complete forecast evidence requires a snapshot")
        if self.status is EvidenceStatus.INCOMPLETE and not self.reason:
            raise ValueError("incomplete forecast evidence requires a reason")

    def with_thresholds(
        self, thresholds: tuple[StationThreshold, ...]
    ) -> ForecastEvidence:
        return replace(self, thresholds=thresholds)


@dataclass(frozen=True, kw_only=True, slots=True)
class PersistedForecastEvidence:
    status: EvidenceStatus
    manifest_json: str
    snapshot: bytes | None
    snapshot_sha256: str | None
    artifact: bytes | None
    artifact_sha256: str | None
    thresholds_json: str | None
    reason: str | None


def incomplete_evidence(reason: str) -> ForecastEvidence:
    return ForecastEvidence(
        status=EvidenceStatus.INCOMPLETE,
        manifest_json="{}",
        snapshot=None,
        snapshot_sha256=None,
        artifact=None,
        artifact_sha256=None,
        reason=reason,
    )
