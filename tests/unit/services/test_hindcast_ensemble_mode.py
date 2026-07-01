"""LOCKED test: hindcast is UNCHANGED by the ensemble fan-out feature.

Reanalysis is a single teacher-forced trajectory (bare ``precipitation`` /
``temperature`` columns — no member suffixes), so an ``ensemble_mode == ENSEMBLE``
model run through ``run_station_hindcast`` still yields 1-member ensembles: the
fan-out is an operational/conformance concern and must NOT touch the hindcast
path.

RED reason (pre-implementation): ``EnsembleMode`` / the ``ensemble_mode`` field do
not exist yet (collection error). Post-implementation this locks the no-fan-out
guarantee for hindcast.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl

from sapphire_flow.services.hindcast import run_station_hindcast
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    ArtifactScope,
    EnsembleMode,
    SpatialRepresentation,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.model import (
    ModelArtifact,
    ModelDataRequirements,
    ModelParams,
    StationModelInputs,
    StationTrainingData,
)
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import (
    make_observations,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHindcastStore,
    FakeObservationStore,
    FakeStationStore,
)

_STEP = timedelta(hours=24)
_PERIOD_START = ensure_utc(datetime(2022, 1, 1, tzinfo=UTC))
_PERIOD_END = ensure_utc(datetime(2022, 1, 6, tzinfo=UTC))  # 5 issue times


def _fixed_clock() -> UtcDatetime:
    return ensure_utc(datetime(2022, 6, 1, tzinfo=UTC))


class _EnsembleModeStationModel:
    """ensemble_mode=ENSEMBLE; predict emits a 1-member ensemble from the bare
    future precipitation column (single reanalysis trajectory)."""

    artifact_scope = ArtifactScope.STATION
    data_requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=1,
        forecast_horizon_steps=2,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=EnsembleMode.ENSEMBLE,
    )

    def train(
        self, data: StationTrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact:
        return b"artifact"

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        fd = inputs.data.future_dynamic
        values = fd.select(
            pl.col("timestamp").alias("valid_time"),
            pl.lit(1).cast(pl.Int32).alias("member_id"),
            pl.col("precipitation").cast(pl.Float64).alias("value"),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter="discharge",
            units="m³/s",
            time_step=inputs.time_step,
            values=values,
        )
        return {"discharge": ensemble}, None

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return artifact if isinstance(artifact, bytes) else b"artifact"

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return raw


def _make_weather_source(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="smn",
        extraction_type=SpatialRepresentation.POINT,
        status=WeatherSourceStatus.ACTIVE,
    )


def _seed_forcing(
    source: FakeWeatherReanalysisSource,
    station_id: StationId,
    start: UtcDatetime,
    n_days: int,
) -> None:
    records = []
    for i in range(n_days * 24):
        ts = ensure_utc(datetime.fromtimestamp(start.timestamp() + i * 3600, tz=UTC))
        for param in ("precipitation", "temperature"):
            records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter=param,
                    valid_time=ts,
                    value=float(i % 20),
                )
            )
    source.set_records(records)


def _seed_observations(
    obs_store: FakeObservationStore,
    station_id: StationId,
    start: UtcDatetime,
    n_days: int,
) -> None:
    obs = make_observations(
        n=n_days * 24,
        station_id=station_id,
        start=start,
        interval=timedelta(hours=1),
    )
    obs_store.store_observations(obs)


def test_ensemble_mode_hindcast_yields_single_member_ensembles() -> None:
    rng = random.Random(0)
    station = make_station_config()
    sid = station.id
    model_id = ModelId("ensemble_model")
    artifact_id = ArtifactId(uuid4())
    run_id = uuid4()

    obs_store = FakeObservationStore()
    hindcast_store = FakeHindcastStore()
    station_store = FakeStationStore()
    basin_store = FakeBasinStore()
    forcing_source = FakeWeatherReanalysisSource()

    station_store.store_station(station)
    station_store.store_weather_source(_make_weather_source(sid))

    data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
    _seed_observations(obs_store, sid, data_start, n_days=400)
    _seed_forcing(forcing_source, sid, data_start, n_days=400)

    results = run_station_hindcast(
        model=_EnsembleModeStationModel(),  # type: ignore[arg-type]
        artifact=b"artifact",
        station_id=sid,
        model_id=model_id,
        artifact_id=artifact_id,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        time_step=_STEP,
        forcing_source=forcing_source,
        obs_store=obs_store,
        hindcast_store=hindcast_store,
        station_store=station_store,
        basin_store=basin_store,
        clock=_fixed_clock,
        rng=rng,
        hindcast_run_id=run_id,
    )

    assert any(r.success for r in results)
    stored = list(hindcast_store._hindcasts.values())
    assert stored, "expected hindcast forecasts to be stored"
    for h in stored:
        assert h.ensemble.member_count == 1
