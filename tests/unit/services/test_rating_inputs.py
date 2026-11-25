from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.rating_derived import derive_rating_sample
from sapphire_flow.services.rating_inputs import merge_rating_days
from sapphire_flow.types.enums import AggregationMethod, QcStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.rating_derived import NativeFeedCustody, RatingDerivedDischarge
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid
from tests.unit.services.test_provisional_discharge import NOW, inputs


def samples() -> tuple[RatingDerivedDischarge, ...]:
    obs, curve, feed, reference = inputs()
    custody = NativeFeedCustody(
        tenant_id=feed.tenant_id,
        station_id=feed.station_id,
        endpoint=feed.endpoint,
        api_station_id=feed.api_station_id,
        evidence_reference="fixture native custody",
        start=NOW - timedelta(days=1),
        end=NOW + timedelta(days=2),
    )
    return tuple(
        derive_rating_sample(
            replace(
                obs,
                id=uuid4(),
                timestamp=NOW + timedelta(hours=i),
                value=value,
                qc_status=status,
            ),
            curve,
            file_id=uuid4(),
            file_sha256="a" * 64,
            delivered_on="2026-09-08",
            feed=custody,
            reference=reference,
            at=NOW + timedelta(days=1),
        )
        for i, (value, status) in enumerate(
            [(1.0, QcStatus.QC_PASSED), (3.0, QcStatus.RAW), (2.0, QcStatus.QC_FAILED)]
        )
    )


class TestMergeRatingDays:
    def test_sample_first_mean_with_honest_rejections_and_roundtrip(self) -> None:
        frame, lineage, latest = merge_rating_days(
            pl.DataFrame(),
            samples(),
            issue_time=NOW + timedelta(days=1),
            weather_cycle_time=NOW + timedelta(days=1),
        )
        assert frame["discharge"].to_list() == [20.0]
        assert latest == NOW + timedelta(hours=1)
        assert lineage is not None and lineage.rating_input is not None
        day = lineage.rating_input.days[0]
        assert (day.accepted, day.rejected, day.extrapolated, day.unassessed) == (
            2,
            1,
            1,
            1,
        )
        assert "rating_expired_at_issue" in day.warnings
        assert ForecastInputLineage.from_content(lineage.content) == lineage

    def test_measured_day_takes_precedence(self) -> None:
        measured = pl.DataFrame({"timestamp": [NOW], "discharge": [99.0]})
        frame, lineage, latest = merge_rating_days(
            measured,
            samples(),
            issue_time=NOW + timedelta(days=1),
            weather_cycle_time=NOW,
        )
        assert frame.equals(measured)
        assert lineage is None and latest is None

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), None])
    def test_nonfinite_measured_bucket_is_not_accepted(
        self, value: float | None
    ) -> None:
        measured = pl.DataFrame(
            {"timestamp": [NOW], "discharge": [value]},
            schema_overrides={"discharge": pl.Float64},
        )
        frame, lineage, _ = merge_rating_days(
            measured,
            samples(),
            issue_time=NOW + timedelta(days=1),
            weather_cycle_time=NOW,
        )
        assert frame["discharge"].to_list() == [20.0]
        assert lineage is not None

    def test_current_partial_day_is_not_consumed(self) -> None:
        with pytest.raises(ValueError, match="current UTC day"):
            merge_rating_days(
                pl.DataFrame(),
                samples(),
                issue_time=NOW + timedelta(hours=12),
                weather_cycle_time=NOW,
            )

    def test_unknown_lineage_kind_is_refused(self) -> None:
        _, lineage, _ = merge_rating_days(
            pl.DataFrame(),
            samples(),
            issue_time=NOW + timedelta(days=1),
            weather_cycle_time=NOW,
        )
        assert lineage is not None
        with pytest.raises(ValueError, match="malformed"):
            ForecastInputLineage.from_content(
                lineage.content.replace(
                    '"kind":"provisional_rating"', '"kind":"unknown"'
                )
            )


