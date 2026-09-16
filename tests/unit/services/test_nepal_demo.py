from datetime import UTC, datetime
from random import Random

import pytest

from sapphire_flow.services.nepal_demo import build_scenario
from sapphire_flow.types.datetime import ensure_utc


class TestBuildScenario:
    def test_reproducible_history_forecast_and_separate_outturn(self) -> None:
        issued = ensure_utc(datetime(2025, 8, 12, tzinfo=UTC))
        scenario = build_scenario(issued, Random(20260916))
        assert scenario == build_scenario(issued, Random(20260916))
        assert len(scenario.history) == 168
        assert [i for i, v in enumerate(scenario.history) if v is None] == list(
            range(120, 126)
        )
        assert len(scenario.median) == 72
        assert all(
            0 <= lo <= mid <= hi
            for lo, mid, hi in zip(
                scenario.lower, scenario.median, scenario.upper, strict=True
            )
        )
        assert scenario.median.index(max(scenario.median)) < max(
            range(72), key=lambda i: scenario.outturn[i] or 0
        )
        assert [i for i, v in enumerate(scenario.outturn) if v is None] == [40, 41]

    def test_naive_time_rejected(self) -> None:
        from sapphire_flow.types.datetime import UtcDatetime

        with pytest.raises(ValueError, match="UTC"):
            build_scenario(UtcDatetime(datetime(2025, 8, 12)), Random(20260916))
