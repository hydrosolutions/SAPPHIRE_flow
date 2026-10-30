from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.config.bafu_units import parse_bafu_unit_registry
from sapphire_flow.types.bafu_unit_registry import (
    BafuDischargeKey,
    BafuGaugeCode,
    ResolvedBafuUnit,
    UnitRefreshState,
    UnitResolutionReason,
    UnresolvedBafuUnit,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.observation_units import DischargeSourceUnit

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.bafu_observation import LindasKind


def instant(day: int) -> UtcDatetime:
    return ensure_utc(datetime(2026, 10, day, tzinfo=UTC))


def rule(**changes: object) -> dict[str, object]:
    return {
        "rule_id": "melera-v1",
        "gauge_code": "2206",
        "kind": "river",
        "unit": "l/s",
        "valid_from": "2026-10-20T00:00:00Z",
        "valid_until": "2026-10-25T00:00:00Z",
        "verified_at": "2026-10-20T00:00:00Z",
        "refresh_due": "2026-10-23T00:00:00Z",
        "evidence_reference": "synthetic-unit-contract",
        "evidence_sha256": "a" * 64,
        **changes,
    }


def registry(*rules: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": "lindas_foen_hydro",
        "parameter": "discharge",
        "version": "test-v1",
        "rules": list(rules),
    }


def key(code: str = "2206", kind: LindasKind = "river") -> BafuDischargeKey:
    return BafuDischargeKey(gauge=BafuGaugeCode(value=code), kind=kind)


class TestParseBafuUnitRegistry:
    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("unit", "gallons/s", "unit"),
            ("gauge_code", "22", "four ASCII digits"),
            ("evidence_sha256", "bad", "SHA-256"),
            ("evidence_reference", "", "reference"),
            ("valid_from", "2026-10-20T00:00:00", "Naive datetime"),
            ("valid_until", "2026-10-20T00:00:00Z", "end must follow"),
            ("refresh_due", "2026-10-19T00:00:00Z", "refresh due"),
        ],
    )
    def test_rejects_invalid_rules(
        self, field: str, value: object, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            parse_bafu_unit_registry(registry(rule(**{field: value})))

    def test_does_not_accept_is_liter_as_a_unit_declaration(self) -> None:
        with pytest.raises(ValueError, match="isLiter"):
            parse_bafu_unit_registry(registry(rule(isLiter=True)))

    def test_rejects_duplicate_rule_ids(self) -> None:
        with pytest.raises(ValueError, match="duplicate unit rule ID"):
            parse_bafu_unit_registry(registry(rule(), rule()))

    def test_rejects_foreign_source(self) -> None:
        with pytest.raises(ValueError, match="source"):
            parse_bafu_unit_registry({**registry(rule()), "source": "dhm"})

    def test_normalizes_aware_offsets_at_boundary(self) -> None:
        parsed = parse_bafu_unit_registry(
            registry(rule(valid_from="2026-10-20T01:00:00+01:00"))
        )
        assert parsed.rules[0].valid_from == instant(20)


class TestResolveBafuUnit:
    def test_matches_measurement_time_and_source_kind(self) -> None:
        parsed = parse_bafu_unit_registry(registry(rule()))
        result = parsed.resolve(key(), instant(20), now=instant(21))
        assert isinstance(result, ResolvedBafuUnit)
        assert result.rule.unit is DischargeSourceUnit.LITRES_PER_SECOND
        assert result.refresh is UnitRefreshState.CURRENT
        assert parsed.resolve(
            key(kind="lake"), instant(20), now=instant(21)
        ) == UnresolvedBafuUnit(reason=UnitResolutionReason.MISSING)

    @pytest.mark.parametrize("day", [19, 25])
    def test_validity_is_half_open(self, day: int) -> None:
        parsed = parse_bafu_unit_registry(registry(rule()))
        assert parsed.resolve(
            key(), instant(day), now=instant(26)
        ) == UnresolvedBafuUnit(reason=UnitResolutionReason.OUTSIDE_VALIDITY)

    def test_refresh_due_warns_without_withholding(self) -> None:
        parsed = parse_bafu_unit_registry(registry(rule()))
        result = parsed.resolve(key(), instant(21), now=instant(23))
        assert isinstance(result, ResolvedBafuUnit)
        assert result.refresh is UnitRefreshState.DUE

    def test_unknown_gauge_has_no_default_unit(self) -> None:
        parsed = parse_bafu_unit_registry(registry(rule()))
        assert parsed.resolve(
            key("2044"), instant(21), now=instant(23)
        ) == UnresolvedBafuUnit(reason=UnitResolutionReason.MISSING)

    def test_future_verification_cannot_authorize_a_read(self) -> None:
        parsed = parse_bafu_unit_registry(
            registry(rule(verified_at="2026-10-22T00:00:00Z"))
        )
        assert parsed.resolve(
            key(), instant(21), now=instant(21)
        ) == UnresolvedBafuUnit(reason=UnitResolutionReason.NOT_YET_VERIFIED)

    @pytest.mark.parametrize("conflicting_unit", ["l/s", "m3/s"])
    def test_conflict_is_order_independent_and_scoped_to_gauge(
        self, conflicting_unit: str
    ) -> None:
        first = rule()
        other = rule(rule_id="conflict", unit=conflicting_unit)
        control = rule(rule_id="control", gauge_code="2044", unit="m3/s")
        for ordered in ((first, other, control), (control, other, first)):
            parsed = parse_bafu_unit_registry(registry(*ordered))
            assert parsed.resolve(
                key(), instant(21), now=instant(23)
            ) == UnresolvedBafuUnit(reason=UnitResolutionReason.CONFLICT)
            assert isinstance(
                parsed.resolve(key("2044"), instant(21), now=instant(23)),
                ResolvedBafuUnit,
            )

    def test_nonoverlapping_revisions_resolve_independently(self) -> None:
        parsed = parse_bafu_unit_registry(
            registry(
                rule(),
                rule(
                    rule_id="next",
                    unit="m3/s",
                    valid_from="2026-10-25T00:00:00Z",
                    valid_until=None,
                ),
            )
        )
        result = parsed.resolve(key(), instant(25), now=instant(25))
        assert isinstance(result, ResolvedBafuUnit)
        assert result.rule.rule_id == "next"


class TestRegistryEvidenceIdentity:
    @pytest.mark.parametrize("timestamp", [20261020, "20261020", 0.0])
    def test_rejects_epoch_or_offset_free_policy_times(self, timestamp: object) -> None:
        with pytest.raises(ValueError, match="ISO text|Naive datetime"):
            parse_bafu_unit_registry(registry(rule(valid_from=timestamp)))

    def test_digest_is_independent_of_rule_order(self) -> None:
        first = rule()
        second = rule(rule_id="control", gauge_code="2044", unit="m3/s")
        assert (
            parse_bafu_unit_registry(registry(first, second)).sha256
            == parse_bafu_unit_registry(registry(second, first)).sha256
        )

    def test_policy_evidence_change_changes_digest(self) -> None:
        assert (
            parse_bafu_unit_registry(registry(rule())).sha256
            != parse_bafu_unit_registry(registry(rule(evidence_sha256="b" * 64))).sha256
        )


@pytest.mark.parametrize(
    "quota", [{}, {"max_station_events": True}, {"max_station_events": 0}]
)
def test_ingest_config_has_no_implicit_quota(
    tmp_path: Path, quota: dict[str, object]
) -> None:
    from pydantic import ValidationError

    from sapphire_flow.config.bafu_units import load_bafu_unit_ingest_config

    path = tmp_path / "unit-ingest.json"
    path.write_text(json.dumps({"registry": registry(rule()), "quota": quota}))
    with pytest.raises(ValidationError, match="max_station"):
        load_bafu_unit_ingest_config(path)


def test_ingest_config_parses_explicit_registry_and_quota(tmp_path: Path) -> None:
    from sapphire_flow.config.bafu_units import load_bafu_unit_ingest_config

    path = tmp_path / "unit-ingest.json"
    path.write_text(
        json.dumps(
            {
                "registry": registry(rule()),
                "quota": {
                    "max_station_events": 10,
                    "max_station_receipts": 10,
                    "max_station_evidence_bytes": 10000,
                    "max_batch_bytes": 1000,
                },
            }
        )
    )
    parsed = load_bafu_unit_ingest_config(path)
    assert parsed.registry.rules[0].unit is DischargeSourceUnit.LITRES_PER_SECOND
    assert parsed.quota.max_batch_bytes == 1000
