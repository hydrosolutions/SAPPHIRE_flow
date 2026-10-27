from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.forecast_reference import ForecastOutputKey
    from sapphire_flow.types.ids import StationId


class DailyOutputSource(Enum):
    FORECAST = "forecast"
    REJECTED = "rejected"


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyOutputReference:
    key: ForecastOutputKey
    source: DailyOutputSource
    output_id: UUID


class DailyMemberState(Enum):
    PENDING = "pending"
    PRODUCED = "produced"
    INPUT_EXCLUDED = "input_excluded"
    CONFIGURATION_EXCLUDED = "configuration_excluded"
    MODEL_FAILED = "model_failed"
    INTEGRITY_REJECTED = "integrity_rejected"


class DailyMemberReason(Enum):
    CADENCE_MISMATCH = "cadence_mismatch"
    MISSING_DECLARED_STATIC = "missing_declared_static"
    MISSING_REQUIRED_TARGET = "missing_required_target"
    DECLARED_STATIC_NOT_REPRESENTABLE = "declared_static_not_representable"
    INSUFFICIENT_FUTURE = "insufficient_future"
    INPUTS_UNAVAILABLE = "inputs_unavailable"
    NAN_TOLERANCE_EXCEEDED = "nan_tolerance_exceeded"
    MISSING_FORECAST_BINDING = "missing_forecast_binding"
    NOT_OPERATIONAL = "not_operational"
    ASSIGNMENT_CHANGED = "assignment_changed"
    MODEL_FAILURE = "model_failure"
    UNSUPPORTED_POST_STATE = "unsupported_post_state"
    OUTPUT_INTEGRITY = "output_integrity"


_REASONS: dict[DailyMemberState, frozenset[DailyMemberReason]] = {
    DailyMemberState.PENDING: frozenset(),
    DailyMemberState.PRODUCED: frozenset(),
    DailyMemberState.INPUT_EXCLUDED: frozenset(
        {
            DailyMemberReason.CADENCE_MISMATCH,
            DailyMemberReason.MISSING_DECLARED_STATIC,
            DailyMemberReason.MISSING_REQUIRED_TARGET,
            DailyMemberReason.DECLARED_STATIC_NOT_REPRESENTABLE,
            DailyMemberReason.INSUFFICIENT_FUTURE,
            DailyMemberReason.INPUTS_UNAVAILABLE,
            DailyMemberReason.NAN_TOLERANCE_EXCEEDED,
        }
    ),
    DailyMemberState.CONFIGURATION_EXCLUDED: frozenset(
        {
            DailyMemberReason.MISSING_FORECAST_BINDING,
            DailyMemberReason.NOT_OPERATIONAL,
            DailyMemberReason.ASSIGNMENT_CHANGED,
        }
    ),
    DailyMemberState.MODEL_FAILED: frozenset(
        {
            DailyMemberReason.MODEL_FAILURE,
            DailyMemberReason.UNSUPPORTED_POST_STATE,
        }
    ),
    DailyMemberState.INTEGRITY_REJECTED: frozenset(
        {DailyMemberReason.OUTPUT_INTEGRITY}
    ),
}


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyMemberOutcome:
    station_id: StationId
    required_parameters: tuple[str, ...]
    state: DailyMemberState
    references: tuple[DailyOutputReference, ...] = ()
    reason: DailyMemberReason | None = None
    provenance: DailyInputProvenance = DailyInputProvenance()

    def __post_init__(self) -> None:
        parameters = set(self.required_parameters)
        if (
            not parameters
            or len(parameters) != len(self.required_parameters)
            or any(not parameter.strip() for parameter in parameters)
        ):
            raise ValueError("required parameters must be nonempty and unique")
        if any(ref.key.station_id != self.station_id for ref in self.references):
            raise ValueError("reference station must match outcome station")
        identities = {(ref.source, ref.output_id) for ref in self.references}
        if len(identities) != len(self.references):
            raise ValueError("parameter references must identify distinct stored rows")
        contexts = {(ref.key.model_id, ref.key.issued_at) for ref in self.references}
        if len(contexts) > 1:
            raise ValueError("parameter references must share model and issue time")
        actual = [ref.key.parameter for ref in self.references]
        if self.state is DailyMemberState.PRODUCED:
            if set(actual) != parameters or len(actual) != len(parameters):
                raise ValueError(
                    "produced parameter references must match all required parameters"
                )
        elif self.state is DailyMemberState.INTEGRITY_REJECTED:
            if not self.references or any(
                ref.source is not DailyOutputSource.REJECTED for ref in self.references
            ):
                raise ValueError(
                    "integrity rejection requires committed rejected evidence"
                )
        elif self.references:
            raise ValueError("unproduced member cannot carry output references")
        allowed = _REASONS[self.state]
        if (allowed and self.reason not in allowed) or (
            not allowed and self.reason is not None
        ):
            raise ValueError("member reason must match outcome state")


def finalized_outcome_state(
    outcomes: tuple[DailyMemberOutcome, ...],
) -> DailyAttemptState:
    """Classify verified outcomes; this does not verify output storage or the roster."""
    if len({outcome.station_id for outcome in outcomes}) != len(outcomes):
        raise ValueError("duplicate member outcome")
    states = {outcome.state for outcome in outcomes}
    terminal = {
        DailyMemberState.PRODUCED,
        DailyMemberState.INPUT_EXCLUDED,
        DailyMemberState.CONFIGURATION_EXCLUDED,
    }
    if DailyMemberState.PRODUCED in states and states <= terminal:
        return DailyAttemptState.COMPLETE
    return DailyAttemptState.INCOMPLETE
