from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Literal

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.observation_units import (
    SourceDischargeValue,
    normalize_discharge,
)

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_unit_registry import (
        BafuDischargeKey,
        ResolvedBafuUnit,
        UnitResolutionReason,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.observation_units import (
        CanonicalDischargeValue,
    )


class DischargeCaptureFailure(Enum):
    INVALID_LITERAL = "invalid_literal"
    NORMALIZATION_UNREPRESENTABLE = "normalization_unrepresentable"
    SUBJECT_MALFORMED = "subject_malformed"
    CONFLICTING_LITERALS = "conflicting_literals"
    MISSING_TIME = "missing_time"
    INVALID_TIME = "invalid_time"
    CONFLICTING_TIMES = "conflicting_times"


def parse_bafu_measurement_time(
    literals: tuple[str, ...],
) -> UtcDatetime | DischargeCaptureFailure:
    if not literals:
        return DischargeCaptureFailure.MISSING_TIME
    try:
        parsed = {ensure_utc(datetime.fromisoformat(literal)) for literal in literals}
    except ValueError:
        return DischargeCaptureFailure.INVALID_TIME
    if len(parsed) != 1:
        return DischargeCaptureFailure.CONFLICTING_TIMES
    return next(iter(parsed))


@dataclass(frozen=True, kw_only=True, slots=True)
class NormalizedDischargeCapture:
    measurement_time: UtcDatetime
    source_value: SourceDischargeValue
    resolution: ResolvedBafuUnit
    canonical_value: CanonicalDischargeValue = field(init=False)

    def __post_init__(self) -> None:
        if self.measurement_time.utcoffset() != timedelta(0):
            raise ValueError("canonical capture requires aware UTC measurement time")
        rule = self.resolution.rule
        if self.measurement_time < rule.valid_from or (
            rule.valid_until is not None and self.measurement_time >= rule.valid_until
        ):
            raise ValueError("canonical capture is outside unit validity")
        object.__setattr__(
            self, "canonical_value", normalize_discharge(self.source_value, rule.unit)
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class QuarantinedDischargeCapture:
    measurement_time: UtcDatetime | None
    reason: DischargeCaptureFailure | UnitResolutionReason

    def __post_init__(self) -> None:
        if (
            self.measurement_time is not None
            and self.measurement_time.utcoffset() != timedelta(0)
        ):
            raise ValueError("quarantine measurement time must be aware UTC or absent")
        time_failures = (
            DischargeCaptureFailure.MISSING_TIME,
            DischargeCaptureFailure.INVALID_TIME,
            DischargeCaptureFailure.CONFLICTING_TIMES,
        )
        if self.reason in time_failures and self.measurement_time is not None:
            raise ValueError("time-failure quarantine cannot assign a measurement time")
        if (
            self.reason
            not in (*time_failures, DischargeCaptureFailure.SUBJECT_MALFORMED)
            and self.measurement_time is None
        ):
            raise ValueError("non-time quarantine requires a measurement time")


@dataclass(frozen=True, kw_only=True, slots=True)
class DischargeParameterDiagnostic:
    reason: DischargeCaptureFailure | UnitResolutionReason
    parameter: Literal["discharge"] = field(default="discharge", init=False)


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuDischargeCapture:
    key: BafuDischargeKey
    source_literals: tuple[str, ...]
    measurement_literals: tuple[str, ...]
    payload_sha256: str
    received_at: UtcDatetime
    result: NormalizedDischargeCapture | QuarantinedDischargeCapture

    def __post_init__(self) -> None:
        if (
            isinstance(self.result, NormalizedDischargeCapture)
            and self.key != self.result.resolution.rule.key
        ):
            raise ValueError("capture key differs from resolved unit rule")
        if self.received_at.utcoffset() != timedelta(0):
            raise ValueError("capture received time must be aware UTC")
        if (
            isinstance(self.result, NormalizedDischargeCapture)
            and self.result.resolution.rule.verified_at > self.received_at
        ):
            raise ValueError("capture cannot use future verification")
        if not self.source_literals:
            raise ValueError("discharge capture requires source literals")
        if re.fullmatch(r"[0-9a-f]{64}", self.payload_sha256) is None:
            raise ValueError("capture payload hash must be lowercase SHA-256")
        if isinstance(self.result, NormalizedDischargeCapture):
            values = tuple(
                SourceDischargeValue.from_literal(literal)
                for literal in self.source_literals
            )
            if any(value != self.result.source_value for value in values):
                raise ValueError("normalized capture contradicts source literals")
        timestamp = parse_bafu_measurement_time(self.measurement_literals)
        if (
            isinstance(self.result, QuarantinedDischargeCapture)
            and self.result.reason
            in (
                DischargeCaptureFailure.MISSING_TIME,
                DischargeCaptureFailure.INVALID_TIME,
                DischargeCaptureFailure.CONFLICTING_TIMES,
            )
            and self.result.reason != timestamp
        ):
            raise ValueError("time-failure reason contradicts measurement literals")
        if (
            self.result.measurement_time is not None
            and timestamp != self.result.measurement_time
        ):
            kind = (
                "normalized"
                if isinstance(self.result, NormalizedDischargeCapture)
                else "quarantined"
            )
            raise ValueError(f"{kind} capture contradicts measurement literals")
