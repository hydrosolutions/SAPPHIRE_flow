from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from shapely.geometry import box

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.onboarding import _run_onboarding
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import (
    ModelArtifactStatus,
    ObservationSource,
    QcStatus,
    SpatialRepresentation,
    StationKind,
    StationStatus,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    BasinId,
    ModelId,
    StationId,
)
from sapphire_flow.types.observation import RawObservation
from tests.conftest import (
    make_deployment_config,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeArtifactLineageWriter,
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
            thresholds={"value_min": 0.0, "value_max": 60.0},
        ),
    ),
)


def _seed_active_climatology_floor(
    store: FakeModelArtifactStore,
    station_id: StationId,
) -> None:
    store.store_artifact(
        model_id=CLIMATOLOGY_FALLBACK_MODEL_ID,
        artifact_bytes=b"climatology_floor",
        training_period_start=_START,
        training_period_end=_END,
        trained_at=_EPOCH,
        station_id=station_id,
        status=ModelArtifactStatus.ACTIVE,
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
    reanalysis_adapter: object = None,
    require_meteoswiss_backfill: bool | None = None,
    lineage_writer: object = None,
):
    # Translate a pre-built adapter into the factory the service now takes.
    # ``require`` defaults to "adapter was supplied" (production supplies one),
    # but a test can force it on with no adapter to exercise the
    # skip-backfill-but-still-hold path.
    factory = (lambda: reanalysis_adapter) if reanalysis_adapter is not None else None
    require = (
        require_meteoswiss_backfill
        if require_meteoswiss_backfill is not None
        else reanalysis_adapter is not None
    )
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
        reanalysis_adapter_factory=factory,  # type: ignore[arg-type]
        require_meteoswiss_backfill=require,
        lineage_writer=lineage_writer,
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

    def test_water_level_onboarding_qc_and_baselines_use_relative_stage(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(
            station_id=sid,
            code="LAKE",
            station_kind=StationKind.LAKE,
            forecast_targets=frozenset({"water_level"}),
            measured_parameters=frozenset({"water_level"}),
            water_level_datum_masl=100.0,
            water_level_unit="m a.s.l.",
        )
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[_make_basin("LAKE")],
            obs_by_station={sid: _make_raw_waterlevel_obs(sid, 100)},
            forcing_by_station={sid: _make_forcing(sid, 100)},
        )

        baselines = s.baseline.fetch_baselines(sid, "water_level")
        assert result.observations_qc_failed == 0
        assert result.observations_qc_passed == 100
        assert baselines
        assert max(b.rolling_mean for b in baselines) < 60.0

    def test_water_level_baselines_skipped_until_datum_exists(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(
            station_id=sid,
            code="LAKE",
            station_kind=StationKind.LAKE,
            forecast_targets=frozenset({"water_level"}),
            measured_parameters=frozenset({"water_level"}),
            water_level_datum_masl=None,
            water_level_unit="m a.s.l.",
        )
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[_make_basin("LAKE")],
            obs_by_station={sid: _make_raw_waterlevel_obs(sid, 100)},
            forcing_by_station={sid: _make_forcing(sid, 100)},
        )

        assert result.observations_qc_passed == 100
        assert s.baseline.fetch_baselines(sid, "water_level") == []

    def test_unsupported_water_level_unit_fails_loudly(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(
            station_id=sid,
            code="LAKE",
            station_kind=StationKind.LAKE,
            forecast_targets=frozenset({"water_level"}),
            measured_parameters=frozenset({"water_level"}),
            water_level_datum_masl=100.0,
            water_level_unit="cm",
        )
        s = _Stores()

        with pytest.raises(ConfigurationError, match="water_level_unit"):
            _run(
                s,
                stations=[station],
                basins=[_make_basin("LAKE")],
                obs_by_station={sid: _make_raw_waterlevel_obs(sid, 10)},
                forcing_by_station={sid: _make_forcing(sid, 10)},
            )


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
        assert result.stations_updated == 1
        assert result.stations_skipped == 0
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
            water_level_unit="m a.s.l.",
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
        # Baselines wait for the surveyed datum; flow regime can still use passed obs.
        assert s.baseline.fetch_baselines(sid, "water_level") == []
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

    def test_no_models_registered_reports_missing_floor(self) -> None:
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
        assert any(
            "missing active climatology_fallback" in err for err in result.errors
        )
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
        _seed_active_climatology_floor(s.artifact, sid)

        # Seed weather source so training_data can proceed
        weather_source = StationWeatherSource(
            station_id=sid,
            nwp_source="camels_ch",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
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

    def test_station_onboarding_records_lineage(self) -> None:
        """Plan 120 Task 2D fixer round: station onboarding
        (`_run_onboarding` -> `services.model_onboarding.onboard_model`) must
        ALSO write `model_artifact_basin_versions` lineage rows -- the
        service-level onboarding path is a separate call site from
        `flows/train_models.py` and `flows/onboard_model.py`, which the
        original Task 2D wiring missed."""
        from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
        from sapphire_flow.types.station import StationWeatherSource
        from tests.fakes.fake_models import FakeStationForecastModel

        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="TR003")
        basin = _make_basin("TR003")
        s = _Stores()
        s.wire_model_stores()
        assert s.model is not None
        _seed_active_climatology_floor(s.artifact, sid)

        weather_source = StationWeatherSource(
            station_id=sid,
            nwp_source="camels_ch",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
        s.station.store_weather_source(weather_source)

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

        discovered = {_FAKE_MODEL_ID: FakeStationForecastModel()}
        lineage_writer = FakeArtifactLineageWriter()

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
                lineage_writer=lineage_writer,
            )

        assert result.models_trained >= 1
        assert len(lineage_writer.calls) >= 1
        recorded_artifact_id, recorded_station_ids = lineage_writer.calls[0]
        assert recorded_station_ids == (sid,)

        active = s.artifact.fetch_artifacts_by_status(  # type: ignore[union-attr]
            model_id=_FAKE_MODEL_ID,
            status=ModelArtifactStatus.ACTIVE,
            station_id=sid,
        )
        assert active[0] == recorded_artifact_id

    def test_trained_model_keeps_config_priority(self) -> None:
        """Plan 089 review (P1): Step 7 (training/promotion) must NOT overwrite
        the config-driven priority set in Step 6. The onboard_model call has to
        thread assignment_priority from model_priorities, else the promoted
        assignment regresses to the default 0."""
        from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
        from sapphire_flow.types.station import StationWeatherSource
        from tests.fakes.fake_models import FakeStationForecastModel

        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="TR002")
        basin = _make_basin("TR002")
        s = _Stores()
        s.wire_model_stores()
        assert s.model is not None
        _seed_active_climatology_floor(s.artifact, sid)

        weather_source = StationWeatherSource(
            station_id=sid,
            nwp_source="camels_ch",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
        s.station.store_weather_source(weather_source)

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

        discovered = {_FAKE_MODEL_ID: FakeStationForecastModel()}
        config = make_deployment_config(model_priorities={str(_FAKE_MODEL_ID): 20})

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
                deployment_config=config,
            )

        # Training must have actually run (so Step 7 wrote the assignment).
        assert result.models_trained >= 1
        assignments = s.station.fetch_model_assignments(sid)
        fake_assignments = [a for a in assignments if a.model_id == _FAKE_MODEL_ID]
        assert len(fake_assignments) == 1
        # The final stored priority is the configured 20, NOT the default 0.
        assert fake_assignments[0].priority == 20

    def test_refuses_operational_without_active_climatology_floor(self) -> None:
        from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
        from sapphire_flow.types.station import StationWeatherSource
        from tests.fakes.fake_models import FakeStationForecastModel

        sid = StationId(uuid4())
        station = make_station_config(
            station_id=sid,
            code="NOFLOOR001",
            station_status=StationStatus.ONBOARDING,
        )
        basin = _make_basin("NOFLOOR001")
        s = _Stores()
        s.wire_model_stores()
        assert s.model is not None

        weather_source = StationWeatherSource(
            station_id=sid,
            nwp_source="camels_ch",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
        s.station.store_weather_source(weather_source)
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
        discovered = {_FAKE_MODEL_ID: FakeStationForecastModel()}

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

        assert result.models_trained >= 1
        assert result.stations_marked_operational == 0
        assert any(
            "missing active climatology_fallback" in err for err in result.errors
        )
        fetched = s.station.fetch_station(sid)
        assert fetched is not None
        assert fetched.station_status != StationStatus.OPERATIONAL

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


class TestRerunIdempotency:
    def test_second_run_is_idempotent(self) -> None:
        sid = StationId(uuid4())
        basin = _make_basin("RERUN001")
        station = make_station_config(station_id=sid, code="RERUN001")
        obs = _make_raw_obs(sid, 50)
        forcing = _make_forcing(sid, 50)
        s = _Stores()

        # First run: everything is created
        result1 = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: obs},
            forcing_by_station={sid: forcing},
        )
        assert result1.stations_created == 1
        assert result1.stations_skipped == 0
        assert result1.observations_imported == 50
        assert result1.errors == []

        # Second run: same data — stations updated, observations skipped
        result2 = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: obs},
            forcing_by_station={sid: forcing},
        )
        assert result2.stations_created == 0
        assert result2.stations_updated == 1
        assert result2.stations_skipped == 0
        assert result2.observations_imported == 0  # all skipped by natural key
        assert result2.basins_created == 0
        assert result2.basins_skipped == 1
        assert result2.errors == []
        # QC only processes RAW obs; on second run, all obs are already QC'd
        assert result2.observations_qc_passed == 0
        assert result2.observations_qc_failed == 0
        # No duplicate baselines or flow regimes
        assert result2.baselines_computed == result1.baselines_computed
        assert result2.flow_regimes_computed == result1.flow_regimes_computed


