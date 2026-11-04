from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from sapphire_flow.services.rating_derived import (
    derive_rating_sample,
    select_eligible_rating,
)
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.rating_derived import NativeFeedCustody
from tests.unit.services.test_provisional_discharge import NOW, inputs


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
