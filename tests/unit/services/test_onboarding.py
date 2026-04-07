from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from sapphire_flow.services.onboarding import _run_onboarding
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import (
    ModelArtifactStatus,
    ObservationSource,
    QcStatus,
    StationKind,
    StationStatus,
)
from sapphire_flow.types.ids import BasinId, ModelId, StationId
from sapphire_flow.types.observation import RawObservation
from tests.conftest import (
    make_deployment_config,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeFlowRegimeConfigStore,
    FakeHindcastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2000, 1, 1, tzinfo=UTC))
_END = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))
_START = ensure_utc(datetime(1980, 1, 1, tzinfo=UTC))

_TEST_RULES = QcRuleSet(
    version="test",
    rules=(
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(days=1),
            thresholds={"value_min": 0.0, "value_max": 10000.0},
        ),
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="water_level",
            time_step=timedelta(days=1),
            thresholds={"value_min": 0.0, "value_max": 1000.0},
        ),
    ),
)


def _fixed_clock() -> UtcDatetime:
    return _EPOCH


def _make_basin(code: str) -> Basin:
    return Basin(
        id=BasinId(uuid4()),
        code=code,
        name=f"Basin {code}",
        geometry=None,
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=_EPOCH,
        network="bafu",
    )


def _make_raw_obs(
    station_id: StationId,
    n: int = 100,
    start: UtcDatetime | None = None,
) -> list[RawObservation]:
    t = start or _EPOCH
    return [
        RawObservation(
            station_id=station_id,
            timestamp=ensure_utc(
                datetime.fromtimestamp(t.timestamp() + i * 86400, tz=UTC)
            ),
            parameter="discharge",
            value=float(10 + i % 50),
            source=ObservationSource.MANUAL_IMPORT,
        )
        for i in range(n)
    ]


def _make_raw_waterlevel_obs(
    station_id: StationId,
    n: int = 100,
    start: UtcDatetime | None = None,
) -> list[RawObservation]:
    t = start or _EPOCH
    return [
        RawObservation(
            station_id=station_id,
            timestamp=ensure_utc(
                datetime.fromtimestamp(t.timestamp() + i * 86400, tz=UTC)
            ),
            parameter="water_level",
            value=float(100 + i % 50),
            source=ObservationSource.MANUAL_IMPORT,
        )
        for i in range(n)
    ]


def _make_forcing(
    station_id: StationId,
    n: int = 100,
    start: UtcDatetime | None = None,
) -> list:
    t = start or _EPOCH
    return [
        make_raw_historical_forcing(
            station_id=station_id,
            valid_time=datetime.fromtimestamp(t.timestamp() + i * 86400, tz=UTC),
            parameter="precipitation",
            value=float(i % 20),
        )
        for i in range(n)
    ]


class _Stores:
    def __init__(self) -> None:
        self.basin = FakeBasinStore()
        self.station = FakeStationStore()
        self.obs = FakeObservationStore()
        self.forcing = FakeHistoricalForcingStore()
        self.baseline = FakeClimBaselineStore()
        self.regime = FakeFlowRegimeConfigStore()
        self.model: FakeModelStore | None = None
        self.artifact: FakeModelArtifactStore | None = None
        self.group: FakeStationGroupStore | None = None
        self.hindcast: FakeHindcastStore | None = None
        self.skill: FakeSkillStore | None = None

    def wire_model_stores(self) -> None:
        self.model = FakeModelStore()
        self.group = FakeStationGroupStore()
        self.artifact = FakeModelArtifactStore(group_store=self.group)
        self.hindcast = FakeHindcastStore()
        self.skill = FakeSkillStore()


