from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from sapphire_flow.adapters.bafu_discharge_capture import capture_bafu_discharge
from sapphire_flow.config.bafu_units import parse_bafu_unit_registry
from sapphire_flow.types.bafu_capture import (
    DischargeCaptureFailure,
    NormalizedDischargeCapture,
    QuarantinedDischargeCapture,
)
from sapphire_flow.types.bafu_unit_registry import (
    BafuDischargeKey,
    BafuGaugeCode,
    UnitResolutionReason,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.observation import (
    HydroScraperBatchResult,
    RawObservation,
    StationFetchOutcome,
)

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_unit_registry import BafuUnitRegistry

from dataclasses import replace
from datetime import UTC, datetime


@pytest.fixture
def unit_registry() -> BafuUnitRegistry:
    return parse_bafu_unit_registry(
        {
            "schema_version": 1,
            "source": "lindas_foen_hydro",
            "parameter": "discharge",
            "version": "synthetic-v1",
            "rules": [
                {
                    "rule_id": "synthetic",
                    "gauge_code": "2206",
                    "kind": "river",
                    "unit": "l/s",
                    "valid_from": "2026-10-20T00:00:00Z",
                    "verified_at": "2026-10-20T00:00:00Z",
                    "refresh_due": "2026-10-30T00:00:00Z",
                    "evidence_reference": "synthetic",
                    "evidence_sha256": "a" * 64,
                }
            ],
        }
    )


class TestCaptureBafuDischarge:
    @pytest.mark.parametrize("literals", [("22",), ("22", "22.000", "2.2e1")])
    def test_normalizes_once_and_retains_all_original_literals(
        self, unit_registry: BafuUnitRegistry, literals: tuple[str, ...]
    ) -> None:
        captured = capture_bafu_discharge(
            key=BafuDischargeKey(gauge=BafuGaugeCode(value="2206"), kind="river"),
            literals=literals,
            measurement_times=("2026-10-21T00:00:00Z",),
            payload_sha256="b" * 64,
            registry=unit_registry,
            now=ensure_utc(datetime(2026, 10, 22, tzinfo=UTC)),
        )
        assert captured.source_literals == literals
        assert isinstance(captured.result, NormalizedDischargeCapture)
        assert captured.result.canonical_value.value == 0.022
        assert captured.result.source_value.value == 22.0
        assert captured.payload_sha256 == "b" * 64

    @pytest.mark.parametrize(
        ("literals", "times", "reason"),
        [
            (
                ("22", "23"),
                ("2026-10-21T00:00:00Z",),
                DischargeCaptureFailure.CONFLICTING_LITERALS,
            ),
            (
                ("NaN",),
                ("2026-10-21T00:00:00Z",),
                DischargeCaptureFailure.INVALID_LITERAL,
            ),
            (("22",), (), DischargeCaptureFailure.MISSING_TIME),
            (("22",), ("invalid",), DischargeCaptureFailure.INVALID_TIME),
            (("22",), ("2026-10-21T00:00:00",), DischargeCaptureFailure.INVALID_TIME),
            (
                ("22",),
                ("2026-10-21T00:00:00Z", "2026-10-22T00:00:00Z"),
                DischargeCaptureFailure.CONFLICTING_TIMES,
            ),
        ],
    )
    def test_quarantines_ambiguous_input_without_losing_originals(
        self,
        unit_registry: BafuUnitRegistry,
        literals: tuple[str, ...],
        times: tuple[str, ...],
        reason: DischargeCaptureFailure,
    ) -> None:
        captured = capture_bafu_discharge(
            key=BafuDischargeKey(gauge=BafuGaugeCode(value="2206"), kind="river"),
            literals=literals,
            measurement_times=times,
            payload_sha256="b" * 64,
            registry=unit_registry,
            now=ensure_utc(datetime(2026, 10, 22, tzinfo=UTC)),
        )
        assert isinstance(captured.result, QuarantinedDischargeCapture)
        assert captured.result.reason is reason
        assert captured.source_literals == literals
        assert captured.measurement_literals == times
        if reason in (
            DischargeCaptureFailure.MISSING_TIME,
            DischargeCaptureFailure.INVALID_TIME,
            DischargeCaptureFailure.CONFLICTING_TIMES,
        ):
            assert captured.result.measurement_time is None

    def test_equivalent_timestamp_spellings_are_not_a_conflict(
        self, unit_registry: BafuUnitRegistry
    ) -> None:
        captured = capture_bafu_discharge(
            key=BafuDischargeKey(gauge=BafuGaugeCode(value="2206"), kind="river"),
            literals=("22",),
            measurement_times=("2026-10-21T00:00:00Z", "2026-10-21T01:00:00+01:00"),
            payload_sha256="b" * 64,
            registry=unit_registry,
            now=ensure_utc(datetime(2026, 10, 22, tzinfo=UTC)),
        )
        assert isinstance(captured.result, NormalizedDischargeCapture)

    def test_unknown_unit_is_quarantined_not_assumed_canonical(
        self, unit_registry: BafuUnitRegistry
    ) -> None:
        captured = capture_bafu_discharge(
            key=BafuDischargeKey(gauge=BafuGaugeCode(value="2044"), kind="river"),
            literals=("22",),
            measurement_times=("2026-10-21T00:00:00Z",),
            payload_sha256="b" * 64,
            registry=unit_registry,
            now=ensure_utc(datetime(2026, 10, 22, tzinfo=UTC)),
        )
        assert isinstance(captured.result, QuarantinedDischargeCapture)
        assert captured.result.reason is UnitResolutionReason.MISSING


class TestCapturedStationOutcomes:
    def test_quarantine_survives_an_empty_observation_result(
        self, unit_registry: BafuUnitRegistry
    ) -> None:
        captured = capture_bafu_discharge(
            key=BafuDischargeKey(gauge=BafuGaugeCode(value="2044"), kind="river"),
            literals=("22",),
            measurement_times=("2026-10-21T00:00:00Z",),
            payload_sha256="b" * 64,
            registry=unit_registry,
            now=ensure_utc(datetime(2026, 10, 22, tzinfo=UTC)),
        )
        outcome = StationFetchOutcome(
            station_id=StationId(UUID(int=1)),
            observations=(),
            failure_cause=None,
            failure_detail=None,
            source_captures=(captured,),
        )
        batch = HydroScraperBatchResult(outcomes=(outcome,))
        assert batch.observations == []
        assert batch.captured_outcomes == (outcome,)
        assert outcome.parameter_diagnostics[0].reason is UnitResolutionReason.MISSING

    def test_unit_diagnostic_does_not_discard_usable_water_level(
        self, unit_registry: BafuUnitRegistry
    ) -> None:
        captured = capture_bafu_discharge(
            key=BafuDischargeKey(gauge=BafuGaugeCode(value="2044"), kind="river"),
            literals=("22",),
            measurement_times=("2026-10-21T00:00:00Z",),
            payload_sha256="b" * 64,
            registry=unit_registry,
            now=ensure_utc(datetime(2026, 10, 22, tzinfo=UTC)),
        )
        water_level = RawObservation(
            station_id=StationId(UUID(int=1)),
            timestamp=ensure_utc(datetime(2026, 10, 21, tzinfo=UTC)),
            parameter="water_level",
            value=1.0,
            source=ObservationSource.MEASURED,
        )
        outcome = StationFetchOutcome(
            station_id=water_level.station_id,
            observations=(water_level,),
            failure_cause=None,
            failure_detail=None,
            source_captures=(captured,),
        )
        assert outcome.observations == (water_level,)
        assert outcome.parameter_diagnostics[0].parameter == "discharge"

    def test_existing_adapters_need_no_capture_arguments(self) -> None:
        outcome = StationFetchOutcome(
            station_id=StationId(UUID(int=1)),
            observations=(),
            failure_cause=None,
            failure_detail=None,
        )
        assert outcome.parameter_diagnostics == ()
        assert HydroScraperBatchResult(outcomes=(outcome,)).captured_outcomes == ()


class TestNormalizedCaptureEvidence:
    @pytest.mark.parametrize(
        ("field", "originals"),
        [
            ("source_literals", ("23",)),
            ("source_literals", ("22", "23")),
            ("source_literals", ("NaN",)),
            ("measurement_literals", ()),
            ("measurement_literals", ("2026-10-22T00:00:00Z",)),
            ("measurement_literals", ("2026-10-21T00:00:00Z", "2026-10-22T00:00:00Z")),
        ],
    )
    def test_rejects_contradictory_normalized_evidence(
        self,
        unit_registry: BafuUnitRegistry,
        field: str,
        originals: tuple[str, ...],
    ) -> None:
        capture = capture_bafu_discharge(
            key=BafuDischargeKey(gauge=BafuGaugeCode(value="2206"), kind="river"),
            literals=("22",),
            measurement_times=("2026-10-21T00:00:00Z",),
            payload_sha256="b" * 64,
            registry=unit_registry,
            now=ensure_utc(datetime(2026, 10, 22, tzinfo=UTC)),
        )
        with pytest.raises(
            ValueError, match="normalized capture|invalid discharge literal"
        ):
            replace(capture, **{field: originals})
