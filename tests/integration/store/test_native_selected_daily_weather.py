from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.config.daily_evaluation import (
    DailyEvaluationConfig,
    DailyOutputContractConfig,
)
from sapphire_flow.db.metadata import model_artifacts
from sapphire_flow.flows import daily_forecast_execution as execution
from sapphire_flow.flows.daily_forecast_context import ScheduledDailyRun
from sapphire_flow.services.forecast_evidence import restore_snapshot
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.operational_weather_cache import (
    PgCommittedOperationalWeatherCache,
)
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.weather_forecast_store import PgWeatherForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptState, ScheduledDailyForecast
from sapphire_flow.types.daily_forecast_claim import DailyClaimKey
from sapphire_flow.types.enums import (
    AlertEligibility,
    EnsembleRepresentation,
    SpatialRepresentation,
)
from sapphire_flow.types.ids import ModelId, StationId
from tests.fakes.fake_models import FakeGroupForecastModel
from tests.unit.adapters.test_recap_operational import ISSUE
from tests.unit.flows.test_run_forecast_cycle import (
    _block_real_httpx_network,  # noqa: F401
)
from tests.unit.flows.test_selected_weather_cycle import _run_selected_cycle

if TYPE_CHECKING:
    import random

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.flows.run_forecast_cycle import ForecastCycleResult
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.model import GroupModelInputs
    from tests.fakes.fake_stores import FakeStationGroupStore