def _run(
    s: _Stores,
    stations: list,
    basins: list,
    obs_by_station: dict,
    forcing_by_station: dict,
    *,
    start_utc: UtcDatetime = _START,
    end_utc: UtcDatetime = _END,
    forcing_source: object = None,
    deployment_config: object = None,
):
    return _run_onboarding(
        stations=stations,
        basins=basins,
        obs_by_station=obs_by_station,
        forcing_by_station=forcing_by_station,
        basin_store=s.basin,
        station_store=s.station,
        obs_store=s.obs,
        forcing_store=s.forcing,
        baseline_store=s.baseline,
        flow_regime_store=s.regime,
        qc_rules=_TEST_RULES,
        clock=_fixed_clock,
        start_utc=start_utc,
        end_utc=end_utc,
        model_store=s.model,
        artifact_store=s.artifact,
        group_store=s.group,
        hindcast_store=s.hindcast,
        skill_store=s.skill,
        forcing_source=forcing_source,  # type: ignore[arg-type]
        deployment_config=deployment_config,  # type: ignore[arg-type]
    )


class TestHappyPath:
    def test_happy_path(self) -> None:
        sid1 = StationId(uuid4())
        sid2 = StationId(uuid4())
        basin1 = _make_basin("B001")
        basin2 = _make_basin("B002")
        station1 = make_station_config(station_id=sid1, code="B001")
        station2 = make_station_config(station_id=sid2, code="B002")
        s = _Stores()

        result = _run(
            s,
            stations=[station1, station2],
            basins=[basin1, basin2],
            obs_by_station={
                sid1: _make_raw_obs(sid1, 100),
                sid2: _make_raw_obs(sid2, 100),
            },
            forcing_by_station={
                sid1: _make_forcing(sid1, 100),
                sid2: _make_forcing(sid2, 100),
            },
        )

        assert result.stations_created == 2
        assert result.stations_skipped == 0
        assert result.basins_created == 2
        assert result.basins_skipped == 0
        assert result.observations_imported == 200
        assert result.forcing_records_imported == 200
        assert result.errors == []
        assert result.observations_qc_passed == 200
        assert result.observations_qc_failed == 0
        assert result.observations_qc_suspect == 0


class TestDedupExistingStation:
    def test_dedup_existing_station(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="EXISTING")
        basin = _make_basin("EXISTING")
        s = _Stores()
        s.station.store_station(station)  # pre-populate

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 10)},
            forcing_by_station={sid: _make_forcing(sid, 10)},
        )

        assert result.stations_created == 0
        assert result.stations_skipped == 1
        assert len(s.station.fetch_all_stations()) == 1


class TestDedupExistingBasin:
    def test_dedup_existing_basin(self) -> None:
        sid = StationId(uuid4())
        basin = _make_basin("BASINDUP")
        station = make_station_config(station_id=sid, code="BASINDUP")
        s = _Stores()
        s.basin.store_basin(basin)  # pre-populate

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 10)},
            forcing_by_station={sid: _make_forcing(sid, 10)},
        )

        assert result.basins_created == 0
        assert result.basins_skipped == 1
        assert len(s.basin.fetch_all_basins()) == 1


class TestQcRunsAndUpdates:
    def test_qc_runs_and_updates(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="QC001")
        basin = _make_basin("QC001")
        # Include one obs that fails range check (value > 10000)
        bad_obs = RawObservation(
            station_id=sid,
            timestamp=ensure_utc(
                datetime.fromtimestamp(_EPOCH.timestamp() + 50 * 86400, tz=UTC)
            ),
            parameter="discharge",
            value=99999.0,
            source=ObservationSource.MANUAL_IMPORT,
        )
        all_obs = _make_raw_obs(sid, 50) + [bad_obs]
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: all_obs},
            forcing_by_station={sid: _make_forcing(sid, 10)},
        )

        assert result.observations_qc_failed >= 1
        assert result.observations_qc_passed == 50
        # No obs should remain RAW after QC
        stored = s.obs.fetch_observations(sid, "discharge", _START, _END)
        raw_count = sum(1 for o in stored if o.qc_status == QcStatus.RAW)
        assert raw_count == 0


class TestBaselinesComputed:
    def test_baselines_computed(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="BAS001")
        basin = _make_basin("BAS001")
        # 5 years of daily data to satisfy min_samples=10 across all DOYs
        obs = _make_raw_obs(sid, 5 * 365)
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: obs},
            forcing_by_station={sid: []},
        )

        assert result.baselines_computed > 0
        assert len(s.baseline.fetch_baselines(sid, "discharge")) > 0


