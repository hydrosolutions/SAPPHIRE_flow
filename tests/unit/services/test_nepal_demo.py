from datetime import UTC, datetime, timedelta
from random import Random

import pytest

from sapphire_flow.services.nepal_demo import build_issue, build_scenario
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc

FIRST = ensure_utc(datetime(2025, 8, 12, tzinfo=UTC))


class TestBuildScenario:
    def test_exact_schedule_and_spanning_observations(self) -> None:
        scenario = build_scenario(
            FIRST, observation_rng=Random(20260916), forecast_rng=Random(20260917)
        )
        assert len(scenario.observations) == 211
        assert [
            i for i, value in enumerate(scenario.observations) if value is None
        ] == [120, 121, 122, 123, 124, 125, 185, 186]
        assert [f.issued_at for f in scenario.forecasts] == [
            FIRST + timedelta(hours=6 * i) for i in range(8)
        ]
        for forecast in scenario.forecasts:
            assert len(forecast.median) == 24
            assert all(
                0 <= lo <= mid <= hi
                for lo, mid, hi in zip(
                    forecast.lower, forecast.median, forecast.upper, strict=True
                )
            )

    def test_observations_cannot_change_forecasts(self) -> None:
        first = build_scenario(FIRST, observation_rng=Random(1), forecast_rng=Random(2))
        second = build_scenario(
            FIRST, observation_rng=Random(100), forecast_rng=Random(2)
        )
        assert first.observations != second.observations
        assert first.forecasts == second.forecasts

    def test_forecast_stream_cannot_change_observations(self) -> None:
        first = build_scenario(FIRST, observation_rng=Random(1), forecast_rng=Random(2))
        second = build_scenario(
            FIRST, observation_rng=Random(1), forecast_rng=Random(3)
        )
        assert first.observations == second.observations
        assert first.forecasts != second.forecasts

    def test_fixed_inputs_reproduce_the_whole_run(self) -> None:
        assert build_scenario(
            FIRST, observation_rng=Random(1), forecast_rng=Random(2)
        ) == (build_scenario(FIRST, observation_rng=Random(1), forecast_rng=Random(2)))

    def test_naive_first_time_rejected(self) -> None:
        with pytest.raises(ValueError, match="Naive datetime"):
            build_scenario(
                UtcDatetime(datetime(2025, 8, 12)),
                observation_rng=Random(1),
                forecast_rng=Random(2),
            )


class TestBuildIssue:
    def test_issue_position_does_not_adjust_the_drawn_curve(self) -> None:
        early = build_issue(first_issued_at=FIRST, issued_at=FIRST, rng=Random(7))
        later = build_issue(
            first_issued_at=FIRST,
            issued_at=ensure_utc(FIRST + timedelta(hours=6)),
            rng=Random(7),
        )
        # Same parameter draw, same absolute valid times: no index-based convergence.
        assert early.median[2:] == later.median[:-2]
        assert early.lower[2:] == later.lower[:-2]
        assert early.upper[2:] == later.upper[:-2]
