from dataclasses import replace
from uuid import uuid4

import pytest

from sapphire_flow.services.operational_weather_proof import (
    WeatherProofError,
    prove_future_receipt,
    verify_future_receipt,
)
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    FutureWeatherRequirement,
    FutureWeatherScope,
)
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.fakes.operational_weather import ISSUE


def proof_rows() -> tuple[FutureWeatherScope, list[WeatherForecastRecord]]:
    sid = StationId(uuid4())
    scope = FutureWeatherScope(
        station_id=sid,
        source="ifs",
        binding_fingerprint="a" * 64,
        issue_time=ISSUE,
        requirements=(
            FutureWeatherRequirement(
                parameter="precipitation", valid_times=(ISSUE,), member_ids=(0, 1)
            ),
        ),
    )
    rows = [
        WeatherForecastRecord(
            id=uuid4(),
            station_id=sid,
            nwp_source="ifs",
            cycle_time=ISSUE,
            valid_time=ISSUE,
            parameter="precipitation",
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=member,
            value=1.0 + member,
            created_at=ISSUE,
        )
        for member in (0, 1)
    ]
    return scope, rows


class TestFutureWeatherProof:
    def test_equal_values_ignore_generated_ids_and_row_order(self) -> None:
        scope, rows = proof_rows()
        readback = [replace(row, id=uuid4()) for row in reversed(rows)]
        receipt = prove_future_receipt(
            scope=scope,
            cycle_time=ISSUE,
            validated_at=ISSUE,
            fetched=rows,
            readback=readback,
        )
        verify_future_receipt(receipt, rows)
        assert receipt.row_count == 2

    def test_conflict_ignored_old_values_are_not_certified(self) -> None:
        scope, rows = proof_rows()
        with pytest.raises(WeatherProofError, match="differs from fetched"):
            prove_future_receipt(
                scope=scope,
                cycle_time=ISSUE,
                validated_at=ISSUE,
                fetched=rows,
                readback=[replace(rows[0], value=9.0), rows[1]],
            )

    def test_receipt_verification_checks_all_original_members(self) -> None:
        scope, rows = proof_rows()
        receipt = prove_future_receipt(
            scope=scope,
            cycle_time=ISSUE,
            validated_at=ISSUE,
            fetched=rows,
            readback=rows,
        )
        with pytest.raises(WeatherProofError, match="complete receipt"):
            verify_future_receipt(receipt, [rows[0], replace(rows[1], value=9.0)])

    def test_missing_required_row_is_not_certified(self) -> None:
        scope, rows = proof_rows()
        with pytest.raises(WeatherProofError, match="complete receipt scope"):
            prove_future_receipt(
                scope=scope,
                cycle_time=ISSUE,
                validated_at=ISSUE,
                fetched=rows,
                readback=rows[:1],
            )

    def test_duplicate_identity_is_not_certified(self) -> None:
        scope, rows = proof_rows()
        with pytest.raises(WeatherProofError, match="duplicate required"):
            prove_future_receipt(
                scope=scope,
                cycle_time=ISSUE,
                validated_at=ISSUE,
                fetched=rows,
                readback=rows + rows[:1],
            )

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
    def test_nonphysical_value_representation_is_not_certified(
        self, value: float
    ) -> None:
        scope, rows = proof_rows()
        with pytest.raises(WeatherProofError, match="unsupported value"):
            prove_future_receipt(
                scope=scope,
                cycle_time=ISSUE,
                validated_at=ISSUE,
                fetched=rows,
                readback=[replace(rows[0], value=value), rows[1]],
            )