class TestFlowRegimesComputed:
    def test_flow_regimes_computed(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="FLW001")
        basin = _make_basin("FLW001")
        # >= 365 observations required for flow regime
        obs = _make_raw_obs(sid, 400)
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: obs},
            forcing_by_station={sid: []},
        )

        assert result.flow_regimes_computed == 1
        stored = s.regime.fetch_latest(sid, "discharge")
        assert stored is not None
        assert stored.station_id == sid


class TestEmptyStations:
    def test_empty_stations(self) -> None:
        s = _Stores()

        result = _run(
            s,
            stations=[],
            basins=[],
            obs_by_station={},
            forcing_by_station={},
        )

        assert result.stations_created == 0
        assert result.stations_skipped == 0
        assert result.basins_created == 0
        assert result.basins_skipped == 0
        assert result.observations_imported == 0
        assert result.forcing_records_imported == 0
        assert result.observations_qc_passed == 0
        assert result.observations_qc_failed == 0
        assert result.observations_qc_suspect == 0
        assert result.baselines_computed == 0
        assert result.flow_regimes_computed == 0
        assert result.errors == []


class TestLakeStationOnboarding:
    def test_lake_station_onboarding(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(
            station_id=sid,
            code="LAKE001",
            station_kind=StationKind.LAKE,
            forecast_targets=frozenset({"water_level"}),
            measured_parameters=frozenset({"water_level"}),
        )
        basin = _make_basin("LAKE001")
        obs = _make_raw_waterlevel_obs(sid, 400)
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: obs},
            forcing_by_station={sid: []},
        )

        assert result.stations_created == 1
        assert result.observations_imported == 400
        assert result.errors == []
        # QC ran on water_level observations
        assert result.observations_qc_passed == 400
        assert result.observations_qc_failed == 0
        # No discharge observations were touched
        discharge_obs = s.obs.fetch_observations(sid, "discharge", _START, _END)
        assert discharge_obs == []
        waterlevel_obs = s.obs.fetch_observations(sid, "water_level", _START, _END)
        assert len(waterlevel_obs) == 400
        # Baselines and flow regime computed for water_level
        assert len(s.baseline.fetch_baselines(sid, "water_level")) > 0
        assert len(s.baseline.fetch_baselines(sid, "discharge")) == 0
        regime = s.regime.fetch_latest(sid, "water_level")
        assert regime is not None
        assert regime.station_id == sid


_FAKE_MODEL_ID = ModelId("fake_station_model")


