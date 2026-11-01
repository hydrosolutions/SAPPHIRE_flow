from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.rating_derived import derive_rating_sample
from sapphire_flow.services.rating_inputs import merge_rating_days
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.rating_derived import NativeFeedCustody, RatingDerivedDischarge
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
