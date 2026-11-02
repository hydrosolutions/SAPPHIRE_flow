from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID  # noqa: TC003

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from sapphire_flow.store.rating_reference_store import parse_reference_evidence
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import ModelId, StationId, TenantId


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeRatingBinding:
    tenant_id: TenantId
    station_id: StationId
    station_code: str
    model_ids: frozenset[ModelId]
    reference: RatingReferenceEvidence
    custody_start: UtcDatetime
    custody_end: UtcDatetime
    custody_reference: str
    reconciliation_days: int


class ProvisionalRatingConfig(BaseModel):
    """Scoped opt-in with an attributed exact curve binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    station_id: UUID
    station_code: str = Field(min_length=1)
    network: Literal["dhm"] = "dhm"
    model_ids: tuple[str, ...] = Field(min_length=1)
    reference_content: str
    reconciliation_days: int = Field(ge=1, le=366)
    custody_start: AwareDatetime
    custody_end: AwareDatetime
    custody_reference: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> NativeRatingBinding:
        reference = parse_reference_evidence(self.reference_content)
        if (
            reference.tenant_id != self.tenant_id
            or reference.station_id != self.station_id
        ):
            raise ValueError("rating reference scope differs from configured opt-in")
        if self.custody_start >= self.custody_end or not self.custody_reference.strip():
            raise ValueError("native rating requires bounded source custody evidence")
        if any(not model.strip() for model in self.model_ids):
            raise ValueError("native rating requires model identities")
        return NativeRatingBinding(
            tenant_id=TenantId(self.tenant_id),
            station_id=StationId(self.station_id),
            station_code=self.station_code,
            model_ids=frozenset(ModelId(m) for m in self.model_ids),
            reference=reference,
            custody_start=ensure_utc(self.custody_start),
            custody_end=ensure_utc(self.custody_end),
            custody_reference=self.custody_reference,
            reconciliation_days=self.reconciliation_days,
        )


if TYPE_CHECKING:
    from sapphire_flow.types.rating_reference import RatingReferenceEvidence
