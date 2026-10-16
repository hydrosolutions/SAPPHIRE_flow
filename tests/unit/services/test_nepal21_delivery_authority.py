from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from uuid import UUID

import pytest

from sapphire_flow.adapters.dhm_files import RatingPoint, RatingTableBlock
from sapphire_flow.services.nepal21_delivery_authority import (
    MAX_WITNESS_BYTES,
    NEPAL21_STATION_CODES,
    ExpectedQcPolicyPin,
    WitnessBytes,
    canonical_json_bytes,
    classify_rating_block,
    declaration_digest,
    function_payload,
    predecessor_semantic_digest,
    sha256_hex_bytes,
    station_set_sha256,
    validate_witness_bytes,
    verify_manifest_catalog,
)
from sapphire_flow.types.domain import (
    ClimBaseline,
    QcRuleParams,
    QcRuleSet,
    StationQcOverride,
)
from sapphire_flow.types.ids import StationId


def _block(points: tuple[RatingPoint, ...]) -> RatingTableBlock:
    return RatingTableBlock(
        rating_type_label="1",
        from_date=date(1990, 1, 1),
        to_date=date(1990, 12, 31),
        from_stage_m=0.0,
        points=points,
    )


def test_canonical_json_refuses_nonfinite_numbers() -> None:
    with pytest.raises(ValueError, match="Out of range float"):
        canonical_json_bytes({"bad": float("nan")})


def test_station_set_hash_is_order_independent() -> None:
    assert station_set_sha256(
        tuple(reversed(NEPAL21_STATION_CODES))
    ) == station_set_sha256(NEPAL21_STATION_CODES)


def test_raw_declaration_digest_preserves_duplicate_order() -> None:
    first = _block(
        (
            RatingPoint(stage_m=0.0, discharge_m3s=1.0),
            RatingPoint(stage_m=0.0, discharge_m3s=1.0),
            RatingPoint(stage_m=1.0, discharge_m3s=2.0),
        )
    )
    second = replace(
        first,
        points=(
            RatingPoint(stage_m=0.0, discharge_m3s=1.0),
            RatingPoint(stage_m=0.0, discharge_m3s=2.0),
            RatingPoint(stage_m=1.0, discharge_m3s=1.0),
        ),
    )
    assert declaration_digest("602.5", first) != declaration_digest("602.5", second)


def test_duplicate_points_require_scoped_canonicalization_approval() -> None:
    block = _block(
        (
            RatingPoint(stage_m=0.0, discharge_m3s=1.0),
            RatingPoint(stage_m=0.0, discharge_m3s=1.0),
            RatingPoint(stage_m=1.0, discharge_m3s=2.0),
        )
    )
    assert classify_rating_block(block) == "duplicate_point_needs_approval"
    assert (
        classify_rating_block(block, canonicalization_approved=True)
        == "canonicalized_usable"
    )
    assert function_payload("602.5", block) is None
    assert function_payload("602.5", block, canonicalization_approved=True) == {
        "codec": "dhm21-canonical-json-v1",
        "station_code": "602.5",
        "interpolation": "linear",
        "stage_unit": "m",
        "discharge_unit": "m3/s",
        "reference": "delivered_stage",
        "points": [
            {"stage_m": 0.0, "discharge_m3s": 1.0},
            {"stage_m": 1.0, "discharge_m3s": 2.0},
        ],
    }


def test_unsorted_stage_with_monotone_discharge_is_usable_after_stage_sort() -> None:
    block = _block(
        (
            RatingPoint(stage_m=1.0, discharge_m3s=2.0),
            RatingPoint(stage_m=0.0, discharge_m3s=1.0),
        )
    )
    assert classify_rating_block(block) == "raw_usable"


def test_increasing_stage_with_decreasing_discharge_is_invalid() -> None:
    block = _block(
        (
            RatingPoint(stage_m=0.0, discharge_m3s=2.0),
            RatingPoint(stage_m=1.0, discharge_m3s=1.0),
        )
    )
    assert classify_rating_block(block) == "decreasing_invalid"
    assert function_payload("350", block) is None


def test_decreasing_curve_is_invalid_even_with_approval() -> None:
    block = _block(
        (
            RatingPoint(stage_m=0.0, discharge_m3s=2.0),
            RatingPoint(stage_m=1.0, discharge_m3s=1.0),
        )
    )
    assert (
        classify_rating_block(block, canonicalization_approved=True)
        == "decreasing_invalid"
    )
    assert function_payload("350", block, canonicalization_approved=True) is None


