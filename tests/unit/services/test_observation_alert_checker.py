from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sapphire_flow.services.observation_alert_checker import check_observation_alerts
from sapphire_flow.types.alert import Alert
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.enums import (
    AlertSource,
    AlertStatus,
    ObservationSource,
    QcStatus,
    ThresholdSource,
)
from sapphire_flow.types.ids import AlertId, StationId
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeObservationStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2025, 6, 1, 12, 0, tzinfo=UTC))
_OBS_TS = ensure_utc(datetime(2025, 6, 1, 11, 0, tzinfo=UTC))


def _make_sid() -> StationId:
    return StationId(uuid4())


def _make_threshold(
    station_id: StationId,
    danger_level: str,
    parameter: str,
    value: float,
) -> StationThreshold:
    return StationThreshold(
        station_id=station_id,
        danger_level=danger_level,
        parameter=parameter,
        value=value,
        source=ThresholdSource.AUTHORITY,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _store_qc_obs(
    obs_store: FakeObservationStore,
    station_id: StationId,
    parameter: str,
    value: float,
) -> None:
    raw = RawObservation(
        station_id=station_id,
        timestamp=_OBS_TS,
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
    )
    obs_store.store_raw_observations([raw])
    oid = obs_store.observations()[-1].id
    obs_store.update_qc(oid, QcStatus.QC_PASSED, [])


def _seed_active_alert(
    alert_store: FakeAlertStore,
    station_id: StationId,
    alert_level: str,
    trigger_value: float = 150.0,
) -> Alert:
    alert = Alert(
        id=AlertId(uuid4()),
        station_id=station_id,
        source=AlertSource.OBSERVATION,
        alert_level=alert_level,
        status=AlertStatus.RAISED,
        trigger_probability=None,
        trigger_value=trigger_value,
        triggered_at=_NOW,
        acknowledged_at=None,
        acknowledged_by=None,
        resolved_at=None,
        first_detected_at=_NOW,
        notified_at=None,
        created_at=_NOW,
    )
    alert_store.upsert_alert(alert)
    return alert


class TestCheckObservationAlerts:
    def test_alert_raised_when_threshold_exceeded(self) -> None:
        sid = _make_sid()
        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        alert_store = FakeAlertStore()

        station_store.store_station(make_station_config(station_id=sid))
        station_store.store_thresholds(
            [_make_threshold(sid, "yellow", "discharge", 100.0)]
        )
        _store_qc_obs(obs_store, sid, "discharge", 150.0)

        check_observation_alerts(
            {(sid, "discharge")}, obs_store, station_store, alert_store, _NOW
        )

        alerts = alert_store.fetch_active_alerts(
            station_id=sid, source=AlertSource.OBSERVATION
        )
        assert len(alerts) == 1
        assert alerts[0].status == AlertStatus.RAISED
        assert alerts[0].source == AlertSource.OBSERVATION
        assert alerts[0].alert_level == "yellow"
        assert alerts[0].trigger_value == 150.0

    def test_no_alert_when_below_threshold(self) -> None:
        sid = _make_sid()
        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        alert_store = FakeAlertStore()

        station_store.store_station(make_station_config(station_id=sid))
        station_store.store_thresholds(
            [_make_threshold(sid, "yellow", "discharge", 100.0)]
        )
        _store_qc_obs(obs_store, sid, "discharge", 50.0)

        check_observation_alerts(
            {(sid, "discharge")}, obs_store, station_store, alert_store, _NOW
        )

        alerts = alert_store.fetch_active_alerts(
            station_id=sid, source=AlertSource.OBSERVATION
        )
        assert len(alerts) == 0

    def test_alert_resolved_when_value_drops(self) -> None:
        sid = _make_sid()
        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        alert_store = FakeAlertStore()

        station_store.store_station(make_station_config(station_id=sid))
        station_store.store_thresholds(
            [_make_threshold(sid, "yellow", "discharge", 100.0)]
        )
        _seed_active_alert(alert_store, sid, "yellow", trigger_value=150.0)
        _store_qc_obs(obs_store, sid, "discharge", 50.0)

        check_observation_alerts(
            {(sid, "discharge")}, obs_store, station_store, alert_store, _NOW
        )

        active = alert_store.fetch_active_alerts(
            station_id=sid, source=AlertSource.OBSERVATION
        )
        assert len(active) == 0
        all_alerts = alert_store.alerts()
        assert all(a.status == AlertStatus.RESOLVED for a in all_alerts)

    def test_no_thresholds_no_alerts(self) -> None:
        sid = _make_sid()
        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        alert_store = FakeAlertStore()

        station_store.store_station(make_station_config(station_id=sid))
        _store_qc_obs(obs_store, sid, "discharge", 500.0)

        check_observation_alerts(
            {(sid, "discharge")}, obs_store, station_store, alert_store, _NOW
        )

        alerts = alert_store.fetch_active_alerts()
        assert len(alerts) == 0

    def test_no_qc_passed_observations_skips(self) -> None:
        sid = _make_sid()
        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        alert_store = FakeAlertStore()

        station_store.store_station(make_station_config(station_id=sid))
        station_store.store_thresholds(
            [_make_threshold(sid, "yellow", "discharge", 100.0)]
        )
        raw = RawObservation(
            station_id=sid,
            timestamp=_OBS_TS,
            parameter="discharge",
            value=500.0,
            source=ObservationSource.MEASURED,
        )
        obs_store.store_raw_observations([raw])

        check_observation_alerts(
            {(sid, "discharge")}, obs_store, station_store, alert_store, _NOW
        )

        alerts = alert_store.fetch_active_alerts(
            station_id=sid, source=AlertSource.OBSERVATION
        )
        assert len(alerts) == 0

    def test_multiple_danger_levels(self) -> None:
        sid = _make_sid()
        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        alert_store = FakeAlertStore()

        station_store.store_station(make_station_config(station_id=sid))
        station_store.store_thresholds(
            [
                _make_threshold(sid, "yellow", "discharge", 100.0),
                _make_threshold(sid, "red", "discharge", 200.0),
            ]
        )
        _store_qc_obs(obs_store, sid, "discharge", 250.0)

        check_observation_alerts(
            {(sid, "discharge")}, obs_store, station_store, alert_store, _NOW
        )

        alerts = alert_store.fetch_active_alerts(
            station_id=sid, source=AlertSource.OBSERVATION
        )
        assert len(alerts) == 2
        levels = {a.alert_level for a in alerts}
        assert levels == {"yellow", "red"}

    def test_multi_parameter_no_premature_resolution(self) -> None:
        sid = _make_sid()
        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        alert_store = FakeAlertStore()

        station_store.store_station(make_station_config(station_id=sid))
        station_store.store_thresholds(
            [
                _make_threshold(sid, "yellow", "discharge", 100.0),
                _make_threshold(sid, "yellow", "water_level", 2.0),
            ]
        )
        _store_qc_obs(obs_store, sid, "discharge", 150.0)
        _store_qc_obs(obs_store, sid, "water_level", 1.5)

        check_observation_alerts(
            {(sid, "discharge"), (sid, "water_level")},
            obs_store,
            station_store,
            alert_store,
            _NOW,
        )

        alerts = alert_store.fetch_active_alerts(
            station_id=sid, source=AlertSource.OBSERVATION
        )
        assert len(alerts) == 1
        assert alerts[0].alert_level == "yellow"
        assert alerts[0].status == AlertStatus.RAISED

    def test_resolution_deferred_when_parameter_not_evaluated(self) -> None:
        sid = _make_sid()
        obs_store = FakeObservationStore()
        station_store = FakeStationStore()
        alert_store = FakeAlertStore()

        station_store.store_station(make_station_config(station_id=sid))
        station_store.store_thresholds(
            [
                _make_threshold(sid, "yellow", "discharge", 100.0),
                _make_threshold(sid, "yellow", "water_level", 2.0),
            ]
        )
        _seed_active_alert(alert_store, sid, "yellow", trigger_value=150.0)
        _store_qc_obs(obs_store, sid, "discharge", 50.0)

        check_observation_alerts(
            {(sid, "discharge")}, obs_store, station_store, alert_store, _NOW
        )

        active = alert_store.fetch_active_alerts(
            station_id=sid, source=AlertSource.OBSERVATION
        )
        assert len(active) == 1
        assert active[0].status == AlertStatus.RAISED
