from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sapphire_flow.types.bafu_capture import (
    DischargeParameterDiagnostic,
    NormalizedDischargeCapture,
    QuarantinedDischargeCapture,
)
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.observation_read import QcContextRequirement

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_capture import BafuDischargeCapture
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.enums import FetchOutcomeCause, ObservationSource
    from sapphire_flow.types.ids import (
        ObservationId,
        ObservationVersionId,
        RatingCurveId,
        StationId,
    )
    from sapphire_flow.types.observation_read import QcReadToken


@dataclass(frozen=True, kw_only=True, slots=True)
class RawObservation:
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float
    source: ObservationSource
    rating_curve_id: RatingCurveId | None = None
    rating_curve_correction_version: str | None = None
    delivery_id: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class Observation:
    id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float | None
    source: ObservationSource
    rating_curve_id: RatingCurveId | None
    rating_curve_correction_version: str | None
    qc_status: QcStatus
    qc_flags: list[QcFlag]
    qc_rule_version: str | None
    created_at: UtcDatetime
    delivery_id: str | None = None
    qc_read_token: QcReadToken | None = field(default=None, compare=False)
    qc_context_segment: int = field(default=0, compare=False)
    qc_context_requirement: QcContextRequirement = field(
        default=QcContextRequirement.ORDINARY, compare=False
    )

    def __post_init__(self) -> None:
        if self.qc_status == QcStatus.MISSING and self.value is not None:
            raise ValueError("Observation.value must be None when qc_status is MISSING")
        if self.qc_status != QcStatus.MISSING and self.value is None:
            raise ValueError(
                "Observation.value must not be None when qc_status is not MISSING"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class ArchivedObservationValue:
    """A discharge value superseded by a rating-curve reprocessing (Plan 035 Task 3).

    Archived before Flow 12 Branch A overwrites a rating-curve-derived observation,
    so the pre-reprocessing operational record survives.
    """

    id: ObservationVersionId
    observation_id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float | None  # None if the superseded observation was MISSING
    rating_curve_id: RatingCurveId  # curve that produced the archived value
    superseded_at: UtcDatetime
    superseded_by_curve_id: RatingCurveId  # curve that replaced it


@dataclass(frozen=True, kw_only=True, slots=True)
class StationFetchOutcome:
    """Plan 175 T3/D9 — one station's result from
    an observation adapter's batch fetch. ``failure_cause is
    None`` means the station polled cleanly (which may still mean zero
    observations, if nothing new was published)."""

    station_id: StationId
    observations: tuple[RawObservation, ...]
    failure_cause: FetchOutcomeCause | None
    failure_detail: str | None
    source_captures: tuple[BafuDischargeCapture, ...] = ()

    @property
    def parameter_diagnostics(self) -> tuple[DischargeParameterDiagnostic, ...]:
        return tuple(
            DischargeParameterDiagnostic(reason=capture.result.reason)
            for capture in self.source_captures
            if isinstance(capture.result, QuarantinedDischargeCapture)
        )

    def __post_init__(self) -> None:
        if (self.failure_cause is None) != (self.failure_detail is None):
            raise ValueError(
                "failure_cause and failure_detail must both be set or both "
                f"be None, got failure_cause={self.failure_cause!r} "
                f"failure_detail={self.failure_detail!r}"
            )
        normalized = tuple(
            capture.result
            for capture in self.source_captures
            if isinstance(capture.result, NormalizedDischargeCapture)
        )
        if self.failure_cause is not None and normalized:
            raise ValueError("failed station outcome cannot carry normalized captures")
        if self.source_captures:
            expected = {
                (result.measurement_time, result.canonical_value.value)
                for result in normalized
            }
            actual = {
                (obs.timestamp, obs.value)
                for obs in self.observations
                if obs.parameter == "discharge"
            }
            if expected != actual:
                raise ValueError(
                    "station discharge observations must match normalized captures"
                )
        if self.failure_cause is not None and self.observations:
            raise ValueError(
                "a failed StationFetchOutcome must not carry observations "
                f"(station_id={self.station_id!r}, cause={self.failure_cause!r})"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class HydroScraperBatchResult:
    """Plan 175 T3 — the typed batch result the ingest flow consumes instead
    of the plain-list ``StationDataSource.fetch_observations`` façade, so a
    mass-429 run can report which stations failed and why (D8/D9) instead of
    silently returning whatever partial data it scraped."""

    outcomes: tuple[StationFetchOutcome, ...]

    @property
    def observations(self) -> list[RawObservation]:
        return [obs for outcome in self.outcomes for obs in outcome.observations]

    @property
    def captured_outcomes(self) -> tuple[StationFetchOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.source_captures)

    @property
    def failed(self) -> tuple[StationFetchOutcome, ...]:
        return tuple(o for o in self.outcomes if o.failure_cause is not None)
