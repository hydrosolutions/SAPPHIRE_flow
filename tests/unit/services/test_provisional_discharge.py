from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.services.provisional_discharge import convert_provisional_discharge
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import (
    MeasurementFeedEvidenceId,
    RatingReferenceProofId,
    TenantId,
)
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.provisional_discharge import ProvisionalDischarge
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_reference import (
    MeasurementFeedEvidence,
    RatingReferenceProof,
    curve_snapshot,
    measurement_snapshot,
)
from tests.conftest import make_observation
from tests.unit.services.test_rating_conversion import _curve

NOW = ensure_utc(datetime(2026, 10, 1, tzinfo=UTC))


def inputs() -> tuple[
    Observation, RatingCurve, MeasurementFeedEvidence, RatingReferenceProof
]:
    curve = _curve(
        [
            {"water_level": 1.0, "discharge": 10.0},
            {"water_level": 2.0, "discharge": 20.0},
        ]
    )
    curve = replace(curve, valid_to=ensure_utc(datetime(2025, 6, 1, tzinfo=UTC)))
    obs = replace(
        make_observation(
            station_id=curve.station_id,
            parameter="water_level",
            value=1.5,
            timestamp=NOW,
        ),
        qc_rule_version="level-v1",
        qc_flags=[
            QcFlag(rule_id="range_check", rule_version="1", status=QcStatus.QC_PASSED)
        ],
    )
    tenant = TenantId(uuid4())
    common = dict(
        tenant_id=tenant,
        station_id=curve.station_id,
        endpoint="https://example.invalid/levels/",
        api_station_id=1,
        evidence_reference="fixture-reference",
        verified_by="fixture-reviewer",
        verified_at=NOW,
    )
    feed = MeasurementFeedEvidence(
        id=MeasurementFeedEvidenceId(uuid4()),
        observation_id=obs.id,
        measurement=measurement_snapshot(obs),
        **common,
    )
    proof = RatingReferenceProof(
        id=RatingReferenceProofId(uuid4()),
        rating_curve_id=curve.id,
        curve=curve_snapshot(curve),
        level_unit="m",
        curve_unit="m",
        level_reference="gauge_zero",
        curve_reference="gauge_zero",
        offset_m=0.0,
        **common,
    )
    return obs, curve, feed, proof


def convert(
    obs: Observation,
    curve: RatingCurve,
    feed: MeasurementFeedEvidence | None,
    proof: RatingReferenceProof | None,
) -> ProvisionalDischarge:
    return convert_provisional_discharge(
        observation=obs,
        curves=[curve],
        feed_evidence=feed,
        reference_proof=proof,
        at=NOW,
    )