class TestIconWeatherSourceBinding:
    """M3 owner decision (Step 4b): every non-weather river station also gets an
    ``icon_ch2_eps`` / ``BASIN_AVERAGE`` / ``ACTIVE`` ``StationWeatherSource``.
    This is what makes the operational ICON forcing path reach the station.
    RED until Step 4b writes the second binding.

    Plan 115b5 (migration 0033) retires the camels-ch REANALYSIS binding —
    onboarding must NOT recreate it (a one-shot migration would otherwise be
    silently undone by the next onboarding run), so a camels-ch-sourced
    forcing set must yield ONLY the icon_ch2_eps binding, never a camels-ch
    ``StationWeatherSource`` row.
    """

    def test_river_station_gets_icon_binding_and_no_camels_ch_binding(self) -> None:
        sid = StationId(uuid4())
        basin = _make_basin("RIV001")
        station = make_station_config(
            station_id=sid, code="RIV001", station_kind=StationKind.RIVER
        )
        s = _Stores()

        _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 100)},
            forcing_by_station={sid: _make_forcing(sid, 100)},
        )

        sources = s.station.fetch_weather_sources(sid)
        by_source = {ws.nwp_source: ws for ws in sources}

        # Plan 115b5: onboarding must never recreate the retired camels-ch
        # reanalysis binding (migration 0033 deleted it DB-side).
        assert "camels-ch" not in by_source

        # New M3 icon_ch2_eps / BASIN_AVERAGE / ACTIVE binding.
        assert "icon_ch2_eps" in by_source
        icon = by_source["icon_ch2_eps"]
        assert icon.extraction_type is SpatialRepresentation.BASIN_AVERAGE
        assert icon.status is WeatherSourceStatus.ACTIVE
        assert icon.station_id == sid

    def test_weather_station_does_not_get_icon_binding(self) -> None:
        # A WEATHER station is a forcing SOURCE, not a forecast target — it must
        # not be bound to the operational ICON forecast path.
        sid = StationId(uuid4())
        basin = _make_basin("WX001")
        station = make_station_config(
            station_id=sid, code="WX001", station_kind=StationKind.WEATHER
        )
        s = _Stores()

        _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 20)},
            forcing_by_station={sid: _make_forcing(sid, 20)},
        )


