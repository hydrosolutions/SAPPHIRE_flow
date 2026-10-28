from dataclasses import asdict, replace
from datetime import timedelta

import pytest

from sapphire_flow.services.provisional_discharge import convert_reference_discharge
from sapphire_flow.store.rating_reference_store import (
    parse_reference_evidence,
    parse_reference_proof,
)
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.rating_reference import (
    OwnerAssumedZeroReference,
    RatingReferenceBasis,
    RatingReferenceProof,
    canonical_content,
    curve_snapshot,
    measurement_snapshot,
)
from tests.unit.services.test_provisional_discharge import NOW, inputs


def assumed_reference(
    proof: RatingReferenceProof | None = None,
) -> OwnerAssumedZeroReference:
    if proof is None:
        proof = inputs()[3]
    return OwnerAssumedZeroReference(
        id=proof.id,
        tenant_id=proof.tenant_id,
        station_id=proof.station_id,
        rating_curve_id=proof.rating_curve_id,
        curve=proof.curve,
        endpoint=proof.endpoint,
        api_station_id=proof.api_station_id,
        level_unit="m",
        curve_unit="m",
        level_unit_evidence_reference="fixture:feed-unit",
        level_unit_evidence_sha256="a" * 64,
        curve_unit_evidence_reference="fixture:curve-unit",
        curve_unit_evidence_sha256="b" * 64,
        assumed_by="owner",
        assumed_at=NOW,
        authority_reference="fixture:owner",
        authority_sha256="c" * 64,
        binding_by="operator",
        binding_at=NOW,
        binding_reference="fixture:mapping",
        binding_sha256="d" * 64,
    )


class TestOwnerAssumedZeroReference:
    def test_roundtrip_retains_separate_attribution(self) -> None:
        reference = assumed_reference()
        content = canonical_content(asdict(reference))
        assert parse_reference_evidence(content) == reference
        assert reference.basis is RatingReferenceBasis.OWNER_ASSUMED_SAME_ZERO
        assert not {"offset_m", "verified_at", "verified_by"} & asdict(reference).keys()

    def test_legacy_accessor_refuses_assumption(self) -> None:
        with pytest.raises(ValueError, match="verified|extra|missing"):
            parse_reference_proof(canonical_content(asdict(assumed_reference())))

    @pytest.mark.parametrize("field", ["assumed_at", "binding_at"])
    def test_naive_time_refused_before_normalization(self, field: str) -> None:
        data = asdict(assumed_reference())
        data[field] = "2026-10-01T00:00:00"
        with pytest.raises(ValueError, match="timezone|aware"):
            parse_reference_evidence(canonical_content(data))

    @pytest.mark.parametrize(
        "field,value",
        [
            ("offset_m", 0.0),
            ("verified_by", "owner"),
            ("basis", "verified"),
            ("authority_sha256", "x" * 64),
            ("level_unit_evidence_reference", " "),
        ],
    )
    def test_malformed_assumption_refused(self, field: str, value: object) -> None:
        data = asdict(assumed_reference())
        data[field] = value
        with pytest.raises(ValueError, match="validation|SHA256|unit evidence"):
            parse_reference_evidence(canonical_content(data))

    def test_missing_discriminator_is_not_verified_fallback(self) -> None:
        data = asdict(assumed_reference())
        del data["basis"]
        with pytest.raises(ValueError, match="validation"):
            parse_reference_evidence(canonical_content(data))

    def test_verified_bytes_remain_untagged(self) -> None:
        proof = inputs()[3]
        content = canonical_content(asdict(proof))
        assert "basis" not in asdict(proof)
        assert parse_reference_evidence(content) == proof
        assert proof.basis is RatingReferenceBasis.VERIFIED
        data = asdict(proof)
        data["basis"] = "verified"
        with pytest.raises(ValueError, match="validation"):
            parse_reference_evidence(canonical_content(data))

    def test_binding_cannot_predate_owner_authority(self) -> None:
        with pytest.raises(ValueError, match="binding"):
            replace(assumed_reference(), binding_at=NOW - timedelta(days=1))

    def test_conversion_remains_expired_and_attributed(self) -> None:
        obs, curve, feed, _ = inputs()
        reference = replace(
            assumed_reference(),
            tenant_id=feed.tenant_id,
            station_id=curve.station_id,
            rating_curve_id=curve.id,
            curve=curve_snapshot(curve),
        )
        result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=reference,
            at=NOW,
        )
        assert result.discharge == 15.0
        assert '"basis":"owner_assumed_same_zero"' in result.content
        assert (
            '"conversion_version":"expired-rating-owner-assumed-zero-v1"'
            in result.content
        )
        with pytest.raises(ValueError, match="expired"):
            convert_reference_discharge(
                observation=obs,
                curves=[replace(curve, valid_to=None)],
                feed_evidence=feed,
                reference_evidence=reference,
                at=NOW,
            )


