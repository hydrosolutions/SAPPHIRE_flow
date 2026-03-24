from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.services.onboarding import _run_onboarding
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeFlowRegimeConfigStore,
    FakeHistoricalForcingStore,
    FakeObservationStore,
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


def _run(
    s: _Stores,
    stations: list,
    basins: list,
    obs_by_station: dict,
    forcing_by_station: dict,
    *,
    start_utc: UtcDatetime = _START,
    end_utc: UtcDatetime = _END,
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
        stored = s.regime.fetch_latest(sid)
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
