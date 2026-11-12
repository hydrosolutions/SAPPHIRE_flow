from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from sapphire_flow.services.network_overview import (
    compact_forecast_series,
    forecast_freshness,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import EnsembleRepresentation, ForecastDataUse
from sapphire_flow.types.ids import ModelId, StationId, TenantId
from sapphire_flow.types.network_overview import (
    OverviewAvailability,
    OverviewFreshness,
    OverviewFreshnessPolicy,
)

START = ensure_utc(datetime(2026, 11, 10, tzinfo=UTC))


def ensemble(
    values: list[tuple[int, float, float]],
    parameter: str = "discharge",
    representation: EnsembleRepresentation = EnsembleRepresentation.QUANTILES,
) -> ForecastEnsemble:
    coordinate = (
        "quantile"
        if representation is EnsembleRepresentation.QUANTILES
        else "member_id"
    )
    return ForecastEnsemble(
        representation=representation,
        values=pl.DataFrame(
            {
                "valid_time": [START + timedelta(hours=h) for h, _, _ in values],
                coordinate: [q for _, q, _ in values],
                "value": [v for _, _, v in values],
            }
        ),
        station_id=StationId(UUID(int=1)),
        issued_at=START,
        parameter=parameter,
        units="m" if parameter == "water_level" else "m³/s",
        forecast_horizon_steps=len({h for h, _, _ in values}),
        time_step=timedelta(hours=1),
    )


class TestCompactForecastSeries:
    def test_remaining_interval_and_same_time_band(self) -> None:
        value = ensemble(
            [
                (h, q, v)
                for h, median in [(0, 4), (1, 8), (2, 8)]
                for q, v in [(0.25, median - 1), (0.5, median), (0.75, median + 2)]
            ]
        )
        result = compact_forecast_series(
            value, as_of=ensure_utc(START + timedelta(minutes=30))
        )
        assert result.peak is not None
        assert (
            result.peak.valid_time,
            result.peak.median,
            result.peak.p25,
            result.peak.p75,
        ) == (START + timedelta(hours=1), 8, 7, 10)
        assert result.support_start == START

    def test_expired_support(self) -> None:
        result = compact_forecast_series(
            ensemble([(0, 0.5, 2)]), as_of=ensure_utc(START + timedelta(hours=1))
        )
        assert result.availability is OverviewAvailability.EXPIRED

    def test_negative_water_level_is_not_negative_discharge(self) -> None:
        rows = [(0, 0.25, -3), (0, 0.5, -2), (0, 0.75, -1)]
        assert (
            compact_forecast_series(
                ensemble(rows, "water_level"), as_of=START
            ).availability
            is OverviewAvailability.AVAILABLE
        )
        assert (
            compact_forecast_series(ensemble(rows), as_of=START).availability
            is OverviewAvailability.INVALID_SERIES
        )

    @pytest.mark.parametrize(
        "rows",
        [
            [(0, 0.5, 1), (0, 0.5, 2)],
            [(0, 0.25, 1)],
            [(0, 0.5, float("nan"))],
            [(0, 0.25, 3), (0, 0.5, 2)],
        ],
    )
    def test_invalid_quantiles(self, rows: list[tuple[int, float, float]]) -> None:
        assert (
            compact_forecast_series(ensemble(rows), as_of=START).availability
            is OverviewAvailability.INVALID_SERIES
        )

    def test_missing_band_is_not_interpolated(self) -> None:
        result = compact_forecast_series(
            ensemble([(0, 0.1, 1), (0, 0.5, 4), (0, 0.9, 8)]), as_of=START
        )
        assert result.peak is not None
        assert result.peak.p25 is None and result.peak.p75 is None

    def test_member_quartiles_use_type7(self) -> None:
        result = compact_forecast_series(
            ensemble(
                [(0, m, float(m)) for m in range(1, 7)],
                representation=EnsembleRepresentation.MEMBERS,
            ),
            as_of=START,
        )
        assert result.peak is not None
        assert (result.peak.median, result.peak.p25, result.peak.p75) == (
            3.5,
            2.25,
            4.75,
        )

    def test_reduction_preserves_peak_edges_and_gap(self) -> None:
        value = ensemble(
            [
                (h, 0.5, float(100 if h == 17 else h))
                for h in range(60)
                if h not in {31, 32}
            ]
        )
        result = compact_forecast_series(value, as_of=START)
        assert len(result.points) == 24
        assert result.peak in result.points
        assert result.points[0].valid_time == START
        assert result.points[-1].valid_time == START + timedelta(hours=59)
        assert len(result.gaps) == 1
        assert {START + timedelta(hours=h) for h in (30, 33)} <= {
            p.valid_time for p in result.points
        }

    def test_excess_gap_anchors_keep_headline_without_false_chart(self) -> None:
        result = compact_forecast_series(
            ensemble([(h * 2, 0.5, float(h)) for h in range(30)]), as_of=START
        )
        assert result.availability is OverviewAvailability.COMPACT_UNAVAILABLE
        assert result.peak is not None and result.peak.median == 29
        assert result.points == ()


class TestForecastFreshness:
    @pytest.fixture
    def policy(self) -> OverviewFreshnessPolicy:
        return OverviewFreshnessPolicy(
            tenant_id=TenantId(UUID(int=1)),
            network="dhm",
            model_id=ModelId("cmal_small"),
            parameter="discharge",
            data_use=ForecastDataUse.PROVISIONAL_RATING,
            timezone=ZoneInfo("Asia/Kathmandu"),
            issue_timezone=ZoneInfo("Asia/Kathmandu"),
            issue_time=time(5, 45),
            delivery_deadline=time(7),
        )

    def test_yesterday_stales_at_deadline(
        self, policy: OverviewFreshnessPolicy
    ) -> None:
        yesterday = ensure_utc(START - timedelta(days=1))
        assert (
            forecast_freshness(
                issued_at=yesterday,
                as_of=ensure_utc(START + timedelta(hours=1, minutes=14)),
                policy=policy,
            ).freshness
            is OverviewFreshness.CURRENT
        )
        assert (
            forecast_freshness(
                issued_at=yesterday,
                as_of=ensure_utc(START + timedelta(hours=1, minutes=15)),
                policy=policy,
            ).freshness
            is OverviewFreshness.STALE
        )

    def test_visible_midnight_issue_wins_before_or_after_deadline(
        self, policy: OverviewFreshnessPolicy
    ) -> None:
        for minutes in (0, 15, 74, 75, 120):
            assert (
                forecast_freshness(
                    issued_at=START,
                    as_of=ensure_utc(START + timedelta(minutes=minutes)),
                    policy=policy,
                ).freshness
                is OverviewFreshness.CURRENT
            )

    @pytest.mark.parametrize(
        ("day", "deadline_utc_hour"),
        [
            (datetime(2026, 11, 10, tzinfo=UTC), 6),
            (datetime(2026, 7, 10, tzinfo=UTC), 5),
            (datetime(2026, 3, 29, tzinfo=UTC), 5),
            (datetime(2026, 10, 25, tzinfo=UTC), 6),
        ],
        ids=["winter", "summer", "spring_forward", "fall_back"],
    )
    def test_swiss_utc_issue_stays_current_until_seven_local(
        self, day: datetime, deadline_utc_hour: int
    ) -> None:
        swiss = OverviewFreshnessPolicy(
            tenant_id=TenantId(UUID(int=2)),
            network="bafu",
            model_id=ModelId("cmal_small"),
            parameter="discharge",
            data_use=ForecastDataUse.STANDARD,
            timezone=ZoneInfo("Europe/Zurich"),
            issue_timezone=ZoneInfo("UTC"),
            issue_time=time(0),
            delivery_deadline=time(7),
        )
        today = ensure_utc(day)
        yesterday = ensure_utc(day - timedelta(days=1))
        deadline = ensure_utc(day + timedelta(hours=deadline_utc_hour))

        def freshness(issued_at: datetime, as_of: datetime) -> OverviewFreshness:
            return forecast_freshness(
                issued_at=ensure_utc(issued_at), as_of=ensure_utc(as_of), policy=swiss
            ).freshness

        after_swiss_midnight = today - timedelta(minutes=30)
        assert freshness(yesterday, after_swiss_midnight) is OverviewFreshness.CURRENT
        assert freshness(yesterday, deadline - timedelta(minutes=1)) is (
            OverviewFreshness.CURRENT
        )
        assert freshness(yesterday, deadline) is OverviewFreshness.STALE
        assert freshness(today, deadline) is OverviewFreshness.CURRENT
        result = forecast_freshness(issued_at=today, as_of=deadline, policy=swiss)
        assert (result.expected_issue, result.deadline) == (today, deadline)

    def test_missing_policy_is_unknown(self) -> None:
        assert (
            forecast_freshness(issued_at=START, as_of=START, policy=None).freshness
            is OverviewFreshness.UNKNOWN
        )