class TestAssumedConversionGuards:
    @pytest.mark.parametrize(
        "status", [QcStatus.RAW, QcStatus.QC_FAILED, QcStatus.QC_UNCHECKED]
    )
    def test_assumption_does_not_pass_qc(self, status: QcStatus) -> None:
        obs, curve, feed, proof = inputs()
        with pytest.raises(ValueError, match="QC"):
            convert_reference_discharge(
                observation=replace(obs, qc_status=status),
                curves=[curve],
                feed_evidence=feed,
                reference_evidence=assumed_reference(proof),
                at=NOW,
            )

    @pytest.mark.parametrize("level", [0.9, 2.1])
    def test_assumption_does_not_allow_extrapolation(self, level: float) -> None:
        obs, curve, feed, proof = inputs()
        obs = replace(obs, value=level)
        feed = replace(feed, measurement=measurement_snapshot(obs))
        with pytest.raises(ValueError, match="range"):
            convert_reference_discharge(
                observation=obs,
                curves=[curve],
                feed_evidence=feed,
                reference_evidence=assumed_reference(proof),
                at=NOW,
            )

    def test_future_binding_rejected(self) -> None:
        obs, curve, feed, proof = inputs()
        reference = replace(
            assumed_reference(proof), binding_at=NOW + timedelta(seconds=1)
        )
        with pytest.raises(ValueError, match="future"):
            convert_reference_discharge(
                observation=obs,
                curves=[curve],
                feed_evidence=feed,
                reference_evidence=reference,
                at=NOW,
            )

    def test_changed_authority_changes_identity_not_discharge(self) -> None:
        obs, curve, feed, proof = inputs()
        reference = assumed_reference(proof)
        first = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=reference,
            at=NOW,
        )
        other = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=replace(reference, authority_reference="fixture:other"),
            at=NOW,
        )
        assert first.discharge == other.discharge
        assert first.fingerprint != other.fingerprint


class TestVerifiedGoldenBytes:
    def test_pre0073_reference_and_conversion_bytes(self) -> None:
        import json
        from pathlib import Path
        from uuid import UUID

        from sapphire_flow.types.rating_reference import content_digest
        from tests.unit.services.test_provisional_discharge import convert

        # Frozen from9dc7c39, not recomputed by the implementation under test.
        golden = json.loads(
            (
                Path(__file__).parents[2]
                / "fixtures"
                / "provisional_reference_0072.json"
            ).read_text()
        )
        obs, curve, feed, proof = inputs()
        curve = replace(curve, id=UUID(int=1), station_id=UUID(int=2), created_at=NOW)
        obs = replace(obs, id=UUID(int=3), station_id=curve.station_id, created_at=NOW)
        feed = replace(
            feed,
            id=UUID(int=4),
            tenant_id=UUID(int=5),
            station_id=curve.station_id,
            observation_id=obs.id,
            measurement=measurement_snapshot(obs),
        )
        proof = replace(
            proof,
            id=UUID(int=6),
            tenant_id=feed.tenant_id,
            station_id=curve.station_id,
            rating_curve_id=curve.id,
            curve=curve_snapshot(curve),
        )
        content = canonical_content(asdict(proof))
        assert content == golden["reference_content"]
        assert content_digest(content) == golden["reference_sha256"]
        result = convert(obs, curve, feed, proof)
        assert result.content == golden["provisional_content"]
        assert result.fingerprint == golden["provisional_sha256"]

    @pytest.mark.parametrize("field", ["assumed_at", "binding_at"])
    def test_direct_domain_rejects_naive_time(self, field: str) -> None:
        with pytest.raises(ValueError, match="aware time"):
            replace(assumed_reference(), **{field: NOW.replace(tzinfo=None)})


SNAPSHOT_FAILURES = (
    "unknown",
    "missing",
    "boolean",
    "numeric_string",
    "version_float",
    "duplicate_points",
    "duplicate_point",
    "escaped_duplicate",
    "nan",
    "overflow",
    "negative_zero",
    "naive_time",
    "offset_time",
    "short_fraction",
    "wrong_uuid",
    "point_extra",
    "unsorted",
    "ties",
    "provenance_type",
)