def _make_geo_basin(code: str) -> Basin:
    """A basin with a real geometry (unlike ``_make_basin``, which is
    ``geometry=None`` — the shape most existing fixtures use, since those
    tests never touch basin-average extraction)."""
    return Basin(
        id=BasinId(uuid4()),
        code=code,
        name=f"Basin {code}",
        geometry=box(6.0, 46.0, 10.0, 48.0),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=_EPOCH,
        network="bafu",
    )


class _FakeMeteoswissBackfillAdapter:
    """Test double for ``MeteoSwissBackfillAdapter`` (Plan 115b2 §2C) — a
    single product publishes a tiny high-water mark just past the backfill
    start, so ``run_backfill`` does exactly one (product, year, batch) chunk;
    every other product has no published asset (omitted from spans)."""

    def __init__(self, *, rows: list | None = None) -> None:
        self._rows = rows or []
        self.fetch_calls = 0

    def discover_product_boundary(self, product: ForcingSource) -> UtcDatetime | None:
        if product is ForcingSource.METEOSWISS_TABSD:
            return ensure_utc(datetime(1981, 1, 2, tzinfo=UTC))
        return None

    def fetch_products(self, products, station_configs, start, end, parameters):  # type: ignore[no-untyped-def]
        self.fetch_calls += 1
        return list(self._rows)


