from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from sapphire_flow.types.bafu_capture import (
    BafuDischargeCapture,
    DischargeCaptureFailure,
    NormalizedDischargeCapture,
    QuarantinedDischargeCapture,
)
from sapphire_flow.types.bafu_capture_batch import (
    BafuCaptureBatchId,
    BafuStationCaptureBatch,
)
from sapphire_flow.types.bafu_unit_registry import (
    BafuDischargeKey,
    BafuGaugeCode,
    BafuUnitRegistry,
    BafuUnitRule,
    ResolvedBafuUnit,
    UnitRefreshState,
    UnitResolutionReason,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.observation import RawObservation
from sapphire_flow.types.observation_units import (
    DischargeSourceUnit,
    SourceDischargeValue,
)


@pytest.fixture
def captured_batch() -> BafuStationCaptureBatch:
    key = BafuDischargeKey(gauge=BafuGaugeCode(value="2206"), kind="river")
    timestamp = ensure_utc(datetime(2026, 10, 21, tzinfo=UTC))
    return BafuStationCaptureBatch(
        batch_id=BafuCaptureBatchId(UUID(int=1)),
        tenant_id=TenantId(UUID(int=2)),
        station_id=StationId(UUID(int=3)),
        key=key,
        received_at=timestamp,
        registry=BafuUnitRegistry(version="synthetic-v1", rules=()),
        observations=(),
        captures=(
            BafuDischargeCapture(
                key=key,
                source_literals=("22",),
                measurement_literals=(timestamp.isoformat(),),
                payload_sha256="b" * 64,
                received_at=timestamp,
                result=QuarantinedDischargeCapture(
                    measurement_time=timestamp, reason=UnitResolutionReason.MISSING
                ),
            ),
        ),
    )


class TestBafuStationCaptureBatch:
    def test_retry_preserves_digest_and_id(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        retry = replace(captured_batch)
        assert retry.input_digest == captured_batch.input_digest
        assert retry.batch_id == captured_batch.batch_id
        assert len(retry.input_digest) == 64

    def test_new_batch_id_does_not_change_input_digest(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        assert (
            replace(
                captured_batch, batch_id=BafuCaptureBatchId(UUID(int=9))
            ).input_digest
            == captured_batch.input_digest
        )

    def test_changed_literal_changes_digest(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        changed = replace(captured_batch.captures[0], source_literals=("23",))
        assert (
            replace(captured_batch, captures=(changed,)).input_digest
            != captured_batch.input_digest
        )

    def test_tenant_and_station_binding_participate_in_digest(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        assert (
            replace(captured_batch, tenant_id=TenantId(UUID(int=8))).input_digest
            != captured_batch.input_digest
        )
        assert (
            replace(captured_batch, station_id=StationId(UUID(int=8))).input_digest
            != captured_batch.input_digest
        )

    def test_rejects_cross_station_observation(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        obs = RawObservation(
            station_id=StationId(UUID(int=99)),
            timestamp=captured_batch.received_at,
            parameter="water_level",
            value=2.0,
            source=ObservationSource.MEASURED,
        )
        with pytest.raises(ValueError, match="station binding"):
            replace(captured_batch, observations=(obs,))

    def test_rejects_canonical_discharge_without_matching_capture(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        obs = RawObservation(
            station_id=captured_batch.station_id,
            timestamp=captured_batch.received_at,
            parameter="discharge",
            value=22.0,
            source=ObservationSource.MEASURED,
        )
        with pytest.raises(ValueError, match="normalized capture"):
            replace(captured_batch, observations=(obs,))

    def test_rejects_ambiguous_unordered_observations(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        obs = RawObservation(
            station_id=captured_batch.station_id,
            timestamp=captured_batch.received_at,
            parameter="water_level",
            value=2.0,
            source=ObservationSource.MEASURED,
        )
        with pytest.raises(ValueError, match="conflicting observations"):
            replace(captured_batch, observations=(obs, replace(obs, value=3.0)))

    def test_equivalent_duplicate_observations_collapse(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        obs = RawObservation(
            station_id=captured_batch.station_id,
            timestamp=captured_batch.received_at,
            parameter="water_level",
            value=2.0,
            source=ObservationSource.MEASURED,
        )
        single = replace(captured_batch, observations=(obs,))
        repeated = replace(captured_batch, observations=(obs, obs))
        assert repeated.observations == single.observations
        assert repeated.input_digest == single.input_digest

    def test_rejects_wrong_source_scope(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        obs = RawObservation(
            station_id=captured_batch.station_id,
            timestamp=captured_batch.received_at,
            parameter="water_level",
            value=2.0,
            source=ObservationSource.MANUAL_IMPORT,
        )
        with pytest.raises(ValueError, match="source-native measured"):
            replace(captured_batch, observations=(obs,))

    def test_observation_order_does_not_change_digest(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        level = RawObservation(
            station_id=captured_batch.station_id,
            timestamp=captured_batch.received_at,
            parameter="water_level",
            value=2.0,
            source=ObservationSource.MEASURED,
        )
        temperature = replace(level, parameter="water_temperature", value=12.0)
        assert (
            replace(captured_batch, observations=(level, temperature)).input_digest
            == replace(captured_batch, observations=(temperature, level)).input_digest
        )

    def test_registry_change_changes_digest(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        assert (
            replace(
                captured_batch,
                registry=replace(captured_batch.registry, version="synthetic-v2"),
            ).input_digest
            != captured_batch.input_digest
        )

    def test_conflicting_captures_are_not_last_wins(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        changed = replace(captured_batch.captures[0], source_literals=("23",))
        with pytest.raises(ValueError, match="conflicting captures"):
            replace(captured_batch, captures=(captured_batch.captures[0], changed))

    def test_equivalent_duplicate_captures_collapse(
        self, captured_batch: BafuStationCaptureBatch
    ) -> None:
        duplicate = replace(captured_batch, captures=captured_batch.captures * 2)
        assert duplicate.input_digest == captured_batch.input_digest
        assert duplicate.captures == captured_batch.captures

    @pytest.mark.parametrize("persisted_value", [0.022, 0.000022])
    def test_canonical_observation_must_match_conversion_exactly(
        self, captured_batch: BafuStationCaptureBatch, persisted_value: float
    ) -> None:
        rule = BafuUnitRule(
            rule_id="synthetic",
            key=captured_batch.key,
            unit=DischargeSourceUnit.LITRES_PER_SECOND,
            valid_from=captured_batch.received_at,
            valid_until=None,
            verified_at=captured_batch.received_at,
            refresh_due=captured_batch.received_at,
            evidence_reference="synthetic",
            evidence_sha256="c" * 64,
        )
        normalized = NormalizedDischargeCapture(
            measurement_time=captured_batch.received_at,
            source_value=SourceDischargeValue(value=22.0),
            resolution=ResolvedBafuUnit(rule=rule, refresh=UnitRefreshState.DUE),
        )
        capture = replace(captured_batch.captures[0], result=normalized)
        obs = RawObservation(
            station_id=captured_batch.station_id,
            timestamp=captured_batch.received_at,
            parameter="discharge",
            value=persisted_value,
            source=ObservationSource.MEASURED,
        )
        if persisted_value == 0.022:
            batch = replace(
                captured_batch,
                registry=BafuUnitRegistry(version="test", rules=(rule,)),
                captures=(capture,),
                observations=(obs,),
            )
            assert batch.observations[0].value == normalized.canonical_value.value
        else:
            with pytest.raises(ValueError, match="normalized capture"):
                replace(
                    captured_batch,
                    registry=BafuUnitRegistry(version="test", rules=(rule,)),
                    captures=(capture,),
                    observations=(obs,),
                )


class TestQuarantineTimeBinding:
    @pytest.mark.parametrize(
        "literals",
        [
            (),
            ("bad",),
            ("2026-10-21T01:00:00+00:00",),
            ("2026-10-21T00:00:00+00:00", "2026-10-21T01:00:00+00:00"),
        ],
    )
    def test_rejects_unproven_nonnull_time(
        self, captured_batch: BafuStationCaptureBatch, literals: tuple[str, ...]
    ) -> None:
        with pytest.raises(ValueError, match="contradicts measurement literals"):
            capture = replace(captured_batch.captures[0], measurement_literals=literals)
            replace(captured_batch, captures=(capture,))

    @pytest.mark.parametrize(
        "literals",
        [(), ("bad",), ("2026-10-21T00:00:00+00:00", "2026-10-21T01:00:00+00:00")],
    )
    def test_null_malformed_capture_remains_representable(
        self, captured_batch: BafuStationCaptureBatch, literals: tuple[str, ...]
    ) -> None:
        capture = replace(
            captured_batch.captures[0],
            measurement_literals=literals,
            result=QuarantinedDischargeCapture(
                measurement_time=None, reason=DischargeCaptureFailure.SUBJECT_MALFORMED
            ),
        )
        candidate = replace(captured_batch, captures=(capture,))
        assert candidate.captures[0].result.measurement_time is None


class TestNullTimeReasonConsistency:
    @pytest.mark.parametrize(
        "reason",
        [
            DischargeCaptureFailure.MISSING_TIME,
            DischargeCaptureFailure.INVALID_TIME,
            DischargeCaptureFailure.CONFLICTING_TIMES,
        ],
    )
    @pytest.mark.parametrize(
        "literals",
        [
            (),
            ("bad",),
            ("2026-10-21T00:00:00Z", "2026-10-22T00:00:00Z"),
            ("2026-10-21T00:00:00Z",),
        ],
    )
    def test_reason_matches_retained_time_parser(
        self,
        captured_batch: BafuStationCaptureBatch,
        reason: DischargeCaptureFailure,
        literals: tuple[str, ...],
    ) -> None:
        from sapphire_flow.types.bafu_capture import parse_bafu_measurement_time

        original = captured_batch.captures[0]
        result = QuarantinedDischargeCapture(measurement_time=None, reason=reason)
        if parse_bafu_measurement_time(literals) == reason:
            assert (
                replace(original, measurement_literals=literals, result=result).result
                == result
            )
        else:
            with pytest.raises(ValueError, match="time-failure reason contradicts"):
                replace(original, measurement_literals=literals, result=result)

    def test_subject_malformed_can_retain_parseable_time(
        self,
        captured_batch: BafuStationCaptureBatch,
    ) -> None:
        original = captured_batch.captures[0]
        result = QuarantinedDischargeCapture(
            measurement_time=None, reason=DischargeCaptureFailure.SUBJECT_MALFORMED
        )
        assert replace(original, result=result).result.measurement_time is None