class TestDeclaredRatingPeriods:
    @pytest.mark.parametrize(
        "phase", [timedelta(0), timedelta(hours=18), timedelta(hours=5, minutes=30)]
    )
    @pytest.mark.parametrize("label", list(PeriodLabel))
    @pytest.mark.parametrize(
        "method,expected",
        [(AggregationMethod.MEAN, 20.0), (AggregationMethod.MAX, 30.0)],
    )
    def test_declared_day_and_method_with_availability_roundtrip(
        self,
        phase: timedelta,
        label: PeriodLabel,
        method: AggregationMethod,
        expected: float,
    ) -> None:
        issue = NOW + timedelta(days=2)
        rows = tuple(
            replace(row, recorded_at=NOW + timedelta(hours=4)) for row in samples()
        )
        period = ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=phase),
            label=label,
            aggregation=method,
        )
        frame, lineage, latest = merge_rating_days(
            pl.DataFrame(),
            rows,
            issue_time=issue,
            weather_cycle_time=issue,
            period=period,
        )
        start = NOW if phase == timedelta(0) else NOW - timedelta(days=1) + phase
        stamp = start + (
            timedelta(days=1) if label is PeriodLabel.END else timedelta(0)
        )
        assert frame["timestamp"].to_list() == [stamp]
        assert frame["discharge"].to_list() == [expected]
        assert frame.schema["available_at_us:discharge"] == pl.Int64
        assert frame["available_at_us:discharge"][0] == int(
            (start + timedelta(days=1)).timestamp() * 1_000_000
        )
        assert latest == NOW + timedelta(hours=1)
        assert lineage is not None and lineage.rating_input is not None
        assert lineage.rating_input.days[0].day == start
        assert ForecastInputLineage.from_content(lineage.content) == lineage

    def test_boundary_sample_belongs_to_next_period_under_end_labels(self) -> None:
        issue = NOW + timedelta(days=2)
        period = ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=1)),
            label=PeriodLabel.END,
            aggregation=AggregationMethod.MEAN,
        )
        rows = tuple(
            replace(row, recorded_at=NOW + timedelta(hours=4)) for row in samples()
        )
        frame, lineage, _ = merge_rating_days(
            pl.DataFrame(),
            rows,
            issue_time=issue,
            weather_cycle_time=issue,
            period=period,
        )
        assert frame["timestamp"].to_list() == [
            NOW + timedelta(hours=1),
            NOW + timedelta(days=1, hours=1),
        ]
        assert frame["discharge"].to_list() == [10.0, 30.0]
        assert lineage is not None and lineage.rating_input is not None
        assert [d.accepted for d in lineage.rating_input.days] == [1, 1]

    @pytest.mark.parametrize("known", [None, NOW + timedelta(days=3)])
    def test_unknown_or_later_source_is_not_admitted(self, known) -> None:
        issue = NOW + timedelta(days=2)
        period = ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            label=PeriodLabel.START,
            aggregation=AggregationMethod.MEAN,
        )
        rows = tuple(replace(row, recorded_at=known) for row in samples())
        frame, lineage, latest = merge_rating_days(
            pl.DataFrame(),
            rows,
            issue_time=issue,
            weather_cycle_time=issue,
            period=period,
        )
        assert frame.is_empty()
        assert lineage is None and latest is None

    def test_late_correction_is_excluded_before_aggregation(self) -> None:
        issue = NOW + timedelta(days=2)
        first, second, rejected = samples()
        rows = (
            replace(first, recorded_at=NOW + timedelta(hours=4)),
            replace(second, recorded_at=issue + timedelta(hours=1)),
            replace(rejected, recorded_at=NOW + timedelta(hours=4)),
        )
        period = ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            label=PeriodLabel.START,
            aggregation=AggregationMethod.MEAN,
        )
        frame, lineage, _ = merge_rating_days(
            pl.DataFrame(),
            rows,
            issue_time=issue,
            weather_cycle_time=issue,
            period=period,
        )
        assert frame["discharge"].to_list() == [10.0]
        assert lineage is not None and lineage.rating_input is not None
        assert second.fingerprint not in lineage.rating_input.days[0].fingerprints

    def test_sparse_point_discharge_cannot_be_summed(self) -> None:
        from sapphire_flow.exceptions import ConfigurationError

        period = ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            label=PeriodLabel.START,
            aggregation=AggregationMethod.SUM,
        )
        with pytest.raises(ConfigurationError, match="daily mean or max"):
            merge_rating_days(
                pl.DataFrame(),
                samples(),
                issue_time=NOW + timedelta(days=2),
                weather_cycle_time=NOW,
                period=period,
            )

    def test_known_measured_period_wins_without_relabelling(self) -> None:
        issue = NOW + timedelta(days=2)
        start = NOW - timedelta(hours=6)
        measured = pl.DataFrame(
            {
                "timestamp": [start],
                "discharge": [99.0],
                "available_at_us:discharge": [int(issue.timestamp() * 1_000_000)],
            }
        )
        period = ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            label=PeriodLabel.START,
            aggregation=AggregationMethod.MEAN,
        )
        rows = tuple(
            replace(row, recorded_at=NOW + timedelta(hours=4)) for row in samples()
        )
        frame, lineage, _ = merge_rating_days(
            measured, rows, issue_time=issue, weather_cycle_time=issue, period=period
        )
        assert frame.equals(measured)
        assert lineage is None