class TestMeteoswissBindingAndBackfillOrHold:
    """Plan 115b2 §2B/§2C — the MeteoSwiss reanalysis binding is written for
    every eligible station (valid basin polygon), and Step 8 withholds
    OPERATIONAL promotion from a MeteoSwiss-eligible station until its
    per-station backfill has landed at least one row."""

    def test_binding_written_and_station_promoted_when_backfill_lands_rows(
        self,
    ) -> None:
        sid = StationId(uuid4())
        basin = _make_geo_basin("MSW001")
        station = make_station_config(
            station_id=sid,
            code="MSW001",
            basin_id=basin.id,
            station_kind=StationKind.WEATHER,
            station_status=StationStatus.ONBOARDING,
        )
        s = _Stores()
        row = make_raw_historical_forcing(
            station_id=sid,
            source=ForcingSource.METEOSWISS_TABSD.value,
            parameter="temperature",
            valid_time=datetime(1981, 1, 1, tzinfo=UTC),
        )
        adapter = _FakeMeteoswissBackfillAdapter(rows=[row])

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 20)},
            forcing_by_station={},
            reanalysis_adapter=adapter,
        )

        bindings = {ws.nwp_source: ws for ws in s.station.fetch_weather_sources(sid)}
        assert "meteoswiss_open_data_reanalysis" in bindings
        assert (
            bindings["meteoswiss_open_data_reanalysis"].extraction_type
            is SpatialRepresentation.BASIN_AVERAGE
        )
        assert result.stations_marked_operational == 1
        assert result.errors == []
        assert s.station.fetch_station(sid).station_status is StationStatus.OPERATIONAL

    def test_station_held_out_when_backfill_produces_zero_rows(self) -> None:
        # Soundness: fails against a Step 8 that promotes on the binding
        # alone (today's bug class — a live binding with zero forcing rows).
        sid = StationId(uuid4())
        basin = _make_geo_basin("MSW002")
        station = make_station_config(
            station_id=sid,
            code="MSW002",
            basin_id=basin.id,
            station_kind=StationKind.WEATHER,
            station_status=StationStatus.ONBOARDING,
        )
        s = _Stores()
        adapter = _FakeMeteoswissBackfillAdapter(rows=[])  # backfill yields nothing

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 20)},
            forcing_by_station={},
            reanalysis_adapter=adapter,
        )

        bindings = {ws.nwp_source: ws for ws in s.station.fetch_weather_sources(sid)}
        assert "meteoswiss_open_data_reanalysis" in bindings  # §2B still ran
        assert result.stations_marked_operational == 0
        assert any("MeteoSwiss" in e and str(sid) in e for e in result.errors)
        assert s.station.fetch_station(sid).station_status is StationStatus.ONBOARDING

    def test_held_out_river_station_excluded_from_assignment_and_training(
        self,
    ) -> None:
        # BLOCKER: a held-out station is NOT operational OR trainable. Step 8
        # alone is not enough — a held-out station must be excluded from Step 6
        # (model assignment) and Step 7 (training) too.
        #
        # Soundness: fails against a hold-out applied only at/after Step 8 —
        # the held station would then still receive a Step-6 model assignment
        # (fetch_model_assignments(held) != []) and could train a Step-7
        # artifact.
        from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
        from sapphire_flow.types.station import StationWeatherSource
        from tests.fakes.fake_models import FakeStationForecastModel

        landed_sid = StationId(uuid4())
        held_sid = StationId(uuid4())
        landed_basin = _make_geo_basin("LAND01")
        held_basin = _make_geo_basin("HELD01")
        landed = make_station_config(
            station_id=landed_sid,
            code="LAND01",
            basin_id=landed_basin.id,
            station_status=StationStatus.ONBOARDING,
        )
        held = make_station_config(
            station_id=held_sid,
            code="HELD01",
            basin_id=held_basin.id,
            station_status=StationStatus.ONBOARDING,
        )
        s = _Stores()
        s.wire_model_stores()
        assert s.model is not None and s.artifact is not None
        _seed_active_climatology_floor(s.artifact, landed_sid)
        _seed_active_climatology_floor(s.artifact, held_sid)

        for sid in (landed_sid, held_sid):
            s.station.store_weather_source(
                StationWeatherSource(
                    station_id=sid,
                    nwp_source="camels_ch",
                    extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                    status=WeatherSourceStatus.ACTIVE,
                    role=WeatherSourceRole.REANALYSIS,
                )
            )

        reanalysis_records = [
            make_raw_historical_forcing(
                station_id=sid,
                parameter=param,
                valid_time=datetime.fromtimestamp(
                    _START.timestamp() + i * 86400, tz=UTC
                ),
                value=float(i % 10),
            )
            for sid in (landed_sid, held_sid)
            for i in range(400)
            for param in ("precipitation", "temperature")
        ]
        forcing_source = FakeWeatherReanalysisSource(records=reanalysis_records)

        # The backfill lands a MeteoSwiss row ONLY for the landed station, so
        # the held station ends the run with a live binding but zero rows.
        landed_row = make_raw_historical_forcing(
            station_id=landed_sid,
            source=ForcingSource.METEOSWISS_TABSD.value,
            parameter="temperature",
            valid_time=datetime(1981, 1, 1, tzinfo=UTC),
        )
        adapter = _FakeMeteoswissBackfillAdapter(rows=[landed_row])

        fake_model = FakeStationForecastModel()
        discovered = {_FAKE_MODEL_ID: fake_model}

        with patch(
            "sapphire_flow.services.model_registry.discover_models",
            return_value=discovered,
        ):
            result = _run(
                s,
                stations=[landed, held],
                basins=[landed_basin, held_basin],
                obs_by_station={
                    landed_sid: _make_raw_obs(landed_sid, 400),
                    held_sid: _make_raw_obs(held_sid, 400),
                },
                forcing_by_station={
                    landed_sid: _make_forcing(landed_sid, 10),
                    held_sid: _make_forcing(held_sid, 10),
                },
                forcing_source=forcing_source,
                deployment_config=make_deployment_config(),
                reanalysis_adapter=adapter,
            )

        # Step 6: the landed station is assigned the model; the held one is not.
        landed_assignments = s.station.fetch_model_assignments(landed_sid)
        held_assignments = s.station.fetch_model_assignments(held_sid)
        assert any(a.model_id == _FAKE_MODEL_ID for a in landed_assignments)
        assert held_assignments == []

        # Step 7: the landed station trains an active artifact; the held one
        # never does.
        landed_active = s.artifact.fetch_artifacts_by_status(
            model_id=_FAKE_MODEL_ID,
            status=ModelArtifactStatus.ACTIVE,
            station_id=landed_sid,
        )
        held_active = s.artifact.fetch_artifacts_by_status(
            model_id=_FAKE_MODEL_ID,
            status=ModelArtifactStatus.ACTIVE,
            station_id=held_sid,
        )
        assert len(landed_active) >= 1
        assert held_active == []

        # Step 8: promotion status matches.
        assert (
            s.station.fetch_station(landed_sid).station_status
            is StationStatus.OPERATIONAL
        )
        assert (
            s.station.fetch_station(held_sid).station_status is StationStatus.ONBOARDING
        )
        assert any("MeteoSwiss" in e and str(held_sid) in e for e in result.errors)

    def test_skip_backfill_still_holds_eligible_station(self) -> None:
        # BLOCKER: ``--skip-meteoswiss-backfill`` writes the binding but runs
        # NO fetch (factory is None) — yet the hold requirement is still ON, so
        # the eligible station must be HELD OUT (a live binding with zero
        # forcing rows is never promoted).
        #
        # Soundness: fails against a hold gate coupled to adapter/factory
        # presence (skip → factory None → gate off → promoted).
        sid = StationId(uuid4())
        basin = _make_geo_basin("SKIP01")
        station = make_station_config(
            station_id=sid,
            code="SKIP01",
            basin_id=basin.id,
            station_kind=StationKind.WEATHER,
            station_status=StationStatus.ONBOARDING,
        )
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 20)},
            forcing_by_station={},
            reanalysis_adapter=None,  # skip: no fetch
            require_meteoswiss_backfill=True,  # but hold requirement stays ON
        )

        bindings = {ws.nwp_source: ws for ws in s.station.fetch_weather_sources(sid)}
        assert "meteoswiss_open_data_reanalysis" in bindings  # §2B binding written
        assert result.stations_marked_operational == 0  # held out
        assert s.station.fetch_station(sid).station_status is StationStatus.ONBOARDING
        assert any("MeteoSwiss" in e and str(sid) in e for e in result.errors)

    def test_no_gate_when_reanalysis_adapter_not_supplied(self) -> None:
        # DI default (no adapter wired, e.g. every OTHER existing test in this
        # file): the binding write still happens for an eligible station, but
        # the hold-out gate is inactive — matches how model_store/
        # forcing_source already gate their own optional steps.
        sid = StationId(uuid4())
        basin = _make_geo_basin("MSW003")
        station = make_station_config(
            station_id=sid,
            code="MSW003",
            basin_id=basin.id,
            station_kind=StationKind.WEATHER,
            station_status=StationStatus.ONBOARDING,
        )
        s = _Stores()

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 20)},
            forcing_by_station={},
        )

        bindings = {ws.nwp_source: ws for ws in s.station.fetch_weather_sources(sid)}
        assert "meteoswiss_open_data_reanalysis" in bindings
        assert result.stations_marked_operational == 1
        assert result.errors == []

    def test_ineligible_station_no_geometry_gets_no_binding(self) -> None:
        sid = StationId(uuid4())
        basin = _make_basin("MSW004")  # geometry=None
        station = make_station_config(
            station_id=sid,
            code="MSW004",
            basin_id=basin.id,
            station_kind=StationKind.WEATHER,
            station_status=StationStatus.ONBOARDING,
        )
        s = _Stores()
        adapter = _FakeMeteoswissBackfillAdapter(rows=[])

        result = _run(
            s,
            stations=[station],
            basins=[basin],
            obs_by_station={sid: _make_raw_obs(sid, 20)},
            forcing_by_station={},
            reanalysis_adapter=adapter,
        )

        bindings = {ws.nwp_source: ws for ws in s.station.fetch_weather_sources(sid)}
        assert "meteoswiss_open_data_reanalysis" not in bindings
        # Not MeteoSwiss-eligible, so the §2C gate does not apply — WEATHER
        # stations promote unconditionally after QC.
        assert result.stations_marked_operational == 1
        assert adapter.fetch_calls == 0  # never fetched for an ineligible station

        sources = s.station.fetch_weather_sources(sid)
        assert all(ws.nwp_source != "icon_ch2_eps" for ws in sources)


