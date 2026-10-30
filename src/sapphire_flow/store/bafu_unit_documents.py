from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sapphire_flow.types.bafu_capture import NormalizedDischargeCapture
from sapphire_flow.types.bafu_write_result import BafuOutcomeCounts
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.observation_units import SourceDischargeValue

if TYPE_CHECKING:
    from sqlalchemy import RowMapping

    from sapphire_flow.types.bafu_capture import BafuDischargeCapture


def _encode(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported evidence type: {type(value).__name__}")


def encode_document(value: object) -> str:
    return json.dumps(
        value,
        default=_encode,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    )


def _canonical_json(value: Any) -> str:
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        # Fixed-point formatting is exact and does not use Decimal context precision.
        number = format(value, "f")
        return number.rstrip("0").rstrip(".") if "." in number else number
    if isinstance(value, list):
        items = cast("list[Any]", value)
        return "[" + ",".join(_canonical_json(item) for item in items) + "]"
    if isinstance(value, dict):
        fields = cast("dict[str, Any]", value)
        return (
            "{"
            + ",".join(
                encode_document(key) + ":" + _canonical_json(fields[key])
                for key in sorted(fields)
            )
            + "}"
        )
    return encode_document(value)


def document_digest(value: object) -> str:
    # Parse the JSON spelling, not the binary float; preserve integer IDs exactly.
    parsed = json.loads(encode_document(value), parse_int=Decimal, parse_float=Decimal)
    return hashlib.sha256(_canonical_json(parsed).encode()).hexdigest()


class CountsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    inserted: int = Field(ge=0)
    restated: int = Field(ge=0)
    evidence_only: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    rejected: int = Field(ge=0)


class ObservationStateDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    station_id: UUID
    timestamp: datetime
    parameter: str
    source: Literal["measured"]
    value: float | None = Field(allow_inf_nan=False)
    rating_curve_id: UUID | None
    rating_curve_correction_version: str | None
    delivery_id: str | None
    qc_status: str
    qc_flags: list[dict[str, Any]] | None
    qc_rule_version: str | None

    @field_validator("timestamp")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("value")
    @classmethod
    def unsigned_zero(cls, value: float | None) -> float | None:
        return 0.0 if value == 0 else value


class ReceiptDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_captures: int = Field(ge=1)
    counts: CountsDocument
    event_ids: list[int]
    observations: list[dict[str, Any]]

    @model_validator(mode="after")
    def check_counts(self) -> ReceiptDocument:
        if sum(self.counts.model_dump().values()) != self.expected_captures:
            raise ValueError("receipt counts differ from expected captures")
        if len(self.event_ids) != self.expected_captures:
            raise ValueError("receipt event references differ from expected captures")
        if any(event_id < 1 for event_id in self.event_ids):
            raise ValueError("receipt event IDs must be positive")
        for state in self.observations:
            ObservationStateDocument.model_validate(state)
        return self

    def domain_counts(self) -> BafuOutcomeCounts:
        return BafuOutcomeCounts(**self.counts.model_dump())


class CaptureDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    source_literals: list[str]
    measurement_literals: list[str]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    received_at: str
    registry_version: str
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scientific: dict[str, Any]


def capture_document(
    capture: BafuDischargeCapture,
    *,
    registry_version: str,
    registry_sha256: str,
) -> dict[str, Any]:
    result = capture.result
    scientific: dict[str, Any] = {
        "gauge": capture.key.gauge.value,
        "kind": capture.key.kind,
        "measurement_time": result.measurement_time.isoformat()
        if result.measurement_time is not None
        else None,
    }
    if isinstance(result, NormalizedDischargeCapture):
        rule = result.resolution.rule
        scientific.update(
            source_value=result.source_value.value,
            canonical_value=result.canonical_value.value,
            unit=rule.unit.value,
            # Verification/refresh timestamps and registry packaging are not
            # scientific revisions. Evidence identity and validity are.
            policy={
                "rule_id": rule.rule_id,
                "evidence_reference": rule.evidence_reference,
                "evidence_sha256": rule.evidence_sha256,
                "valid_from": rule.valid_from.isoformat(),
                "valid_until": rule.valid_until.isoformat()
                if rule.valid_until is not None
                else None,
            },
        )
    else:
        literals: set[str] = set()
        for literal in capture.source_literals:
            try:
                literals.add(str(SourceDischargeValue.from_literal(literal).value))
            except ValueError:
                literals.add(literal)
        scientific.update(reason=result.reason.value, literals=sorted(literals))
        if result.measurement_time is None:
            scientific["measurement_literals"] = sorted(
                set(capture.measurement_literals)
            )
    return CaptureDocument(
        source_literals=list(capture.source_literals),
        measurement_literals=list(capture.measurement_literals),
        payload_sha256=capture.payload_sha256,
        received_at=capture.received_at.isoformat(),
        registry_version=registry_version,
        registry_sha256=registry_sha256,
        scientific=scientific,
    ).model_dump(mode="json")


def observation_state(row: RowMapping) -> dict[str, Any]:
    fields = ObservationStateDocument.model_fields
    return ObservationStateDocument.model_validate(
        {key: row[key] for key in fields}
    ).model_dump(mode="json")


def value_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in ObservationStateDocument.model_validate(state)
        .model_dump(mode="json")
        .items()
        if key not in ("qc_status", "qc_flags", "qc_rule_version")
    }


def counts_document(counts: BafuOutcomeCounts) -> CountsDocument:
    return CountsDocument(**asdict(counts))


class ReceiptStateReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    station_id: UUID
    timestamp: datetime
    parameter: str
    db_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