def test_predecessor_digest_changes_for_identity_qc_and_curve_field_drift() -> None:
    base = {
        "kind": "observation",
        "id": "obs-1",
        "station_id": "station-1",
        "timestamp": "1990-01-01T00:00:00+00:00",
        "parameter": "discharge",
        "source": "manual_import",
        "value": 1.0,
        "delivery_id": "dhm-barkhk-2026-09-08",
        "created_at": "2026-01-01T00:00:00+00:00",
        "qc_status": "raw",
        "qc_flags": [],
        "qc_rule_version": None,
        "rating_curve_id": "curve-1",
        "rating_curve_correction_version": 1,
        "points": [{"stage": 0.0, "q": 1.0}],
    }
    original = predecessor_semantic_digest([base])
    for key, value in (
        ("id", "obs-2"),
        ("created_at", "2026-01-02T00:00:00+00:00"),
        ("qc_status", "qc_passed"),
        ("qc_flags", [{"rule_id": "range"}]),
        ("qc_rule_version", "v1"),
        ("rating_curve_correction_version", 2),
    ):
        changed = {**base, key: value}
        assert predecessor_semantic_digest([changed]) != original

    curve_base = {
        "kind": "rating_curve",
        "id": "curve-1",
        "station_id": "station-1",
        "version": 1,
        "valid_from": "1990-01-01T00:00:00+00:00",
        "valid_to": "1991-01-01T00:00:00+00:00",
        "points": [{"stage": 0.0, "q": 1.0}],
        "interpolation": "linear",
        "uploaded_by": "delivery",
        "created_at": "2026-01-01T00:00:00+00:00",
        "delivery_id": "dhm-barkhk-2026-09-08",
        "rating_type_label": "1",
    }
    curve_original = predecessor_semantic_digest([curve_base])
    for key, value in (
        ("id", "curve-2"),
        ("created_at", "2026-01-02T00:00:00+00:00"),
        ("points", [{"stage": 0.0, "q": 2.0}]),
        ("version", 2),
        ("valid_to", "1992-01-01T00:00:00+00:00"),
    ):
        changed = {**curve_base, key: value}
        assert predecessor_semantic_digest([changed]) != curve_original


def test_predecessor_digest_rejects_omitted_required_fields() -> None:
    with pytest.raises(ValueError, match="missing required fields"):
        predecessor_semantic_digest(
            [
                {
                    "kind": "observation",
                    "id": "obs-1",
                    "station_id": "station-1",
                }
            ]
        )


