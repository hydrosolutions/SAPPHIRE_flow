from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from prefect.client.schemas.objects import FlowRun

from sapphire_flow.flows.daily_forecast_context import scheduled_daily_run


class TestScheduledDailyRun:
    def test_uses_expected_tick_not_actual_start(self) -> None:
        deployment = uuid4()
        run = FlowRun(
            flow_id=uuid4(),
            deployment_id=deployment,
            auto_scheduled=True,
            expected_start_time=datetime(2026, 10, 26, 23, tzinfo=UTC),
            start_time=datetime(2026, 10, 27, 1, tzinfo=UTC),
        )
        result = scheduled_daily_run(run, expected_deployment_id=deployment)
        assert result is not None
        assert result.context.issue_time == datetime(2026, 10, 26, tzinfo=UTC)
        assert result.owner_id == run.id

    @pytest.mark.parametrize(
        "changes",
        [
            {"auto_scheduled": False},
            {"deployment_id": None},
            {"deployment_id": uuid4()},
            {"expected_start_time": None},
            {"parent_task_run_id": uuid4()},
        ],
    )
    def test_unproven_runs_cannot_be_scheduled(
        self, changes: dict[str, object]
    ) -> None:
        deployment = uuid4()
        run = FlowRun(
            flow_id=uuid4(),
            deployment_id=deployment,
            auto_scheduled=True,
            expected_start_time=datetime(2026, 10, 26, tzinfo=UTC),
        )
        assert (
            scheduled_daily_run(
                run.model_copy(update=changes), expected_deployment_id=deployment
            )
            is None
        )

    def test_no_runtime_context_is_not_scheduled(self) -> None:
        assert scheduled_daily_run(None, expected_deployment_id=uuid4()) is None

    def test_day_is_utc_not_local_calendar_date(self) -> None:
        deployment: UUID = uuid4()
        run = FlowRun(
            flow_id=uuid4(),
            deployment_id=deployment,
            auto_scheduled=True,
            expected_start_time=datetime(
                2026, 10, 27, 1, tzinfo=timezone(timedelta(hours=5, minutes=45))
            ),
        )
        result = scheduled_daily_run(run, expected_deployment_id=deployment)
        assert result is not None
        assert result.context.issue_time == datetime(2026, 10, 26, tzinfo=UTC)
