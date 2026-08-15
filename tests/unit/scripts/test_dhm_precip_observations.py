"""Task 1b (Plan 173, M-A3) — normalised frame -> `Observation` objects (D2, D7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import polars as pl
import pytest

from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.observations import observations_by_station, station_id_for


def _normalised_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


class TestObservationsByStation:
    def test_a_null_row_becomes_missing_with_none_value(self) -> None:
        rows = [
            {
                "source_row_index": None,
                "station": "A",
                "timestamp": datetime(2024, 6, 1, 1),
                "value_mm": None,
            },
        ]
        result = observations_by_station(
            _normalised_frame(rows),
            parameter="precipitation",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        (obs,) = result[Station("A")]
        assert obs.qc_status == QcStatus.MISSING
        assert obs.value is None

    def test_a_delivered_row_becomes_raw_with_its_value(self) -> None:
        rows = [
            {
                "source_row_index": 3,
                "station": "A",
                "timestamp": datetime(2024, 6, 1, 0),
                "value_mm": 4.2,
            },
        ]
        result = observations_by_station(
            _normalised_frame(rows),
            parameter="precipitation",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        (obs,) = result[Station("A")]
        assert obs.qc_status == QcStatus.RAW
        assert obs.value == pytest.approx(4.2)

    def test_round_trip_count_and_values_are_preserved(self) -> None:
        values: list[float | None] = [1.0, None, 3.0, None, 0.0, 5.5]
        rows = [
            {
                "source_row_index": i if v is not None else None,
                "station": "A",
                "timestamp": datetime(2024, 6, 1, i),
                "value_mm": v,
            }
            for i, v in enumerate(values)
        ]
        result = observations_by_station(
            _normalised_frame(rows),
            parameter="precipitation",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        observations = sorted(result[Station("A")], key=lambda o: o.timestamp)
        assert len(observations) == len(values)
        assert [o.value for o in observations] == values

    def test_multiple_stations_are_chunked_separately(self) -> None:
        rows = [
            {
                "source_row_index": 0,
                "station": "A",
                "timestamp": datetime(2024, 6, 1, 0),
                "value_mm": 1.0,
            },
            {
                "source_row_index": 0,
                "station": "B",
                "timestamp": datetime(2024, 6, 1, 0),
                "value_mm": 2.0,
            },
        ]
        result = observations_by_station(
            _normalised_frame(rows),
            parameter="precipitation",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert set(result.keys()) == {Station("A"), Station("B")}
        assert len(result[Station("A")]) == 1
        assert len(result[Station("B")]) == 1

    def test_station_id_is_deterministic_across_calls(self) -> None:
        assert station_id_for(Station("Aiselukhark")) == station_id_for(
            Station("Aiselukhark")
        )
        assert station_id_for(Station("Aiselukhark")) != station_id_for(
            Station("Sindhuli Madhi")
        )


class TestConstructingAGapRowAsRawInvariant:
    def test_constructing_a_null_row_as_raw_raises(self) -> None:
        # D2's locked invariant (Observation.__post_init__): a null value
        # can only be paired with qc_status=MISSING. Get this wrong and
        # construction raises on the first gap — of which there are 568 x 26.
        with pytest.raises(ValueError, match="MISSING"):
            Observation(
                id=ObservationId(uuid.uuid4()),
                station_id=StationId(uuid.uuid4()),
                timestamp=datetime(2024, 6, 1, tzinfo=UTC),
                parameter="precipitation",
                value=None,
                source=ObservationSource.MANUAL_IMPORT,
                rating_curve_id=None,
                rating_curve_correction_version=None,
                qc_status=QcStatus.RAW,
                qc_flags=[],
                qc_rule_version=None,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )


if __name__ == "__main__":
    pytest.main([__file__])
