from pathlib import Path

import pytest
import sqlalchemy as sa

from sapphire_flow.types.daily_forecast_claim import (
    DailyClaimKey,
    FrozenDailyAssignment,
)
from tests.integration.store.swiss_overview_benchmark import (
    run_swiss_overview_benchmark,
)
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)


class TestSwissOverviewIsolated:
    def test_134_station_bounded_reads(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_swiss_overview_benchmark(
            shape="stress_members",
            isolated_committed_db_engine=isolated_committed_db_engine,
            assignment=assignment,
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
