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
    "failure_mode", ["none", "empty", "capture", "past_stale", "future_stale"]
)
def test_selected_scheduled_daily_commits_and_retries_with_immutable_weather(
    isolated_committed_db_engine: sa.Engine,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: Literal["none", "empty", "capture", "past_stale", "future_stale"],
) -> None:
    engine = isolated_committed_db_engine
    model = ScheduledWeatherModel(empty_first=failure_mode == "empty")
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
            PgStationStore(conn).store_station(
                replace(station, basin_id=None, code=f"weather-{sid}")
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

        forecasts = _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            model_id=ModelId("cmal_small"),
            first_station_id=sid,
            model_override=model,
            configure_cycle=configure,
            execution_runs=2 if stale_leg is None else 1,
            configure_refresh=enable_scheduled if stale_leg is not None else None,
            refresh_case=(
                "past_unavailable"
                if stale_leg == "past"
                else "future_unavailable"
                if stale_leg == "future"
                else "none"
            ),
        )
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
                    if failure_mode != "none"
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