class TestProvisionalConversion:
    def test_converts_with_exact_immutable_lineage(self) -> None:
        obs, curve, feed, proof = inputs()
        result = convert(obs, curve, feed, proof)
        assert result.discharge == 15.0
        assert result.curve == curve_snapshot(curve)
        assert result.measurement == measurement_snapshot(obs)
        assert result == convert(obs, curve, feed, proof)

    def test_rejects_measurement_restatement(self) -> None:
        obs, curve, feed, proof = inputs()
        with pytest.raises(ValueError, match="measurement"):
            convert(replace(obs, value=1.6), curve, feed, proof)

    def test_qc_only_change_keeps_feed_but_changes_identity(self) -> None:
        obs, curve, feed, proof = inputs()
        changed = replace(obs, qc_rule_version="level-v2")
        assert (
            convert(obs, curve, feed, proof).fingerprint
            != convert(changed, curve, feed, proof).fingerprint
        )

    @pytest.mark.parametrize(
        "status",
        [QcStatus.RAW, QcStatus.QC_UNCHECKED, QcStatus.QC_FAILED, QcStatus.QC_SUSPECT],
    )
    def test_rejects_unpassed_qc(self, status: QcStatus) -> None:
        obs, curve, feed, proof = inputs()
        with pytest.raises(ValueError, match="QC"):
            convert(replace(obs, qc_status=status), curve, feed, proof)

    @pytest.mark.parametrize("level", [0.9, 2.1])
    def test_rejects_clamped_endpoints(self, level: float) -> None:
        obs, curve, feed, proof = inputs()
        obs = replace(obs, value=level)
        feed = replace(feed, measurement=measurement_snapshot(obs))
        with pytest.raises(ValueError, match="range"):
            convert(obs, curve, feed, proof)

    def test_rejects_missing_feed(self) -> None:
        obs, curve, _, proof = inputs()
        with pytest.raises(ValueError, match="feed"):
            convert(obs, curve, None, proof)

    def test_newest_uses_chronology_before_version(self) -> None:
        obs, curve, feed, proof = inputs()
        older = replace(
            curve,
            id=uuid4(),
            version=999,
            valid_from=ensure_utc(datetime(2024, 1, 1, tzinfo=UTC)),
        )
        result = convert_provisional_discharge(
            observation=obs,
            curves=[older, curve],
            feed_evidence=feed,
            reference_proof=proof,
            at=NOW,
        )
        assert result.rating_curve_id == curve.id

    @pytest.mark.parametrize("valid_to", [None, NOW.replace(year=2027)])
    def test_newest_nonexpired_is_not_silently_skipped(
        self, valid_to: datetime | None
    ) -> None:
        obs, curve, feed, proof = inputs()
        newer = replace(
            curve,
            id=uuid4(),
            valid_from=NOW.replace(year=2026, month=1),
            valid_to=valid_to,
        )
        with pytest.raises(ValueError, match="expired"):
            convert_provisional_discharge(
                observation=obs,
                curves=[curve, newer],
                feed_evidence=feed,
                reference_proof=proof,
                at=NOW,
            )

    def test_duplicate_chronology_holds(self) -> None:
        obs, curve, feed, proof = inputs()
        with pytest.raises(ValueError, match="ambiguous"):
            convert_provisional_discharge(
                observation=obs,
                curves=[curve, replace(curve, id=uuid4())],
                feed_evidence=feed,
                reference_proof=proof,
                at=NOW,
            )

    def test_points_and_rules_order_do_not_change_identity(self) -> None:
        obs, curve, feed, proof = inputs()
        obs.qc_flags.append(
            QcFlag(rule_id="spike", rule_version="1", status=QcStatus.QC_PASSED)
        )
        first = convert(obs, curve, feed, proof)
        obs.qc_flags.reverse()
        curve.points.reverse()
        assert convert(obs, curve, feed, proof) == first

    def test_original_mutation_does_not_mutate_snapshot(self) -> None:
        obs, curve, feed, proof = inputs()
        result = convert(obs, curve, feed, proof)
        prior_content = result.content
        curve.points[0]["discharge"] = 999.0
        obs.qc_flags.clear()
        assert result.content == prior_content
        assert proof.curve == result.curve

    @pytest.mark.parametrize(
        "change",
        [
            dict(level_reference="unknown"),
            dict(curve_reference="unknown"),
            dict(level_unit="cm"),
            dict(offset_m=float("inf")),
        ],
    )
    def test_unknown_reference_units_and_transform_refused(
        self, change: dict[str, object]
    ) -> None:
        _, _, _, proof = inputs()
        with pytest.raises(ValueError):
            replace(proof, **change)

    def test_nonfinite_output_is_rejected(self) -> None:
        obs, curve, feed, proof = inputs()
        curve.points[0]["discharge"] = -1e308
        curve.points[1]["discharge"] = 1e308
        proof = replace(proof, curve=curve_snapshot(curve))
        with pytest.raises(ValueError, match="finite"):
            convert(obs, curve, feed, proof)

    def test_feed_endpoint_and_api_identity_must_match(self) -> None:
        obs, curve, feed, proof = inputs()
        with pytest.raises(ValueError, match="association"):
            convert(obs, curve, replace(feed, api_station_id=2), proof)

    def test_explicit_metre_offset_is_applied(self) -> None:
        obs, curve, feed, proof = inputs()
        proof = replace(proof, offset_m=0.5, level_reference="masl")
        assert convert(obs, curve, feed, proof).discharge == 20.0

    def test_unknown_proof_holds(self) -> None:
        obs, curve, feed, _ = inputs()
        with pytest.raises(ValueError, match="reference proof"):
            convert(obs, curve, feed, None)

    def test_missing_rule_generation_holds(self) -> None:
        obs, curve, feed, proof = inputs()
        with pytest.raises(ValueError, match="QC rule generation"):
            convert(replace(obs, qc_rule_version=None), curve, feed, proof)


class TestProvisionalCurveChronology:
    @pytest.mark.parametrize("position", ["before", "start", "within", "expired"])
    def test_only_backward_application_before_original_start_is_held(
        self, position: str
    ) -> None:
        from datetime import timedelta

        obs, curve, feed, proof = inputs()
        timestamp = {
            "before": curve.valid_from - timedelta(microseconds=1),
            "start": curve.valid_from,
            "within": curve.valid_from + timedelta(days=1),
            "expired": curve.valid_to + timedelta(days=1),
        }[position]
        obs = replace(obs, timestamp=timestamp)
        feed = replace(feed, measurement=measurement_snapshot(obs))
        if position == "before":
            with pytest.raises(ValueError, match="before curve validity"):
                convert(obs, curve, feed, proof)
        else:
            result = convert(obs, curve, feed, proof)
            assert isinstance(result, ProvisionalDischarge)
            assert result.measurement == measurement_snapshot(obs)
            assert result.curve == curve_snapshot(curve)
            assert result.discharge == 15.0

    def test_does_not_fall_back_to_an_older_curve_for_pre_start_measurement(
        self,
    ) -> None:
        from datetime import timedelta

        obs, curve, feed, proof = inputs()
        older = replace(
            curve,
            id=uuid4(),
            version=99,
            valid_from=curve.valid_from - timedelta(days=365),
        )
        obs = replace(obs, timestamp=curve.valid_from - timedelta(days=1))
        feed = replace(feed, measurement=measurement_snapshot(obs))
        with pytest.raises(ValueError, match="before curve validity"):
            convert_provisional_discharge(
                observation=obs,
                curves=[older, curve],
                feed_evidence=feed,
                reference_proof=proof,
                at=NOW,
            )
