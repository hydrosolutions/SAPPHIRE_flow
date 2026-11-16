from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.services.station_history import build_station_history
from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station_history import (
    HistoryCurve,
    HistoryOutcome,
    HistoryResolution,
    HistorySample,
    StationHistoryRequest,
)

NOW = datetime(2026, 11, 16, 12, 30, tzinfo=UTC)


def request(**changes: object) -> StationHistoryRequest:
    base = StationHistoryRequest(
        station_id=StationId(uuid4()),
        start=NOW - timedelta(hours=2),
        end=NOW,
        generated_at=NOW,
        resolution=HistoryResolution.HOURLY,
        purposes=(ForecastDataUse.STANDARD, ForecastDataUse.PROVISIONAL_RATING),
    )
    return replace(base, **changes)


def sample(
    value: float | None, timestamp: datetime, **changes: object
) -> HistorySample:
    base = HistorySample(
        timestamp=timestamp,
        source="measured",
        data_use=ForecastDataUse.STANDARD,
        value=value,
        outcome=HistoryOutcome.ACCEPTED if value is not None else HistoryOutcome.HELD,
        reason=None if value is not None else "unreconciled",
        source_qc_status=QcStatus.RAW,
        source_assessment="unassessed",
    )
    return replace(base, **changes)


class TestBuildStationHistory:
    def test_irregular_sample_mean_bounds_and_empty_hours(self) -> None:
        query = request()
        rows = (
            sample(2.0, query.start),
            sample(6.0, query.start + timedelta(minutes=1)),
        )
        response = build_station_history(query, rows)
        measured, supplied, derived = response.series
        first, middle, last = measured.points
        assert (first.value, first.minimum, first.maximum, first.sample_count) == (
            4.0,
            2.0,
            6.0,
            2,
        )
        assert first.timestamp == query.start
        assert first.end == query.start.replace(minute=0) + timedelta(hours=1)
        assert first.interval == "clipped_hour"
        assert middle.interval == "complete_hour" and middle.value is None
        assert last.interval == "unfinished_hour" and last.value is None
        assert measured.coverage == "unknown"
        assert supplied.availability == derived.availability == "no_source"

    def test_partial_support_is_distinct_from_bucket_clipping(self) -> None:
        query = request()
        rows = (
            sample(2.0, query.start),
            sample(None, query.start + timedelta(minutes=1)),
        )
        series = build_station_history(query, rows).series[0]
        assert series.availability == "available"
        assert series.completeness == series.points[0].completeness == "partial"
        assert series.points[0].counts.held == 1
        assert series.points[0].value == 2.0
        assert series.latest_timestamp == query.start

    def test_native_preserves_times_and_no_fake_empty_points(self) -> None:
        query = request(resolution=HistoryResolution.NATIVE)
        actual = query.start + timedelta(seconds=17)
        result = build_station_history(query, (sample(None, actual),))
        measured = result.series[0]
        assert (
            measured.availability == "no_eligible_result"
            and measured.latest_timestamp is None
        )
        assert measured.points[0].timestamp == actual
        assert measured.points[0].outcome is HistoryOutcome.HELD
        assert measured.points[0].source_qc_status is QcStatus.RAW
        assert measured.points[0].source_assessment == "unassessed"
        assert result.series[1].points == ()

    def test_derived_union_keeps_frozen_curves_warnings_and_separate_source(
        self,
    ) -> None:
        query = request()
        first = HistoryCurve(
            curve_id=str(uuid4()),
            content_sha256="a" * 64,
            version="1",
            valid_from=query.start - timedelta(days=30),
            valid_to=None,
        )
        second = replace(first, content_sha256="b" * 64, version="2")
        rows = tuple(
            sample(
                value,
                query.start + timedelta(minutes=i),
                source="rating_derived",
                data_use=ForecastDataUse.PROVISIONAL_RATING,
                curve=curve,
                warnings=(warning,),
                stored_at=NOW - timedelta(minutes=3),
                policy="stored-policy",
            )
            for i, (value, curve, warning) in enumerate(
                (
                    (2.0, first, "extrapolated_below"),
                    (8.0, second, "extrapolated_above"),
                )
            )
        )
        result = build_station_history(query, rows + (sample(100.0, query.start),))
        assert result.series[0].points[0].value == 100.0
        point = result.series[2].points[0]
        assert point.value == 5.0
        assert set(point.curve_refs) == set(result.curves) == {"a" * 64, "b" * 64}
        assert point.warnings == ("extrapolated_above", "extrapolated_below")
        assert point.source_qc_counts == {"raw": 2}

    @pytest.mark.parametrize(
        "changes",
        [
            {"end": NOW + timedelta(seconds=1)},
            {"start": NOW},
            {"start": NOW - timedelta(days=8)},
            {"purposes": (ForecastDataUse.STANDARD,) * 2},
        ],
    )
    def test_invalid_window_or_duplicate_purpose(self, changes: dict) -> None:
        with pytest.raises(ValueError, match="history"):
            request(**changes)