class TestOnboardingSteps6Through8:
    def test_steps6_8_skipped_when_model_store_none(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="S001")
        basin = _make_basin("S001")
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 10)},
            forcing_by_station={sid: _make_forcing(sid, 10)},
        )

        assert result.model_assignments_created == 0
        assert result.models_trained == 0
        assert result.stations_marked_operational == 0
        assert result.errors == []

    def test_weather_stations_marked_operational(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(
            station_id=sid,
            code="W001",
            station_kind=StationKind.WEATHER,
            network="meteoswiss",
            forecast_targets=None,
            measured_parameters=frozenset({"temperature"}),
        )
        basin = _make_basin("W001")
        s = _Stores()
        s.wire_model_stores()

        with patch(
            "sapphire_flow.services.model_registry.discover_models",
            return_value={},
        ):
            result = _run(
                s,
                stations=[station],
                basins=[basin],
                obs_by_station={},
                forcing_by_station={},
                forcing_source=FakeWeatherReanalysisSource(),
                deployment_config=make_deployment_config(),
            )

        assert result.stations_marked_operational == 1
        assert result.model_assignments_created == 0
        assert result.models_trained == 0
        fetched = s.station.fetch_station(sid)
        assert fetched is not None
        assert fetched.station_status == StationStatus.OPERATIONAL

    def test_no_models_registered_no_errors(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="NM001")
        basin = _make_basin("NM001")
        s = _Stores()
        s.wire_model_stores()

        with patch(
            "sapphire_flow.services.model_registry.discover_models",
            return_value={},
        ):
            result = _run(
                s,
                stations=[station],
                basins=[basin],
                obs_by_station={sid: _make_raw_obs(sid, 10)},
                forcing_by_station={sid: _make_forcing(sid, 10)},
                forcing_source=FakeWeatherReanalysisSource(),
                deployment_config=make_deployment_config(),
            )

        assert result.model_assignments_created == 0
        assert result.models_trained == 0
        assert result.errors == []
        # Non-weather station without any active artifact stays non-operational
        assert result.stations_marked_operational == 0

    def test_station_scoped_model_assigned_and_trained(self) -> None:
        from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
        from sapphire_flow.types.station import StationWeatherSource
        from tests.fakes.fake_models import FakeStationForecastModel

        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="TR001")
        basin = _make_basin("TR001")
        s = _Stores()
        s.wire_model_stores()
        assert s.model is not None

        # Seed weather source so training_data can proceed
        weather_source = StationWeatherSource(
            station_id=sid,
            nwp_source="camels_ch",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
        )
        s.station.store_weather_source(weather_source)

        # Build reanalysis forcing with required parameters for the training window
        reanalysis_records = [
            make_raw_historical_forcing(
                station_id=sid,
                parameter=param,
                valid_time=datetime.fromtimestamp(
                    _START.timestamp() + i * 86400, tz=UTC
                ),
                value=float(i % 10),
            )
            for i in range(400)
            for param in ("precipitation", "temperature")
        ]
        forcing_source = FakeWeatherReanalysisSource(records=reanalysis_records)

        fake_model = FakeStationForecastModel()
        discovered = {_FAKE_MODEL_ID: fake_model}

        with patch(
            "sapphire_flow.services.model_registry.discover_models",
            return_value=discovered,
        ):
            result = _run(
                s,
                stations=[station],
                basins=[basin],
                obs_by_station={sid: _make_raw_obs(sid, 400)},
                forcing_by_station={sid: _make_forcing(sid, 10)},
                forcing_source=forcing_source,
                deployment_config=make_deployment_config(),
            )

        assert result.model_assignments_created >= 1
        assignments = s.station.fetch_model_assignments(sid)
        assert any(a.model_id == _FAKE_MODEL_ID for a in assignments)

        assert result.models_trained >= 1
        active = s.artifact.fetch_artifacts_by_status(  # type: ignore[union-attr]
            model_id=_FAKE_MODEL_ID,
            status=ModelArtifactStatus.ACTIVE,
            station_id=sid,
        )
        assert len(active) >= 1

        assert result.stations_marked_operational == 1
        fetched = s.station.fetch_station(sid)
        assert fetched is not None
        assert fetched.station_status == StationStatus.OPERATIONAL

    def test_station_stays_onboarding_without_active_artifact(self) -> None:

        sid = StationId(uuid4())
        station = make_station_config(
            station_id=sid,
            code="NOART001",
            station_status=StationStatus.ONBOARDING,
        )
        basin = _make_basin("NOART001")
        s = _Stores()
        s.wire_model_stores()

        # Wire model stores but do NOT patch discover_models — empty registry
        # means step 6 creates no assignments and step 7 trains nothing.
        with patch(
            "sapphire_flow.services.model_registry.discover_models",
            return_value={},
        ):
            result = _run(
                s,
                stations=[station],
                basins=[basin],
                obs_by_station={sid: _make_raw_obs(sid, 10)},
                forcing_by_station={sid: _make_forcing(sid, 10)},
                forcing_source=FakeWeatherReanalysisSource(),
                deployment_config=make_deployment_config(),
            )

        assert result.stations_marked_operational == 0
        fetched = s.station.fetch_station(sid)
        assert fetched is not None
        # Status should not have been promoted (still whatever it was stored as)
        assert fetched.station_status != StationStatus.OPERATIONAL
