from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl

from sapphire_flow.flows.compute_skills import compute_skills_flow, compute_skills_task
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from tests.fakes.fake_stores import (
    FakeFlowRegimeConfigStore,
    FakeHindcastStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationStore,
)

_RNG = random.Random(99)
_EPOCH = ensure_utc(datetime(2025, 1, 15, 0, 0, tzinfo=UTC))


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _populate_stores(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    parameter: str = "discharge",
) -> tuple[
    FakeHindcastStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationStore,
    FakeFlowRegimeConfigStore,
]:
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import (
        EnsembleRepresentation,
        ForcingType,
        ObservationSource,
        QcStatus,
    )
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.ids import HindcastForecastId, ObservationId
    from sapphire_flow.types.observation import Observation

    hindcast_store = FakeHindcastStore()
    obs_store = FakeObservationStore()
    skill_store = FakeSkillStore()
    station_store = FakeStationStore()
    flow_regime_store = FakeFlowRegimeConfigStore()

    units = "m3/s" if parameter == "discharge" else "m"
    time_step = timedelta(hours=1)

    for i in range(3):
        step = ensure_utc(datetime(2025, 1, i + 1, tzinfo=UTC))
        vt = ensure_utc(datetime(2025, 1, i + 1, 1, 0, tzinfo=UTC))

        df = pl.DataFrame(
            [{"valid_time": vt, "member_id": m, "value": 10.0 + m} for m in range(3)]
        ).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )

        ensemble = ForecastEnsemble.from_members(
            station_id=station_id,
            issued_at=step,
            parameter=parameter,
            units=units,
            time_step=time_step,
            values=df,
        )
        hc = HindcastForecast(
            id=HindcastForecastId(_uuid()),
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=artifact_id,
            hindcast_step=step,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=_uuid(),
            ensemble=ensemble,
            created_at=step,
        )
        hindcast_store.store_hindcast(hc)

        obs = Observation(
            id=ObservationId(_uuid()),
            station_id=station_id,
            timestamp=vt,
            parameter=parameter,
            value=10.5,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=step,
        )
        obs_store.store_observations([obs])

    return hindcast_store, obs_store, skill_store, station_store, flow_regime_store


class TestComputeSkillsTask:
    def test_water_level_parameter_computes_skill(self) -> None:
        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        stores = _populate_stores(sid, mid, aid, parameter="water_level")
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        scores, diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="water_level",
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=clock,
        )

        assert len(scores) > 0
        assert all(s.parameter == "water_level" for s in scores)
        assert len(diagrams) > 0
        assert all(d.parameter == "water_level" for d in diagrams)

    def test_flow_wrapper_delegates_to_task(self) -> None:
        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        scores, diagrams = compute_skills_flow(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=clock,
        )

        assert len(scores) > 0
        assert all(s.parameter == "discharge" for s in scores)