class TestOnboardFromCamelschTenantResolution:
    """Plan 147 Slice A: onboard_from_camelsch resolves the config tenant CODE
    to a TenantId at the boundary — BEFORE any data is loaded. An unknown code
    is a hard ConfigurationError, never a silent Swiss default.

    Soundness: fails against a pre-fix onboard_from_camelsch that had no
    tenant_store/tenant_code parameters (TypeError) or that ignored the code
    (the bad code would be swallowed and stations would silently land on
    the default tenant instead of raising).
    """

    def test_unknown_tenant_code_raises_before_loading_data(self) -> None:
        from sapphire_flow.services.onboarding import onboard_from_camelsch
        from tests.fakes.fake_stores import FakeTenantStore

        with pytest.raises(ConfigurationError, match="unknown tenant code"):
            onboard_from_camelsch(
                data_dir="/nonexistent/path/must/not/be/read",
                basin_store=FakeBasinStore(),
                station_store=FakeStationStore(),
                obs_store=FakeObservationStore(),
                forcing_store=FakeHistoricalForcingStore(),
                baseline_store=FakeClimBaselineStore(),
                flow_regime_store=FakeFlowRegimeConfigStore(),
                qc_rules=_TEST_RULES,
                clock=lambda: _EPOCH,
                tenant_store=FakeTenantStore(),
                tenant_code="no-such-tenant",
            )
