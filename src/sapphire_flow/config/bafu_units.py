from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 -- Pydantic schema
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sapphire_flow.types.bafu_observation import LindasKind  # noqa: TC001
from sapphire_flow.types.bafu_unit_registry import (
    BafuDischargeKey,
    BafuGaugeCode,
    BafuUnitRegistry,
    BafuUnitRule,
)
from sapphire_flow.types.bafu_write_result import BafuJournalQuota
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.observation_units import DischargeSourceUnit


class _RulePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    gauge_code: str
    kind: LindasKind
    unit: Literal["l/s", "m3/s"]
    valid_from: datetime
    valid_until: datetime | None = None
    verified_at: datetime
    refresh_due: datetime
    evidence_reference: str
    evidence_sha256: str

    @field_validator(
        "valid_from", "valid_until", "verified_at", "refresh_due", mode="before"
    )
    @classmethod
    def parse_timestamp(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(
                "registry timestamps require ISO text with an explicit offset"
            )
        return ensure_utc(datetime.fromisoformat(value))

    def to_domain(self) -> BafuUnitRule:
        return BafuUnitRule(
            rule_id=self.rule_id,
            key=BafuDischargeKey(
                gauge=BafuGaugeCode(value=self.gauge_code), kind=self.kind
            ),
            unit=DischargeSourceUnit(self.unit),
            valid_from=ensure_utc(self.valid_from),
            valid_until=ensure_utc(self.valid_until)
            if self.valid_until is not None
            else None,
            verified_at=ensure_utc(self.verified_at),
            refresh_due=ensure_utc(self.refresh_due),
            evidence_reference=self.evidence_reference,
            evidence_sha256=self.evidence_sha256,
        )


class _RegistryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    source: Literal["lindas_foen_hydro"]
    parameter: Literal["discharge"]
    version: str
    rules: tuple[_RulePayload, ...]


def parse_bafu_unit_registry(raw: object) -> BafuUnitRegistry:
    payload = _RegistryPayload.model_validate(raw)
    return BafuUnitRegistry(
        version=payload.version, rules=tuple(rule.to_domain() for rule in payload.rules)
    )


class _QuotaPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    max_station_events: int = Field(gt=0)
    max_station_receipts: int = Field(gt=0)
    max_station_evidence_bytes: int = Field(gt=0)
    max_batch_bytes: int = Field(gt=0)


class _IngestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    registry: _RegistryPayload
    quota: _QuotaPayload


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuUnitIngestConfig:
    registry: BafuUnitRegistry
    quota: BafuJournalQuota


def load_bafu_unit_ingest_config(path: Path) -> BafuUnitIngestConfig:
    payload = _IngestPayload.model_validate(json.loads(path.read_text()))
    return BafuUnitIngestConfig(
        registry=BafuUnitRegistry(
            version=payload.registry.version,
            rules=tuple(rule.to_domain() for rule in payload.registry.rules),
        ),
        quota=BafuJournalQuota(**payload.quota.model_dump()),
    )