def test_witness_validation_rejects_mismatched_hash_duplicate_keys_and_size() -> None:
    good = WitnessBytes(kind="manifest_catalog", content=b'{"a":1}')
    validate_witness_bytes(good, expected_sha256=good.sha256)
    with pytest.raises(ValueError, match="do not match"):
        validate_witness_bytes(good, expected_sha256="0" * 64)
    dup = WitnessBytes(kind="manifest_catalog", content=b'{"a":1,"a":2}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        validate_witness_bytes(dup, expected_sha256=dup.sha256)
    too_large = WitnessBytes(
        kind="manifest_catalog", content=b"{" + b" " * MAX_WITNESS_BYTES + b"}"
    )
    with pytest.raises(ValueError, match="size cap"):
        validate_witness_bytes(too_large, expected_sha256=too_large.sha256)


def test_manifest_catalog_requires_exact_roster_and_unique_raw_declarations() -> None:
    raw_payload = {"station_code": "225", "points": [{"stage": 0.0, "q": 1.0}]}
    declaration = {
        "station_code": "225",
        "declaration_content_sha256": sha256_hex_bytes(
            canonical_json_bytes(raw_payload)
        ),
        "raw_declaration_payload_json": raw_payload,
    }
    payload = {
        "station_codes": list(NEPAL21_STATION_CODES),
        "rating_declarations": [declaration],
    }
    witness = WitnessBytes(
        kind="manifest_catalog", content=canonical_json_bytes(payload)
    )
    assert verify_manifest_catalog(witness)["station_codes"] == list(
        NEPAL21_STATION_CODES
    )

    duplicated = WitnessBytes(
        kind="manifest_catalog",
        content=canonical_json_bytes(
            {**payload, "rating_declarations": [declaration, declaration]}
        ),
    )
    with pytest.raises(ValueError, match="duplicate declaration"):
        verify_manifest_catalog(duplicated)

    weakened = WitnessBytes(
        kind="manifest_catalog",
        content=canonical_json_bytes(
            {**payload, "station_codes": list(NEPAL21_STATION_CODES[:-1])}
        ),
    )
    with pytest.raises(ValueError, match="station scope"):
        verify_manifest_catalog(weakened)

    absent_declarations = WitnessBytes(
        kind="manifest_catalog",
        content=canonical_json_bytes({"station_codes": list(NEPAL21_STATION_CODES)}),
    )
    with pytest.raises(ValueError, match="declarations must be explicit"):
        verify_manifest_catalog(absent_declarations)

    missing_payload = WitnessBytes(
        kind="manifest_catalog",
        content=canonical_json_bytes(
            {
                "station_codes": list(NEPAL21_STATION_CODES),
                "rating_declarations": [
                    {"station_code": "225", "declaration_content_sha256": "1" * 64}
                ],
            }
        ),
    )
    with pytest.raises(ValueError, match="raw payload binding"):
        verify_manifest_catalog(missing_payload)


def test_canonical_json_is_stable_and_utf8_hashable() -> None:
    assert canonical_json_bytes({"b": 1, "a": [2]}) == b'{"a":[2],"b":1}'
    assert sha256_hex_bytes(canonical_json_bytes({"b": 1, "a": [2]})) == (
        "63c9663de90ee828bbda6cd9acf02d0c653986c1ec25aa239920641edc9a1de5"
    )


@pytest.mark.parametrize(
    "changed_field",
    [
        "threshold",
        "rule_version",
        "rule_set_version",
        "cadence",
        "network",
        "station_network",
        "override",
        "baseline",
    ],
)
def test_qc_policy_bytes_bind_complete_input_bundle(changed_field: str) -> None:
    station_id = StationId(UUID("00000000-0000-0000-0000-000000000001"))
    rule = QcRuleParams(
        rule_id="range_check",
        rule_version="synthetic-v1",
        parameter="discharge",
        time_step=timedelta(days=1),
        thresholds={"value_min": 0.0, "value_max": 10.0},
        network="dhm",
    )
    pin = ExpectedQcPolicyPin(
        policy_sha256="0" * 64,
        rule_set=QcRuleSet(version="synthetic-v1", rules=(rule,)),
        station_networks={station_id: "dhm"},
        overrides=[],
        baselines=[],
    )
    original = pin.content_bytes
    match changed_field:
        case "threshold":
            rule.thresholds["value_max"] = 100.0
            changed = pin
        case "rule_version":
            changed = replace(
                pin,
                rule_set=replace(
                    pin.rule_set, rules=(replace(rule, rule_version="v2"),)
                ),
            )
        case "rule_set_version":
            changed = replace(pin, rule_set=replace(pin.rule_set, version="v2"))
        case "cadence":
            changed = replace(
                pin,
                rule_set=replace(
                    pin.rule_set, rules=(replace(rule, time_step=timedelta(hours=1)),)
                ),
            )
        case "network":
            changed = replace(
                pin,
                rule_set=replace(pin.rule_set, rules=(replace(rule, network=None),)),
            )
        case "station_network":
            changed = replace(pin, station_networks={station_id: "other"})
        case "override":
            changed = replace(
                pin,
                overrides=[
                    StationQcOverride(
                        station_id=station_id,
                        rule_id="range_check",
                        parameter="discharge",
                        time_step=timedelta(days=1),
                        thresholds={"value_max": 20.0},
                    )
                ],
            )
        case "baseline":
            changed = replace(
                pin,
                baselines=[
                    ClimBaseline(
                        station_id=station_id,
                        parameter="discharge",
                        day_of_year=1,
                        rolling_mean=5.0,
                        rolling_std=2.0,
                        sample_count=10,
                    )
                ],
            )
        case _:
            raise AssertionError("unknown mutation")
    assert sha256_hex_bytes(changed.content_bytes) != sha256_hex_bytes(original)


def test_qc_policy_bytes_refuse_nonfinite_thresholds() -> None:
    pin = ExpectedQcPolicyPin(
        policy_sha256="0" * 64,
        rule_set=QcRuleSet(
            version="synthetic",
            rules=(
                QcRuleParams(
                    rule_id="range_check",
                    rule_version="v1",
                    parameter="discharge",
                    time_step=timedelta(days=1),
                    thresholds={"value_max": float("nan")},
                ),
            ),
        ),
        station_networks={},
        overrides=[],
        baselines=[],
    )
    with pytest.raises(ValueError, match="must be finite"):
        _ = pin.content_bytes
