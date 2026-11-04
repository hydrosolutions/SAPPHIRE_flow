from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.services.rating_derived import derive_rating_sample
from sapphire_flow.services.rating_inputs import merge_rating_days
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import InterpolationMethod, ObservationSource, QcStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import (
    ObservationId,
    RatingCurveId,
    RatingReferenceProofId,
    StationId,
    TenantId,
)
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_derived import NativeFeedCustody, RatingDerivedDischarge
from sapphire_flow.types.rating_reference import RatingReferenceProof, curve_snapshot

NOW = ensure_utc(datetime(2026, 10, 1, tzinfo=UTC))
CREATED = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


def legacy_parts() -> tuple[
    Observation, RatingCurve, NativeFeedCustody, RatingReferenceProof
]:
    station = StationId(UUID(int=2))
    tenant = TenantId(UUID(int=5))
    observation = Observation(
        id=ObservationId(UUID(int=1)),
        station_id=station,
        timestamp=NOW,
        parameter="water_level",
        value=1.5,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.QC_PASSED,
        qc_flags=[
            QcFlag(rule_id="range_check", rule_version="1", status=QcStatus.QC_PASSED)
        ],
        qc_rule_version="level-v1",
        created_at=CREATED,
    )
    curve = RatingCurve(
        id=RatingCurveId(UUID(int=3)),
        station_id=station,
        version=1,
        valid_from=CREATED,
        valid_to=ensure_utc(datetime(2025, 6, 1, tzinfo=UTC)),
        points=[
            {"water_level": 1.0, "discharge": 10.0},
            {"water_level": 2.0, "discharge": 20.0},
        ],
        interpolation=InterpolationMethod.LINEAR,
        uploaded_by=None,
        created_at=CREATED,
    )
    custody = NativeFeedCustody(
        tenant_id=tenant,
        station_id=station,
        endpoint="https://example.invalid/levels/",
        api_station_id=1,
        evidence_reference="fixture native custody",
        start=NOW - timedelta(days=1),
        end=NOW + timedelta(days=2),
    )
    reference = RatingReferenceProof(
        id=RatingReferenceProofId(UUID(int=4)),
        tenant_id=tenant,
        station_id=station,
        endpoint=custody.endpoint,
        api_station_id=1,
        evidence_reference="fixture reference",
        verified_by="fixture reviewer",
        verified_at=NOW,
        rating_curve_id=curve.id,
        curve=curve_snapshot(curve),
        level_unit="m",
        curve_unit="m",
        level_reference="gauge_zero",
        curve_reference="gauge_zero",
        offset_m=0.0,
    )
    return observation, curve, custody, reference


def legacy_sample_and_lineage() -> tuple[RatingDerivedDischarge, ForecastInputLineage]:
    observation, curve, custody, reference = legacy_parts()
    sample = derive_rating_sample(
        observation,
        curve,
        file_id=UUID(int=6),
        file_sha256="a" * 64,
        delivered_on="2026-09-08",
        feed=custody,
        reference=reference,
        at=NOW,
    )
    _, lineage, _ = merge_rating_days(
        pl.DataFrame(),
        (sample,),
        issue_time=NOW + timedelta(days=1),
        weather_cycle_time=NOW,
    )
    assert lineage is not None
    return sample, lineage


class TestNativeRatingCompatibility:
    def test_v1_bytes_match_pre_capture_baseline(self) -> None:
        golden = json.loads(
            (
                Path(__file__).parents[2] / "fixtures/native_rating_v1_golden.json"
            ).read_text()
        )
        sample, lineage = legacy_sample_and_lineage()
        assert sample.content == golden["sample_content"]
        assert lineage.content == golden["lineage_content"]

    @pytest.mark.parametrize("evidence_mix", ["native", "mixed"])
    def test_policy_labels_preserve_numerical_daily_mean(
        self, evidence_mix: Literal["native", "mixed"]
    ) -> None:
        from sapphire_flow.types.native_capture import (
            CanonicalProviderIdentity,
            CapturedLevelReference,
            CapturedSourceVersion,
            CaptureId,
            NativeCapture,
        )
        from sapphire_flow.types.rating_derived import NativeCaptureFeed
        from sapphire_flow.types.rating_reference import measurement_snapshot

        legacy, _ = legacy_sample_and_lineage()
        observation, curve, custody, reference = legacy_parts()
        observation = replace(
            observation,
            id=ObservationId(UUID(int=11)),
            timestamp=NOW + timedelta(hours=1),
        )
        capture = NativeCapture(
            id=CaptureId(UUID(int=12)),
            source_version=CapturedSourceVersion(
                tenant_id=custody.tenant_id,
                station_id=observation.station_id,
                observation_id=observation.id,
                measurement=measurement_snapshot(observation),
                provider=CanonicalProviderIdentity.parse(custody.endpoint, 1),
                level_reference=CapturedLevelReference.GAUGE_ZERO,
            ),
        )
        native = derive_rating_sample(
            observation,
            curve,
            file_id=UUID(int=6),
            file_sha256="a" * 64,
            delivered_on="2026-09-08",
            feed=NativeCaptureFeed(capture=capture),
            reference=reference,
            at=NOW + timedelta(days=1),
        )
        frame, lineage, _ = merge_rating_days(
            pl.DataFrame(),
            (legacy, native) if evidence_mix == "mixed" else (native,),
            issue_time=NOW + timedelta(days=1),
            weather_cycle_time=NOW,
        )
        assert frame["discharge"].to_list() == [15.0]
        assert lineage is not None
        assert lineage.transformation_versions == (
            ("native-rating-linear-endpoints-v1", "native-rating-linear-endpoints-v2")
            if evidence_mix == "mixed"
            else ("native-rating-linear-endpoints-v2",)
        )
        assert ForecastInputLineage.from_content(lineage.content) == lineage

    def test_omissions_only_add_explanations_to_legacy_lineage(self) -> None:
        from sapphire_flow.types.native_capture import Sha256Digest
        from sapphire_flow.types.rating_input_lineage import RatingUnreconciledSource
        from sapphire_flow.types.rating_reference import canonical_content

        sample, baseline = legacy_sample_and_lineage()
        omitted = RatingUnreconciledSource(
            observation_id=ObservationId(UUID(int=7)),
            timestamp=NOW + timedelta(hours=2),
            source_snapshot_sha256=Sha256Digest(value="b" * 64),
        )
        frame, lineage, _ = merge_rating_days(
            pl.DataFrame(),
            (sample,),
            issue_time=NOW + timedelta(days=1),
            weather_cycle_time=NOW,
            unreconciled_sources=(omitted,),
        )
        expected = json.loads(baseline.content)
        expected["rating_input"]["unreconciled_sources"] = [
            {
                "observation_id": str(omitted.observation_id),
                "timestamp": omitted.timestamp.isoformat(),
                "source_snapshot_sha256": "b" * 64,
            }
        ]
        expected["rating_input"]["days"][0]["warnings"] = sorted(
            [
                *expected["rating_input"]["days"][0]["warnings"],
                "rating_unreconciled_sources_omitted",
            ]
        )
        assert frame["discharge"].to_list() == [15.0]
        assert lineage is not None
        assert lineage.content == canonical_content(expected)
        assert ForecastInputLineage.from_content(lineage.content) == lineage
