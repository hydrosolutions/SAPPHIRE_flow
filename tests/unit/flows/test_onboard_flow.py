from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from sapphire_flow.flows.onboard import onboard_stations_flow
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource
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
_START = ensure_utc(datetime(1980, 1, 1, tzinfo=UTC))
_END = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))

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


def _make_raw_obs(station_id: StationId, n: int = 100) -> list[RawObservation]:
    return [
        RawObservation(
            station_id=station_id,
            timestamp=ensure_utc(
                datetime.fromtimestamp(_EPOCH.timestamp() + i * 86400, tz=UTC)
            ),
            parameter="discharge",
            value=float(10 + i % 50),
            source=ObservationSource.MANUAL_IMPORT,
        )
        for i in range(n)
    ]


def _make_forcing(station_id: StationId, n: int = 100) -> list:
    return [
        make_raw_historical_forcing(
            station_id=station_id,
            valid_time=datetime.fromtimestamp(_EPOCH.timestamp() + i * 86400, tz=UTC),
            parameter="precipitation",
            value=float(i % 20),
        )
        for i in range(n)
    ]


def _inject_stores() -> dict:
    return {
        "basin_store": FakeBasinStore(),
        "station_store": FakeStationStore(),
        "obs_store": FakeObservationStore(),
        "forcing_store": FakeHistoricalForcingStore(),
        "baseline_store": FakeClimBaselineStore(),
        "flow_regime_store": FakeFlowRegimeConfigStore(),
    }


class TestOnboardFlowWithFakes:
    def test_onboard_flow_with_fakes(self) -> None:
        sid1 = StationId(uuid4())
        sid2 = StationId(uuid4())
        station1 = make_station_config(station_id=sid1, code="B001")
        station2 = make_station_config(station_id=sid2, code="B002")
        basin1 = _make_basin("B001")
        basin2 = _make_basin("B002")

        obs_by_station = {
            sid1: _make_raw_obs(sid1, 100),
            sid2: _make_raw_obs(sid2, 100),
        }
        forcing_by_station = {
            sid1: _make_forcing(sid1, 100),
            sid2: _make_forcing(sid2, 100),
        }

        stores = _inject_stores()

        with (
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_stations",
                return_value=([station1, station2], [basin1, basin2]),
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_observations",
                return_value=obs_by_station,
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_forcing",
                return_value=forcing_by_station,
            ),
        ):
            result = onboard_stations_flow(
                data_dir="./data/CAMELS_CH",
                qc_rules=_TEST_RULES,
                clock=_fixed_clock,
                **stores,
            )

        assert result.stations_created == 2
        assert result.stations_skipped == 0
        assert result.basins_created == 2
        assert result.basins_skipped == 0
        assert result.observations_imported == 200
        assert result.forcing_records_imported == 200
        assert result.errors == []
        assert result.observations_qc_passed == 200

    def test_onboard_flow_no_data(self) -> None:
        stores = _inject_stores()

        with (
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_stations",
                return_value=([], []),
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_observations",
                return_value={},
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_forcing",
                return_value={},
            ),
        ):
            result = onboard_stations_flow(
                data_dir="./data/CAMELS_CH",
                qc_rules=_TEST_RULES,
                clock=_fixed_clock,
                **stores,
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

    def test_onboard_flow_skips_existing_station(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="EXISTING")
        basin = _make_basin("EXISTING")

        stores = _inject_stores()
        stores["station_store"].store_station(station)

        with (
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_stations",
                return_value=([station], [basin]),
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_observations",
                return_value={sid: _make_raw_obs(sid, 10)},
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_forcing",
                return_value={sid: _make_forcing(sid, 10)},
            ),
        ):
            result = onboard_stations_flow(
                data_dir="./data/CAMELS_CH",
                qc_rules=_TEST_RULES,
                clock=_fixed_clock,
                **stores,
            )

        assert result.stations_created == 0
        assert result.stations_skipped == 1
        assert result.errors == []


class TestDataDirResolution:
    def test_empty_data_dir_triggers_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolved_root = tmp_path / "resolved"
        resolved_root.mkdir()
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="R001")
        basin = _make_basin("R001")
        stores = _inject_stores()

        with (
            patch(
                "sapphire_flow.flows.onboard._resolve_default_camels_dir",
                return_value=str(resolved_root / "raw" / "CAMELS_CH"),
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_stations",
                return_value=([station], [basin]),
            ) as mock_load_stations,
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_observations",
                return_value={sid: _make_raw_obs(sid, 10)},
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_forcing",
                return_value={sid: _make_forcing(sid, 10)},
            ),
        ):
            result = onboard_stations_flow(
                data_dir="",
                qc_rules=_TEST_RULES,
                clock=_fixed_clock,
                **stores,
            )

        assert result.stations_created == 1
        call_args = mock_load_stations.call_args
        # data_dir may be passed positionally or as kwarg
        actual_data_dir = call_args[1].get("data_dir") or call_args[0][0]
        assert actual_data_dir == Path(str(resolved_root / "raw" / "CAMELS_CH"))

    def test_explicit_data_dir_bypasses_resolution(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="E001")
        basin = _make_basin("E001")
        stores = _inject_stores()

        with (
            patch(
                "sapphire_flow.flows.onboard._resolve_default_camels_dir",
                side_effect=AssertionError("should not be called"),
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_stations",
                return_value=([station], [basin]),
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_observations",
                return_value={sid: _make_raw_obs(sid, 10)},
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_forcing",
                return_value={sid: _make_forcing(sid, 10)},
            ),
        ):
            result = onboard_stations_flow(
                data_dir="./data/CAMELS_CH",
                qc_rules=_TEST_RULES,
                clock=_fixed_clock,
                **stores,
            )

        assert result.stations_created == 1

    def test_download_true_with_resolved_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolved_path = str(tmp_path / "raw" / "CAMELS_CH")
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="D001")
        basin = _make_basin("D001")
        stores = _inject_stores()

        with (
            patch(
                "sapphire_flow.flows.onboard._resolve_default_camels_dir",
                return_value=resolved_path,
            ),
            patch(
                "sapphire_flow.flows.onboard._download_task",
                return_value=resolved_path,
            ) as mock_download,
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_stations",
                return_value=([station], [basin]),
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_observations",
                return_value={sid: _make_raw_obs(sid, 10)},
            ),
            patch(
                "sapphire_flow.adapters.camelsch_adapter.load_forcing",
                return_value={sid: _make_forcing(sid, 10)},
            ),
        ):
            result = onboard_stations_flow(
                data_dir="",
                download=True,
                qc_rules=_TEST_RULES,
                clock=_fixed_clock,
                **stores,
            )

        mock_download.assert_called_once_with(resolved_path)
        assert result.stations_created == 1