class ScheduledWeatherModel(FakeGroupForecastModel):
    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = replace(
        FakeGroupForecastModel.data_requirements,
        supported_time_steps=frozenset({timedelta(days=1)}),
        lookback_steps=2,
        forecast_horizon_steps=10,
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
    )

    def __init__(self, *, empty_first: bool) -> None:
        self.calls = 0
        self.empty_first = empty_first

    def predict_batch(
        self, artifact: object, inputs: GroupModelInputs, rng: random.Random
    ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
        self.calls += 1
        if self.empty_first and self.calls == 1:
            return {}
        return super().predict_batch(artifact, inputs, rng)


@pytest.mark.parametrize(
    "failure_mode",
    [
        "none",
        "empty",
        "capture",
        "past_stale",
        "future_stale",
        "automatic_repeat",
        "calendar_refused",
        "creation_calendar_refused",
        "mixed_calendar",
    ],
)
def test_selected_scheduled_daily_commits_and_retries_with_immutable_weather(
    isolated_committed_db_engine: sa.Engine,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: Literal[
        "none",
        "empty",
        "capture",
        "past_stale",
        "future_stale",
        "automatic_repeat",
        "calendar_refused",
        "creation_calendar_refused",
        "mixed_calendar",
    ],
) -> None:
    engine = isolated_committed_db_engine
    model = ScheduledWeatherModel(empty_first=failure_mode == "empty")
    actual_time = ISSUE
    claims_before_guard = []
    calendar_refusal = failure_mode in ("calendar_refused", "creation_calendar_refused")
    if calendar_refusal:
        method_name = (
            "deserialize_artifact"
            if failure_mode == "calendar_refused"
            else "predict_batch"
        )
        original_method = getattr(model, method_name)

        def advance_day(*args: object, **kwargs: object) -> object:
            nonlocal actual_time
            result = original_method(*args, **kwargs)
            snapshot = ledger.read_day(keys[0])
            assert snapshot.latest is not None
            claims_before_guard.append(snapshot.latest.claim)
            actual_time = ISSUE + timedelta(days=1)
            return result

        monkeypatch.setattr(model, method_name, advance_day)
    if failure_mode == "capture":
        import importlib

        from sapphire_flow.exceptions import WeatherEvidenceCaptureError

        runner = importlib.import_module("sapphire_flow.services.run_group_forecast")
        capture = runner.capture_group_evidence
        capture_calls = 0

        def first_capture_fails(**kwargs: object) -> object:
            nonlocal capture_calls
            capture_calls += 1
            if capture_calls == 1:
                raise WeatherEvidenceCaptureError("scheduled selected capture fixture")
            return capture(**kwargs)

        monkeypatch.setattr(runner, "capture_group_evidence", first_capture_fails)
    sid = StationId(uuid4())
    mixed_calendar = failure_mode == "mixed_calendar"
    if mixed_calendar:
        from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker

        original_check = ForecastOutputQualityChecker.check

        def advance_second_member(
            self: ForecastOutputQualityChecker,
            ensemble: ForecastEnsemble,
            *args: object,
            **kwargs: object,
        ) -> object:
            nonlocal actual_time
            flags = original_check(self, ensemble, *args, **kwargs)
            if ensemble.station_id != sid:
                actual_time = ISSUE + timedelta(days=1)
            return flags

        monkeypatch.setattr(
            ForecastOutputQualityChecker, "check", advance_second_member
        )
    ledger = PgDailyForecastStore(engine.begin)
    scheduled_calls: list[ScheduledDailyRun] = []
    keys: list[DailyClaimKey] = []
    stale_leg = (
        failure_mode.removesuffix("_stale") if failure_mode.endswith("_stale") else None
    )
    deferred_options: dict[str, object] = {}
    committed_store: PgForecastStore | None = None

    def scheduled(**kwargs: object) -> ScheduledDailyRun:
        run = ScheduledDailyRun(
            owner_id=uuid4(),
            context=ScheduledDailyForecast(
                scheduled_tick=ISSUE + timedelta(hours=6 * len(scheduled_calls))
            ),
        )
        scheduled_calls.append(run)
        return run

    monkeypatch.setattr(execution, "current_scheduled_daily_run", scheduled)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:

        def configure(
            stores: dict[str, object],
            groups: FakeStationGroupStore | None,
            config: DeploymentConfig,
            mid: ModelId,
        ) -> dict[str, object]:
            assert groups is not None
            group = replace(
                groups.fetch_groups_for_station(sid)[0], name=f"native-weather-{sid}"
            )
            groups.store_group(group)
            station = stores["station_store"].fetch_station(sid)
            artifact_id, _ = stores["artifact_store"].fetch_active_artifact(
                mid, group_id=group.id
            )
            artifact = stores["artifact_store"].fetch_artifact_record(artifact_id)
            assert station is not None and artifact is not None
            for member_id in group.station_ids:
                member_station = stores["station_store"].fetch_station(member_id)
                assert member_station is not None
                PgStationStore(conn).store_station(
                    replace(member_station, basin_id=None, code=f"weather-{member_id}")
                )
            PgStationGroupStore(conn, transaction_factory=engine.begin).store_group(
                group
            )
            conn.execute(
                sa.text(
                    "INSERT INTO models "
                    "(id, display_name, artifact_scope, description) "
                    "VALUES ('cmal_small', 'Offline daily fixture', 'group', 'test') "
                    "ON CONFLICT (id) DO NOTHING"
                )
            )
            conn.execute(
                sa.insert(model_artifacts).values(
                    id=artifact.id,
                    model_id=mid,
                    group_id=group.id,
                    status="active",
                    artifact_path=artifact.artifact_path,
                    sha256_hash=artifact.sha256_hash,
                    training_period_start=artifact.training_period_start,
                    training_period_end=artifact.training_period_end,
                    trained_at=artifact.trained_at,
                )
            )
            stores["nwp_store"] = PgWeatherForecastStore(conn)
            nonlocal committed_store
            committed_store = PgForecastStore(conn, transaction_factory=engine.begin)
            if stale_leg is None:
                stores["forecast_store"] = committed_store
            deployment_id = uuid4()
            daily = DailyEvaluationConfig(
                tenant_id=station.tenant_id,
                deployment="native-weather-test",
                prefect_deployment_id=deployment_id,
                group_ids=(group.id,),
                output_contracts=(
                    DailyOutputContractConfig(
                        parameter="discharge",
                        units="m³/s",
                        representation=EnsembleRepresentation.MEMBERS,
                        first_lead_hours=24,
                        horizon_steps=10,
                        series_keys=tuple(float(i) for i in range(21)),
                    ),
                ),
            )
            keys.append(
                DailyClaimKey(
                    tenant_id=station.tenant_id,
                    deployment="native-weather-test",
                    model_id=mid,
                    group_id=group.id,
                    context=ScheduledDailyForecast(scheduled_tick=ISSUE),
                )
            )
            options = {
                "config": config.model_copy(update={"daily_evaluation": daily}),
                "daily_forecast_store": ledger,
                "daily_forecast_writer": PgCommittedForecastWriter(engine.begin),
                "rejected_forecast_store": PgRejectedForecastStore(
                    conn, transaction_factory=engine.begin
                ),
                "operational_weather_cache": PgCommittedOperationalWeatherCache(
                    engine.begin
                ),
            }
            if failure_mode == "automatic_repeat" or calendar_refusal or mixed_calendar:
                from zoneinfo import ZoneInfo

                options["config"] = config.model_copy(
                    update={
                        "daily_evaluation": daily,
                        "nwp_max_fallback_age_hours": 36.0,
                        "operational_weather": config.operational_weather.model_copy(
                            update={
                                "automatic_issue_timezone": ZoneInfo("Asia/Kathmandu"),
                            }
                        ),
                    }
                )
                options["cycle_time"] = None
                if calendar_refusal or mixed_calendar:
                    options["clock"] = lambda: actual_time
            if stale_leg is not None:
                deferred_options.update(options)
                return {
                    "operational_weather_cache": options["operational_weather_cache"]
                }
            return options

        def enable_scheduled(stores: dict[str, object]) -> dict[str, object]:
            assert committed_store is not None
            stores["forecast_store"] = committed_store
            return deferred_options

        def verify_refusal(
            result: ForecastCycleResult, stores: dict[str, object]
        ) -> None:
            assert result.forecasts_stored == 0
            assert any("issue_day_mismatch" in error for error in result.errors)
            assert not [
                forecast
                for forecast in stores["forecast_store"].fetch_forecasts_for_cycle(
                    ISSUE
                )
                if forecast.station_id == sid
            ]

        def verify_mixed(
            result: ForecastCycleResult, stores: dict[str, object]
        ) -> None:
            assert result.forecasts_stored == 1, result.errors
            assert any("issue_day_mismatch" in error for error in result.errors)

        forecasts = _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            model_id=ModelId("cmal_small"),
            first_station_id=sid,
            station_count=2 if mixed_calendar else 1,
            model_override=model,
            configure_cycle=configure,
            execution_runs=2
            if stale_leg is None and not calendar_refusal and not mixed_calendar
            else 1,
            verify_cycle=verify_refusal
            if calendar_refusal
            else verify_mixed
            if mixed_calendar
            else None,
            configure_refresh=enable_scheduled if stale_leg is not None else None,
            refresh_case=(
                "past_unavailable"
                if stale_leg == "past"
                else "future_unavailable"
                if stale_leg == "future"
                else "none"
            ),
        )
        if mixed_calendar:
            from sapphire_flow.types.daily_forecast_outcome import (
                DailyMemberReason,
                DailyMemberState,
            )

            assert forecasts is not None and len(forecasts) == 1
            assert forecasts[0].station_id == sid
            assert forecasts[0].created_at == ISSUE
            assert forecasts[0].weather_evidence_version == 1
            assert model.calls == 1
            day = ledger.read_day(keys[0])
            assert (
                day.latest is not None
                and day.latest.state is DailyAttemptState.COMPLETE
            )
            members = {member.station_id: member for member in day.latest.outcomes}
            assert len(members) == 2
            assert members[sid].state is DailyMemberState.PRODUCED
            assert len(members[sid].references) == 1
            excluded = next(
                member for member_id, member in members.items() if member_id != sid
            )
            assert excluded.state is DailyMemberState.INPUT_EXCLUDED
            assert excluded.reason is DailyMemberReason.INPUTS_UNAVAILABLE
            assert excluded.references == ()
            assert all(
                member.provenance.nwp_reference_time == ISSUE
                for member in members.values()
            )
            stored = (
                conn.execute(
                    sa.text(
                        "SELECT station_id FROM forecasts "
                        "WHERE station_id IN (:good, :excluded)"
                    ),
                    {"good": sid, "excluded": excluded.station_id},
                )
                .scalars()
                .all()
            )
            assert stored == [sid]
            return
        if calendar_refusal:
            assert forecasts == []
            assert model.calls == (0 if failure_mode == "calendar_refused" else 1)
            day = ledger.read_day(keys[0])
            assert day.authoritative is not None
            assert day.authoritative.state is DailyAttemptState.INCOMPLETE
            assert (
                day.latest is not None
                and day.latest.state is DailyAttemptState.INCOMPLETE
            )
            assert day.latest.claim.key == keys[0]
            assert day.latest.claim.key.context.issue_time == ISSUE
            assert day.latest.claim == claims_before_guard[0]
            assert day.latest.failure_reason is None
            from sapphire_flow.types.daily_forecast_outcome import (
                DailyMemberReason,
                DailyMemberState,
            )

            assert all(
                member.state is DailyMemberState.INPUT_EXCLUDED
                and member.reason is DailyMemberReason.INPUTS_UNAVAILABLE
                for member in day.latest.outcomes
            )
            return
        assert forecasts is not None and len(forecasts) == 1
        assert model.calls == (
            2 if failure_mode == "empty" or stale_leg is not None else 1
        )
        day = ledger.read_day(keys[0])
        assert day.authoritative is not None
        assert day.authoritative.state is DailyAttemptState.COMPLETE
        assert day.authoritative.claim.ordinal.value == (
            2 if failure_mode in ("empty", "capture") else 1
        )
        evidence = forecasts[0].evidence
        assert (
            evidence is not None
            and evidence.weather_evidence_version == 1
            and evidence.snapshot is not None
        )
        source = restore_snapshot(evidence.snapshot)["source_records"][0][1]
        assert (
            source["weather_use"]["used_at"]
            == (
                ISSUE
                + timedelta(
                    hours=1
                    if stale_leg is not None
                    else 6
                    if failure_mode not in ("none", "automatic_repeat")
                    else 0
                )
            ).isoformat()
        )

        if stale_leg is not None:
            from tests.integration.api.test_dashboard_forecasts import (
                _client,
                app_overrides_clear,
            )

            client = _client(conn)
            try:
                response = client.get(f"/api/v1/forecasts/{forecasts[0].id}/data.json")
            finally:
                client.close()
                app_overrides_clear()
            assert response.status_code == 200
            body = response.json()
            assert body["weather_use"][stale_leg]["freshness"] == "stale"
            assert body["input_quality"] == "degraded"
            assert any(
                "weather STALE" in flag["detail"]
                for flag in body["input_quality_flags"]
            )
