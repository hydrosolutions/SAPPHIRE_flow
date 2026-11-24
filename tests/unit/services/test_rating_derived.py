from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from enum import StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from sapphire_flow.services.rating_derived import (
    derive_rating_sample,
    select_eligible_rating,
)
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.rating_derived import NativeFeedCustody
from tests.unit.services.test_provisional_discharge import NOW, inputs

if TYPE_CHECKING:
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge


class TestRatingDerived:
    @pytest.mark.parametrize("status", [QcStatus.RAW, QcStatus.QC_UNCHECKED])
    def test_unassessed_not_certified_and_retry_excludes_wall_time(
        self, status: QcStatus
    ) -> None:
        obs, curve, feed, ref = inputs()
        obs = replace(obs, value=3.0, qc_status=status, qc_flags=[])
        custody = NativeFeedCustody(
            tenant_id=feed.tenant_id,
            station_id=feed.station_id,
            endpoint=feed.endpoint,
            api_station_id=feed.api_station_id,
            evidence_reference="fixture native custody",
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=2),
        )
        kwargs = dict(
            file_id=uuid4(),
            file_sha256="a" * 64,
            delivered_on="2026-09-08",
            feed=custody,
            reference=ref,
        )
        first = derive_rating_sample(obs, curve, at=NOW, **kwargs)
        retry = derive_rating_sample(obs, curve, at=NOW + timedelta(days=1), **kwargs)
        assert first.fingerprint == retry.fingerprint
        assert first.discharge == 30.0
        assert first.warnings == (
            "rating_expired_at_measurement",
            "qc_unassessed",
            "extrapolated_above",
        )
        assert obs.qc_status is status
        assert (
            derive_rating_sample(
                replace(obs, qc_rule_version="changed"), curve, at=NOW, **kwargs
            ).fingerprint
            != first.fingerprint
        )

    @pytest.mark.parametrize(
        "status", [QcStatus.QC_FAILED, QcStatus.QC_SUSPECT, QcStatus.MISSING]
    )
    def test_bad_qc_is_recorded_rejection(self, status: QcStatus) -> None:
        obs, curve, feed, ref = inputs()
        obs = replace(
            obs,
            qc_status=status,
            value=None if status is QcStatus.MISSING else obs.value,
        )
        custody = NativeFeedCustody(
            tenant_id=feed.tenant_id,
            station_id=feed.station_id,
            endpoint=feed.endpoint,
            api_station_id=feed.api_station_id,
            evidence_reference="fixture native custody",
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        result = derive_rating_sample(
            obs,
            curve,
            file_id=uuid4(),
            file_sha256="a" * 64,
            delivered_on="2026-09-08",
            feed=custody,
            reference=ref,
            at=NOW,
        )
        assert result.discharge is None
        assert result.rejection == "source_qc_rejected"

    def test_future_curve_does_not_replace_historical_curve(self) -> None:
        _, curve, _, _ = inputs()
        future = replace(curve, valid_from=NOW + timedelta(days=1))
        assert select_eligible_rating([curve, future], NOW) == curve
        with pytest.raises(ValueError, match="no eligible historical block"):
            select_eligible_rating([future], NOW)


@pytest.mark.parametrize(
    "value,literal",
    [
        (float("nan"), "NaN"),
        (float("inf"), "+Inf"),
        (float("-inf"), "-Inf"),
        (None, None),
    ],
)
@pytest.mark.parametrize("status", [QcStatus.RAW, QcStatus.QC_FAILED])
def test_unusable_values_preserve_canonical_rejected_source(
    value: float | None, literal: str | None, status: QcStatus
) -> None:
    import json

    obs, curve, feed, ref = inputs()
    custody = NativeFeedCustody(
        tenant_id=feed.tenant_id,
        station_id=feed.station_id,
        endpoint=feed.endpoint,
        api_station_id=feed.api_station_id,
        evidence_reference="fixture",
        start=NOW - timedelta(days=1),
        end=NOW + timedelta(days=1),
    )
    result = derive_rating_sample(
        replace(
            obs,
            value=value,
            qc_status=QcStatus.MISSING if value is None else status,
            qc_flags=[],
        ),
        curve,
        file_id=uuid4(),
        file_sha256="a" * 64,
        delivered_on="2026-09-08",
        feed=custody,
        reference=ref,
        at=NOW,
    )
    assert result.discharge is None
    assert result.rejection is not None
    assert json.loads(result.content)["source"]["value"] == (
        {"nonfinite_float": literal} if literal else None
    )


def test_verified_reference_offset_is_applied_once() -> None:
    obs, curve, feed, ref = inputs()
    custody = NativeFeedCustody(
        tenant_id=feed.tenant_id,
        station_id=feed.station_id,
        endpoint=feed.endpoint,
        api_station_id=feed.api_station_id,
        evidence_reference="fixture",
        start=NOW - timedelta(days=1),
        end=NOW + timedelta(days=1),
    )
    result = derive_rating_sample(
        obs,
        curve,
        file_id=uuid4(),
        file_sha256="a" * 64,
        delivered_on="2026-09-08",
        feed=custody,
        reference=replace(ref, offset_m=1.0),
        at=NOW,
    )
    assert result.discharge == 25.0
    assert obs.value == 1.5


class TestNativeCaptureDerivation:
    def test_capture_changes_evidence_not_numbers_or_expiry(self) -> None:
        import json

        from sapphire_flow.types.native_capture import (
            CanonicalProviderIdentity,
            CapturedLevelReference,
            CapturedSourceVersion,
            CaptureId,
            NativeCapture,
        )
        from sapphire_flow.types.rating_derived import NativeCaptureFeed
        from sapphire_flow.types.rating_reference import measurement_snapshot

        obs, curve, feed, ref = inputs()
        capture = NativeCapture(
            id=CaptureId(uuid4()),
            source_version=CapturedSourceVersion(
                observation_id=obs.id,
                station_id=obs.station_id,
                tenant_id=feed.tenant_id,
                measurement=measurement_snapshot(obs),
                provider=CanonicalProviderIdentity.parse(
                    feed.endpoint, feed.api_station_id
                ),
                level_reference=CapturedLevelReference.GAUGE_ZERO,
            ),
        )
        custody = NativeFeedCustody(
            tenant_id=feed.tenant_id,
            station_id=feed.station_id,
            endpoint=feed.endpoint,
            api_station_id=feed.api_station_id,
            evidence_reference="fixture",
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        kwargs = dict(
            file_id=uuid4(),
            file_sha256="a" * 64,
            delivered_on="2026-09-08",
            reference=ref,
        )
        legacy = derive_rating_sample(obs, curve, feed=custody, at=NOW, **kwargs)
        native = derive_rating_sample(
            obs, curve, feed=NativeCaptureFeed(capture=capture), at=NOW, **kwargs
        )
        assert (native.discharge, native.warnings) == (
            legacy.discharge,
            legacy.warnings,
        )
        payload = json.loads(native.content)
        assert payload["curve"]["valid_to"] == curve.valid_to.isoformat()
        assert payload["feed"]["capture_id"] == str(capture.id)
        assert payload["policy"] == "native-rating-linear-endpoints-v2"
        assert native == derive_rating_sample(
            obs,
            curve,
            feed=NativeCaptureFeed(capture=capture),
            at=NOW + timedelta(hours=1),
            **kwargs,
        )
        changed_qc = derive_rating_sample(
            replace(obs, qc_status=QcStatus.QC_FAILED),
            curve,
            feed=NativeCaptureFeed(capture=capture),
            at=NOW,
            **kwargs,
        )
        assert changed_qc.rejection == "source_qc_rejected"
        assert changed_qc.fingerprint != native.fingerprint
        with pytest.raises(ValueError, match="current raw measurement"):
            derive_rating_sample(
                replace(obs, value=2.2),
                curve,
                feed=NativeCaptureFeed(capture=capture),
                at=NOW,
                **kwargs,
            )


class GoldenCase(StrEnum):
    CUSTODY_VERIFIED = auto()
    CUSTODY_ASSUMED = auto()
    NATIVE_VERIFIED = auto()
    NATIVE_ASSUMED = auto()
    NONCANONICAL_ASSUMED = auto()
    BELOW = auto()
    ABOVE = auto()
    RAW = auto()
    QC_FAILED = auto()
    MISSING = auto()
    NAN = auto()
    POSITIVE_INF = auto()
    NEGATIVE_INF = auto()
    NEGATIVE_DISCHARGE = auto()
    INVALID_INTERPOLATION = auto()


def golden_derivation(case: GoldenCase) -> RatingDerivedDischarge:
    from sapphire_flow.types.enums import InterpolationMethod
    from sapphire_flow.types.ids import (
        ObservationId,
        RatingCurveId,
        RatingReferenceProofId,
        StationId,
        TenantId,
    )
    from sapphire_flow.types.native_capture import (
        CanonicalProviderIdentity,
        CapturedLevelReference,
        CapturedSourceVersion,
        CaptureId,
        NativeCapture,
    )
    from sapphire_flow.types.rating_derived import NativeCaptureFeed
    from sapphire_flow.types.rating_reference import (
        AssumedCurveSnapshot,
        curve_snapshot,
        measurement_snapshot,
    )
    from tests.unit.types.test_rating_reference import assumed_reference

    obs, curve, original_feed, reference = inputs()
    curve = replace(
        curve,
        id=RatingCurveId(UUID(int=1)),
        station_id=StationId(UUID(int=2)),
        created_at=NOW,
        points=[
            {**point, "metadata": {"label": "synthetic café 雪", "checked": True}}
            for point in curve.points
        ],
        interpolation=InterpolationMethod.LOG_LINEAR
        if case is GoldenCase.INVALID_INTERPOLATION
        else curve.interpolation,
    )
    values = {
        GoldenCase.BELOW: 0.5,
        GoldenCase.ABOVE: 3.0,
        GoldenCase.NEGATIVE_DISCHARGE: -1.0,
        GoldenCase.MISSING: None,
        GoldenCase.NAN: float("nan"),
        GoldenCase.POSITIVE_INF: float("inf"),
        GoldenCase.NEGATIVE_INF: float("-inf"),
    }
    status = {
        GoldenCase.RAW: QcStatus.RAW,
        GoldenCase.QC_FAILED: QcStatus.QC_FAILED,
        GoldenCase.MISSING: QcStatus.MISSING,
    }.get(case, obs.qc_status)
    obs = replace(
        obs,
        id=ObservationId(UUID(int=3)),
        station_id=curve.station_id,
        created_at=NOW,
        value=values.get(case, obs.value),
        qc_status=status,
        qc_context_segment=7,
        qc_flags=[]
        if case is GoldenCase.RAW
        else [replace(flag, detail="synthetic café 雪") for flag in obs.qc_flags],
    )
    custody = NativeFeedCustody(
        tenant_id=TenantId(UUID(int=5)),
        station_id=curve.station_id,
        endpoint=original_feed.endpoint,
        api_station_id=original_feed.api_station_id,
        evidence_reference="synthetic custody",
        start=NOW - timedelta(days=1),
        end=NOW + timedelta(days=1),
    )
    reference = replace(
        reference,
        id=RatingReferenceProofId(UUID(int=6)),
        tenant_id=custody.tenant_id,
        station_id=curve.station_id,
        rating_curve_id=curve.id,
        curve=curve_snapshot(curve),
    )
    evidence = (
        assumed_reference(reference)
        if case
        in {
            GoldenCase.CUSTODY_ASSUMED,
            GoldenCase.NATIVE_ASSUMED,
            GoldenCase.NONCANONICAL_ASSUMED,
        }
        else reference
    )
    if case is GoldenCase.NONCANONICAL_ASSUMED:
        evidence = replace(
            assumed_reference(reference),
            curve=AssumedCurveSnapshot(
                content=json.dumps(json.loads(reference.curve.content), indent=1)
            ),
        )
    feed: NativeFeedCustody | NativeCaptureFeed = custody
    if case in {GoldenCase.NATIVE_VERIFIED, GoldenCase.NATIVE_ASSUMED}:
        feed = NativeCaptureFeed(
            capture=NativeCapture(
                id=CaptureId(UUID(int=7)),
                source_version=CapturedSourceVersion(
                    observation_id=obs.id,
                    station_id=obs.station_id,
                    tenant_id=custody.tenant_id,
                    measurement=measurement_snapshot(obs),
                    provider=CanonicalProviderIdentity.parse(
                        custody.endpoint, custody.api_station_id
                    ),
                    level_reference=CapturedLevelReference.GAUGE_ZERO,
                ),
            )
        )
    return derive_rating_sample(
        obs,
        curve,
        file_id=UUID(int=8),
        file_sha256="a" * 64,
        delivered_on="2026-09-08",
        feed=feed,
        reference=evidence,
        at=NOW,
    )


class TestFrozenRatingBytes:
    @pytest.mark.parametrize("case", list(GoldenCase))
    def test_pre_optimization_content_and_fingerprint(self, case: GoldenCase) -> None:
        fixture = Path(__file__).parents[2] / "fixtures/rating_derivation_bytes.json"
        golden = json.loads(fixture.read_text())["cases"][case]
        result = golden_derivation(case)
        assert result.content == golden["content"]
        assert result.fingerprint == golden["fingerprint"]