def malformed_inner_snapshot(content: str, mutation: str) -> str:
    import json
    import re

    data = json.loads(content)
    if mutation == "unknown":
        data["unexpected"] = True
    elif mutation == "missing":
        del data["delivery_id"]
    elif mutation == "boolean":
        data["points"][0]["water_level"] = False
    elif mutation == "numeric_string":
        data["points"][0]["discharge"] = "10.0"
    elif mutation == "version_float":
        data["version"] = 1.0
    elif mutation == "duplicate_points":
        return '{"points":[],' + content[1:]
    elif mutation in ("duplicate_point", "escaped_duplicate"):
        key = "water\\u005flevel" if mutation == "escaped_duplicate" else "water_level"
        return content.replace('"water_level":', '"' + key + '":0,"water_level":', 1)
    elif mutation == "nan":
        return re.sub(r'"discharge":[^,}]+', '"discharge":NaN', content, count=1)
    elif mutation == "overflow":
        return re.sub(r'"discharge":[^,}]+', '"discharge":1e400', content, count=1)
    elif mutation == "negative_zero":
        data["points"][0]["water_level"] = -0.0
    elif mutation == "naive_time":
        data["created_at"] = "2025-01-01T00:00:00"
    elif mutation == "offset_time":
        data["created_at"] = "2025-01-01T05:45:00+05:45"
    elif mutation == "short_fraction":
        data["created_at"] = "2025-01-01T00:00:00.000+00:00"
    elif mutation == "wrong_uuid":
        data["id"] = "invalid"
    elif mutation == "point_extra":
        data["points"][0]["unexpected"] = 1
    elif mutation == "unsorted":
        data["points"] = list(reversed(data["points"]))
    elif mutation == "ties":
        data["points"][1]["water_level"] = data["points"][0]["water_level"]
    elif mutation == "provenance_type":
        data["delivery_id"] = 10
    return canonical_content(data)


class TestAssumedCurveSnapshot:
    def test_canonical_content_has_one_representation(self) -> None:
        from sapphire_flow.types.rating_reference import AssumedCurveSnapshot

        with pytest.raises(ValueError, match="requires CurveSnapshot"):
            AssumedCurveSnapshot(content=inputs()[3].curve.content)

    def test_verified_constructor_refuses_lossless_assumed_type(self) -> None:
        import json

        from sapphire_flow.types.rating_reference import AssumedCurveSnapshot

        proof = inputs()[3]
        snapshot = AssumedCurveSnapshot(
            content=json.dumps(json.loads(proof.curve.content), indent=2)
        )
        with pytest.raises(ValueError, match="verified reference requires canonical"):
            replace(proof, curve=snapshot)  # type: ignore[arg-type]

    @pytest.mark.parametrize("mutation", SNAPSHOT_FAILURES)
    def test_strict_inner_validation(self, mutation: str) -> None:
        from sapphire_flow.types.rating_reference import assumed_curve_content

        if mutation == "point_extra":
            assumed_curve_content(
                malformed_inner_snapshot(inputs()[3].curve.content, mutation)
            )
            return
        with pytest.raises(ValueError, match="snapshot|curve|UUID|isoformat"):
            assumed_curve_content(
                malformed_inner_snapshot(inputs()[3].curve.content, mutation)
            )


class TestAssumedParserLimits:
    @pytest.mark.parametrize("token", ["1e-400", "-0", "NaN", "Infinity"])
    def test_rejects_unsafe_metadata_numbers(self, token: str) -> None:
        from sapphire_flow.types.rating_reference import assumed_curve_content

        content = inputs()[3].curve.content.replace(
            '"discharge":10.0', '"discharge":10.0,"metadata":' + token, 1
        )
        with pytest.raises(ValueError, match="snapshot"):
            assumed_curve_content(content)

    def test_year_boundary_offset_has_domain_error(self) -> None:
        import json

        from sapphire_flow.types.rating_reference import assumed_curve_content

        data = json.loads(inputs()[3].curve.content)
        data["created_at"] = "0001-01-01T00:00:00+01:00"
        with pytest.raises(ValueError, match="UTC timestamps"):
            assumed_curve_content(canonical_content(data))

    def test_decoder_recursion_is_domain_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.types.rating_reference import assumed_curve_content

        def exhausted_decoder(*args: object, **kwargs: object) -> object:
            raise RecursionError("bounded parser exhaustion witness")

        monkeypatch.setattr(
            "sapphire_flow.types.rating_reference.json.loads", exhausted_decoder
        )
        with pytest.raises(ValueError, match="snapshot") as info:
            assumed_curve_content("{}")
        assert isinstance(info.value.__cause__, RecursionError)
