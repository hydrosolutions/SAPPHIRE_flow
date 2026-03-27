from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl

from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from tests.fakes.fake_stores import (
    FakeHindcastStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationStore,
)

_RNG = random.Random(42)
_EPOCH = ensure_utc(datetime(2025, 3, 1, tzinfo=UTC))


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _seed_hindcasts_and_obs(
    hindcast_store: FakeHindcastStore,
    obs_store: FakeObservationStore,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    hindcast_run_id: UUID,
    parameter: str,
) -> None:
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

    units = "m3/s" if parameter == "discharge" else "m"
    time_step = timedelta(hours=1)

    for i in range(3):
        step = ensure_utc(datetime(2025, 2, i + 1, tzinfo=UTC))
        vt = ensure_utc(datetime(2025, 2, i + 1, 1, 0, tzinfo=UTC))
        df = pl.DataFrame(
            [{"valid_time": vt, "member_id": m, "value": 5.0 + m} for m in range(3)]
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
            hindcast_run_id=hindcast_run_id,
            ensemble=ensemble,
            created_at=step,
        )
        hindcast_store.store_hindcast(hc)
        obs = Observation(
            id=ObservationId(_uuid()),
            station_id=station_id,
            timestamp=vt,
            parameter=parameter,
            value=6.0,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=step,
        )
        obs_store.store_observations([obs])


class TestMultiParameterImport:
    def test_compute_skills_task_is_importable(self) -> None:
        assert hasattr(compute_skills_task, "map")

    def test_compute_skills_task_has_fn(self) -> None:
        assert callable(compute_skills_task.fn)


class TestMultiParameterSkillComputation:
    def test_computes_skills_for_all_target_parameters(self) -> None:
        target_parameters = frozenset({"discharge", "water_level"})
        station_ids = [StationId(_uuid()), StationId(_uuid())]
        model_id = ModelId("multi-param-model")
        artifact_id = ArtifactId(_uuid())
        hindcast_run_id = _uuid()
        clock = lambda: _EPOCH  # noqa: E731

        hindcast_store = FakeHindcastStore()
        obs_store = FakeObservationStore()
        skill_store = FakeSkillStore()
        station_store = FakeStationStore()

        for sid in station_ids:
            for param in target_parameters:
                _seed_hindcasts_and_obs(
                    hindcast_store=hindcast_store,
                    obs_store=obs_store,
                    station_id=sid,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_run_id=hindcast_run_id,
                    parameter=param,
                )

        skill_pairs = [
            (sid, param) for sid in station_ids for param in sorted(target_parameters)
        ]
        for sid, param in skill_pairs:
            compute_skills_task.fn(
                station_id=sid,
                model_id=model_id,
                artifact_id=artifact_id,
                parameter=param,
                hindcast_run_id=hindcast_run_id,
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=None,
                deployment_config=None,
                clock=clock,
            )

        stored_params = {s.parameter for s in skill_store._scores}
        stored_stations = {s.station_id for s in skill_store._scores}

        assert stored_params == target_parameters
        assert stored_stations == set(station_ids)
