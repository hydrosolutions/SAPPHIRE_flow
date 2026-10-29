from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.observation_units import DischargeSourceUnit

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_observation import LindasKind
    from sapphire_flow.types.datetime import UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuGaugeCode:
    value: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9]{4}", self.value) is None:
            raise ValueError("BAFU gauge code must contain four ASCII digits")


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuDischargeKey:
    gauge: BafuGaugeCode
    kind: LindasKind

    def __post_init__(self) -> None:
        if self.kind not in ("river", "lake"):
            raise ValueError("LINDAS kind must be river or lake")


def _require_utc(value: UtcDatetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError("registry timestamps must be aware UTC")


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuUnitRule:
    rule_id: str
    key: BafuDischargeKey
    unit: DischargeSourceUnit
    valid_from: UtcDatetime
    valid_until: UtcDatetime | None
    verified_at: UtcDatetime
    refresh_due: UtcDatetime
    evidence_reference: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        for value in (self.valid_from, self.verified_at, self.refresh_due):
            _require_utc(value)
        if self.valid_until is not None:
            _require_utc(self.valid_until)
            if self.valid_until <= self.valid_from:
                raise ValueError("unit validity end must follow its start")
        if self.refresh_due < self.verified_at:
            raise ValueError("refresh due must not precede verification")
        if not self.rule_id.strip() or self.rule_id != self.rule_id.strip():
            raise ValueError("unit rule ID must be nonempty and trimmed")
        if not self.evidence_reference.strip():
            raise ValueError("unit evidence reference must be nonempty")
        if re.fullmatch(r"[0-9a-f]{64}", self.evidence_sha256) is None:
            raise ValueError("unit evidence hash must be lowercase SHA-256")
        if self.unit not in tuple(DischargeSourceUnit):
            raise ValueError("unit rule requires a supported discharge source unit")


class UnitRefreshState(Enum):
    CURRENT = "current"
    DUE = "due"


class UnitResolutionReason(Enum):
    MISSING = "missing"
    OUTSIDE_VALIDITY = "outside_validity"
    NOT_YET_VERIFIED = "not_yet_verified"
    CONFLICT = "conflict"


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolvedBafuUnit:
    rule: BafuUnitRule
    refresh: UnitRefreshState


@dataclass(frozen=True, kw_only=True, slots=True)
class UnresolvedBafuUnit:
    reason: UnitResolutionReason


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuUnitRegistry:
    version: str
    rules: tuple[BafuUnitRule, ...]
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.version.strip() or self.version != self.version.strip():
            raise ValueError("registry version must be nonempty and trimmed")
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate unit rule ID")
        document = {
            "schema_version": 1,
            "source": "lindas_foen_hydro",
            "parameter": "discharge",
            "version": self.version,
            "rules": [
                _rule_document(rule)
                for rule in sorted(self.rules, key=lambda rule: rule.rule_id)
            ],
        }
        encoded = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        object.__setattr__(self, "sha256", hashlib.sha256(encoded.encode()).hexdigest())

    def resolve(
        self, key: BafuDischargeKey, measurement_time: UtcDatetime, *, now: UtcDatetime
    ) -> ResolvedBafuUnit | UnresolvedBafuUnit:
        _require_utc(measurement_time)
        _require_utc(now)
        matching = tuple(rule for rule in self.rules if rule.key == key)
        if not matching:
            return UnresolvedBafuUnit(reason=UnitResolutionReason.MISSING)
        valid = tuple(
            rule
            for rule in matching
            if rule.valid_from <= measurement_time
            and (rule.valid_until is None or measurement_time < rule.valid_until)
        )
        if not valid:
            return UnresolvedBafuUnit(reason=UnitResolutionReason.OUTSIDE_VALIDITY)
        available = tuple(rule for rule in valid if rule.verified_at <= now)
        if not available:
            return UnresolvedBafuUnit(reason=UnitResolutionReason.NOT_YET_VERIFIED)
        if len(available) != 1:
            return UnresolvedBafuUnit(reason=UnitResolutionReason.CONFLICT)
        rule = available[0]
        refresh = (
            UnitRefreshState.DUE
            if now >= rule.refresh_due
            else UnitRefreshState.CURRENT
        )
        return ResolvedBafuUnit(rule=rule, refresh=refresh)


def _rule_document(rule: BafuUnitRule) -> dict[str, str | None]:
    return {
        "rule_id": rule.rule_id,
        "gauge_code": rule.key.gauge.value,
        "kind": rule.key.kind,
        "unit": rule.unit.value,
        "valid_from": rule.valid_from.isoformat(),
        "valid_until": rule.valid_until.isoformat()
        if rule.valid_until is not None
        else None,
        "verified_at": rule.verified_at.isoformat(),
        "refresh_due": rule.refresh_due.isoformat(),
        "evidence_reference": rule.evidence_reference,
        "evidence_sha256": rule.evidence_sha256,
    }
