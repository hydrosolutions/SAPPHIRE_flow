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
from scripts.dhm_precip.observations import (
    iter_observations_by_station,
    observations_by_station,
    station_id_for,
)


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


class TestIterObservationsByStation:
    def _frame(self, station_names: list[str]) -> pl.DataFrame:
        rows = [
            {
                "source_row_index": i,
                "station": name,
                "timestamp": datetime(2024, 6, 1, 0),
                "value_mm": 1.0,
            }
            for i, name in enumerate(station_names)
        ]
        return _normalised_frame(rows)

    def test_matches_the_eager_dict_built_from_the_same_frame(self) -> None:
        # D7 — streaming must not change the result, only when memory for
        # each station's Observation list is held.
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
                "timestamp": datetime(2024, 6, 1, 1),
                "value_mm": None,
            },
            {
                "source_row_index": 1,
                "station": "A",
                "timestamp": datetime(2024, 6, 1, 2),
                "value_mm": 3.0,
            },
        ]
        frame = _normalised_frame(rows)
        created_at = datetime(2026, 1, 1, tzinfo=UTC)

        streamed = dict(
            iter_observations_by_station(
                frame, parameter="precipitation", created_at=created_at
            )
        )
        eager = observations_by_station(
            frame, parameter="precipitation", created_at=created_at
        )

        assert streamed.keys() == eager.keys()
        for station in streamed:
            assert streamed[station] == eager[station]

    def test_returns_a_lazy_generator_not_a_materialised_collection(self) -> None:
        # A `list`/`dict` return would defeat D7's whole point (all
        # stations' `Observation` objects built before the caller can start
        # discarding any of them). Calling the function must return an
        # object that has not yet iterated the frame — proven here by
        # `inspect.isgenerator`.
        import inspect

        frame = self._frame(["A", "B", "C"])
        result = iter_observations_by_station(
            frame,
            parameter="precipitation",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert inspect.isgenerator(result)

    def test_only_the_requested_station_is_built_before_the_next_next_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # D7's actual memory claim: consuming ONE item from the generator
        # must build ONLY that one station's Observation list — not every
        # station's list up front. Spies on the per-group builder to prove
        # it is called incrementally, not all at once before the first
        # yield.
        import scripts.dhm_precip.observations as observations_module

        calls: list[str] = []
        original = observations_module._observations_for_group

        def _spy(station: Station, group: pl.DataFrame, **kwargs: object) -> list:
            calls.append(str(station))
            return original(station, group, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(observations_module, "_observations_for_group", _spy)

        frame = self._frame(["A", "B", "C"])
        generator = iter_observations_by_station(
            frame,
            parameter="precipitation",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        first_station, _first_obs = next(generator)
        assert calls == [str(first_station)]  # NOT all three stations yet

        remaining = dict(generator)
        assert calls == ["A", "B", "C"]
        assert set(remaining.keys()) | {first_station} == {
            Station("A"),
            Station("B"),
            Station("C"),
        }


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
