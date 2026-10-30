from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.adapters.bafu_discharge_units import (
    parse_discharge_literal,
)
from sapphire_flow.types.bafu_capture import (
    BafuDischargeCapture,
    DischargeCaptureFailure,
    NormalizedDischargeCapture,
    QuarantinedDischargeCapture,
)
from sapphire_flow.types.bafu_capture import (
    parse_bafu_measurement_time as parse_bafu_measurement_time,
)
from sapphire_flow.types.bafu_unit_registry import UnresolvedBafuUnit
from sapphire_flow.types.observation_units import DischargeUnderflowError

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_unit_registry import (
        BafuDischargeKey,
        BafuUnitRegistry,
    )
    from sapphire_flow.types.datetime import UtcDatetime


def capture_bafu_discharge(
    *,
    key: BafuDischargeKey,
    literals: tuple[str, ...],
    measurement_times: tuple[str, ...],
    payload_sha256: str,
    registry: BafuUnitRegistry,
    now: UtcDatetime,
) -> BafuDischargeCapture:
    timestamp = parse_bafu_measurement_time(measurement_times)
    if isinstance(timestamp, DischargeCaptureFailure):
        result: NormalizedDischargeCapture | QuarantinedDischargeCapture = (
            QuarantinedDischargeCapture(measurement_time=None, reason=timestamp)
        )
    else:
        result = _resolve_value(key, literals, timestamp, registry=registry, now=now)
    return BafuDischargeCapture(
        key=key,
        source_literals=literals,
        measurement_literals=measurement_times,
        payload_sha256=payload_sha256,
        received_at=now,
        result=result,
    )


def _resolve_value(
    key: BafuDischargeKey,
    literals: tuple[str, ...],
    timestamp: UtcDatetime,
    *,
    registry: BafuUnitRegistry,
    now: UtcDatetime,
) -> NormalizedDischargeCapture | QuarantinedDischargeCapture:
    if not literals:
        raise ValueError("discharge capture requires source literals")
    try:
        values = tuple(parse_discharge_literal(literal) for literal in literals)
    except ValueError:
        return QuarantinedDischargeCapture(
            measurement_time=timestamp, reason=DischargeCaptureFailure.INVALID_LITERAL
        )
    if len({value.value for value in values}) != 1:
        return QuarantinedDischargeCapture(
            measurement_time=timestamp,
            reason=DischargeCaptureFailure.CONFLICTING_LITERALS,
        )
    resolution = registry.resolve(key, timestamp, now=now)
    if isinstance(resolution, UnresolvedBafuUnit):
        return QuarantinedDischargeCapture(
            measurement_time=timestamp, reason=resolution.reason
        )
    try:
        return NormalizedDischargeCapture(
            measurement_time=timestamp, source_value=values[0], resolution=resolution
        )
    except DischargeUnderflowError:
        return QuarantinedDischargeCapture(
            measurement_time=timestamp,
            reason=DischargeCaptureFailure.NORMALIZATION_UNREPRESENTABLE,
        )
