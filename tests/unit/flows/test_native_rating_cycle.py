from __future__ import annotations

import json
import random
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from sapphire_flow.config.dhm import DhmBinding, DhmConfig
from sapphire_flow.config.provisional_rating import ProvisionalRatingConfig
from sapphire_flow.flows import run_forecast_cycle as cycle
from sapphire_flow.services.rating_inputs import NativeRatingInputs
from sapphire_flow.services.run_group_forecast import GroupStatePolicy
from sapphire_flow.store.rating_reference_store import parse_reference_evidence
from sapphire_flow.types.enums import ForecastDataUse, PipelineCheckType
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.rating_reference import canonical_content
from tests.fakes.fake_adapters import FakeWeatherForecastSource
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeForecastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)
from tests.unit.flows.test_run_forecast_cycle import (
    _build_station_and_stores,
    _empty_qc_rules,
    _make_config,
    _SmallFakeGroupModel,
    _store_group_run,
)
from tests.unit.services.test_rating_inputs import NOW, samples


@pytest.mark.parametrize(
    "mode", ["native", "wrong_store", "missing_model", "fatal_store", "pretrack_fatal"]
)
def test_native_cycle_group_dispatch_isolated_and_stateless(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
    monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
    rows = samples()
    sid = rows[0].station_id
    reference = parse_reference_evidence(
        canonical_content(json.loads(rows[0].content)["reference"])
    )
    mid = ModelId("fake_group_model")
    opt = ProvisionalRatingConfig(
        tenant_id=reference.tenant_id,
        station_id=sid,
        station_code="450",
        model_ids=(str(mid),),
        reference_content=canonical_content(asdict(reference)),
        reconciliation_days=2,
        custody_start=NOW - timedelta(days=2),
        custody_end=NOW + timedelta(days=2),
        custody_reference="fixture",
    )
    dhm = DhmConfig(
        endpoint=reference.endpoint,
        bindings=(
            DhmBinding(
                network="dhm",
                station_code="450",
                api_station_id=reference.api_station_id,
                level_reference="gauge_zero",
            ),
        ),
        max_requests_per_batch=1,
        max_bytes_per_batch=1000,
        max_batch_seconds=30.0,
    )

    class Reader:
        def read_current(
            self,
            binding: object,
            dhm: object,
            *,
            start: datetime,
            end: datetime,
            at: datetime,
        ) -> tuple[RatingDerivedDischarge, ...]:
            return tuple(row for row in rows if start <= row.timestamp < end)

    native_inputs = NativeRatingInputs(
        reader=Reader(), bindings=(opt.to_domain(),), dhm=dhm
    )
    station = FakeStationStore()
    obs = FakeObservationStore()
    weather = FakeWeatherForecastStore()
    artifacts = FakeModelArtifactStore()
    forcing = FakeHistoricalForcingStore()
    groups = FakeStationGroupStore()
    _build_station_and_stores(
        sid,
        mid,
        station,
        obs,
        weather,
        artifacts,
        forcing,
        seed_model_assignment=True,
        seed_artifact=False,
    )
    group = _store_group_run(groups, artifacts, mid, frozenset({sid}))
    assignment = groups.fetch_group_model_assignments(group.id)[0]
    groups.store_group_model_assignment(
        replace(assignment, time_step=timedelta(days=1))
    )
    model = _SmallFakeGroupModel()
    model.data_requirements = replace(
        model.data_requirements,
        lookback_steps=1,
        forecast_horizon_steps=1,
        supported_time_steps=frozenset({timedelta(days=1)}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
    )
    store = FakeForecastStore(
        data_use=ForecastDataUse.STANDARD
        if mode == "wrong_store"
        else ForecastDataUse.PROVISIONAL_RATING
    )
    health = FakePipelineHealthStore()
    if mode == "fatal_store":

        def fail_store(forecast: object, **kwargs: object) -> None:
            raise RuntimeError("fixture native persistence failure")

        monkeypatch.setattr(store, "store_forecast", fail_store)
    recorded = []

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("ordinary station/daily/alert path must not run")

    from sapphire_flow.services import run_group_forecast as group_service
    from sapphire_flow.services import run_station_forecast as station_service

    monkeypatch.setattr(station_service, "run_all_station_forecasts", forbidden)
    monkeypatch.setattr(cycle, "reserves_daily_group", forbidden)
    original = group_service.run_group_forecast

    def group_run(**kwargs: Any) -> Any:
        recorded.append(kwargs["state_policy"])
        return original(**kwargs)

    monkeypatch.setattr(group_service, "run_group_forecast", group_run)
    config = _make_config().model_copy(update={"provisional_ratings": (opt,)})
    kwargs = dict(
        pipeline_health_store=health,
        station_store=station,
        obs_store=obs,
        weather_forecast_store=weather,
        forecast_store=store,
        model_state_store=FakeModelStateStore(),
        artifact_store=artifacts,
        alert_store=FakeAlertStore(),
        baseline_store=FakeClimBaselineStore(),
        basin_store=FakeBasinStore(),
        group_store=groups,
        forcing_store=forcing,
        adapter=FakeWeatherForecastSource(result={}),
        models={} if mode == "missing_model" else {mid: model},
        config=config,
        qc_rules=_empty_qc_rules(),
        clock=lambda: NOW + timedelta(days=1, hours=6),
        rng=random.Random(42),
        rating_inputs=native_inputs,
        data_use=ForecastDataUse.PROVISIONAL_RATING,
    )
    if mode == "pretrack_fatal":
        from uuid import uuid4

        from sapphire_flow.adapters.recap_gateway import RecapAuthError
        from sapphire_flow.types.ids import StationId
        from tests.unit.flows.test_run_forecast_cycle import (
            _MODEL_ID,
            _FakeCandidateAwareSource,
            _SmallFakeModel,
        )

        sibling = StationId(uuid4())
        _build_station_and_stores(
            sibling,
            _MODEL_ID,
            station,
            obs,
            weather,
            artifacts,
            forcing,
            seed_nwp=False,
        )
        issue = NOW + timedelta(days=1, hours=6)
        failure = RecapAuthError("fixture native per-track fatal", status_code=401)
        kwargs["adapter"] = _FakeCandidateAwareSource(
            raise_on_cycle={issue: lambda: failure}
        )
        kwargs["models"] = {mid: model, _MODEL_ID: _SmallFakeModel()}
        with pytest.raises(RecapAuthError, match="fixture native per-track fatal"):
            cycle.run_forecast_cycle_flow(**kwargs)
        assert health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS) == []
        return
    if mode == "fatal_store":
        with pytest.raises(RuntimeError, match="fixture native persistence failure"):
            cycle.run_forecast_cycle_flow(**kwargs)
        assert health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS) == []
        return
    if mode != "native":
        from sapphire_flow.exceptions import ConfigurationError

        with pytest.raises(
            ConfigurationError, match="purpose-scoped|installed native group"
        ):
            cycle.run_forecast_cycle_flow(**kwargs)
        return
    result = cycle.run_forecast_cycle_flow(**kwargs)
    assert result.errors == ()
    assert result.forecasts_stored == 1
    assert recorded == [GroupStatePolicy.STATELESS_ONLY]
    assert result.alerts_checked is False
    assert health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS) == []


if TYPE_CHECKING:
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge
