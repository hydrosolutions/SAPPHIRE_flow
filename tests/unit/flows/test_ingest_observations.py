from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sapphire_flow.flows.ingest_observations import (
    IngestResult,
    _load_adapter_endpoint,
    _run_qc_task,
    ingest_observations_flow,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource, QcStatus, StationKind
from sapphire_flow.types.observation import RawObservation

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeClimBaselineStore,
    FakeObservationStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2026, 4, 8, 14, 20, tzinfo=UTC))

_QC_RULES = QcRuleSet(
    version="test",
    rules=(
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(seconds=600),
            thresholds={"value_min": 0.0, "value_max": 5000.0},
        ),
        QcRuleParams(
            rule_id="rate_of_change",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(seconds=600),
            thresholds={"max_rate": 50.0},
        ),
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="water_level",
            time_step=timedelta(seconds=600),
            thresholds={"value_min": 0.0, "value_max": 3000.0},
        ),
    ),
)

_WATER_LEVEL_DATUM_RULES = QcRuleSet(
    version="test",
    rules=(
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="water_level",
            time_step=timedelta(seconds=600),
            thresholds={"value_min": -2.0, "value_max": 20.0},
        ),
        QcRuleParams(
            rule_id="rate_of_change",
            rule_version="1.0",
            parameter="water_level",
            time_step=timedelta(seconds=600),
            thresholds={"max_rate": 0.5},
        ),
        QcRuleParams(
            rule_id="gross_outlier",
            rule_version="1.0",
            parameter="water_level",
            time_step=timedelta(seconds=600),
            thresholds={"k_sigma": 1.0},
        ),
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(seconds=600),
            thresholds={"value_min": 0.0, "value_max": 5000.0},
        ),
    ),
)


def _fixed_clock() -> UtcDatetime:
    return _NOW


def _make_obs(
    station_id: StationId,
    parameter: str = "discharge",
    value: float = 42.0,
    offset_minutes: int = 0,
) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=ensure_utc(_NOW - timedelta(minutes=offset_minutes)),
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
    )


class TestIngestObservationsFlow:
    def test_happy_path_two_stations(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))
        s2 = make_station_config(code="2289", name="Rhein Basel", rng=random.Random(2))

        station_store = FakeStationStore()
        station_store.store_station(s1)
        station_store.store_station(s2)

        obs_store = FakeObservationStore()
        # Pre-populate with history so QC time_step is inferred as 600s.
        # Values close to incoming data to avoid rate_of_change flags.
        history = {
            (s1.id, "discharge"): 110.0,
            (s1.id, "water_level"): 500.0,
            (s2.id, "discharge"): 775.0,
            (s2.id, "water_level"): 244.0,
        }
        for (sid, param), val in history.items():
            old = _make_obs(sid, param, val, offset_minutes=10)
            obs_store.store_raw_observations([old])
        for o in obs_store.observations():
            obs_store.update_qc(o.id, QcStatus.QC_PASSED, [])

        raw_obs = [
            _make_obs(s1.id, "discharge", 114.0),
            _make_obs(s1.id, "water_level", 502.0),
            _make_obs(s2.id, "discharge", 780.0),
            _make_obs(s2.id, "water_level", 245.0),
        ]

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource(raw_obs),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert isinstance(result, IngestResult)
        assert result.stations_polled == 2
        assert result.observations_fetched == 4
        assert result.observations_stored == 4
        assert result.observations_skipped == 0
        assert result.qc_passed == 4
        assert result.qc_failed == 0
        assert result.qc_suspect == 0
        assert result.stations_failed == 0

    def test_no_stations_returns_empty(self) -> None:
        result = ingest_observations_flow(
            station_store=FakeStationStore(),
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert result.stations_polled == 0
        assert result.observations_fetched == 0
        assert result.observations_stored == 0

    def test_no_new_data_from_adapter(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(s1)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert result.stations_polled == 1
        assert result.observations_fetched == 0
        assert result.observations_stored == 0

    def test_duplicate_observations_skipped(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(s1)

        obs = _make_obs(s1.id, "discharge", 114.0)
        obs_store = FakeObservationStore()
        # Pre-populate with same observation and mark as QC'd
        obs_store.store_raw_observations([obs])
        stored = obs_store.observations()[0]
        obs_store.update_qc(stored.id, QcStatus.QC_PASSED, [])

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([obs]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert result.observations_fetched == 1
        assert result.observations_stored == 0
        assert result.observations_skipped == 1
        # No new RAW obs to QC (existing one already QC'd)
        assert result.qc_passed == 0

    def test_qc_range_failure_flagged(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(s1)
        obs_store = FakeObservationStore()

        # Pre-populate with history so QC time_step is inferred as 600s
        old_obs = _make_obs(s1.id, "discharge", 100.0, offset_minutes=10)
        obs_store.store_raw_observations([old_obs])
        stored = obs_store.observations()[0]
        obs_store.update_qc(stored.id, QcStatus.QC_PASSED, [])

        # Discharge of -10 violates range_check (min=0)
        bad_obs = _make_obs(s1.id, "discharge", -10.0)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([bad_obs]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert result.observations_stored == 1
        assert result.qc_failed == 1
        assert result.qc_passed == 0

    def test_qc_context_window_detects_rate_of_change(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(s1)
        obs_store = FakeObservationStore()

        # Pre-populate with a historical observation 10 minutes ago
        old_obs = _make_obs(s1.id, "discharge", 100.0, offset_minutes=10)
        obs_store.store_raw_observations([old_obs])
        # Mark old obs as QC passed so it's part of the context window but not re-QC'd
        stored_obs = obs_store.observations()[0]
        obs_store.update_qc(stored_obs.id, QcStatus.QC_PASSED, [])

        # New observation with extreme rate of change (jump of 200, max_rate=50)
        new_obs = _make_obs(s1.id, "discharge", 300.0)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([new_obs]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert result.observations_stored == 1
        assert result.qc_suspect == 1  # rate_of_change produces QC_SUSPECT

    def test_water_level_datum_shift_is_applied_to_qc_task(self) -> None:
        station = make_station_config(water_level_datum_masl=260.0)
        obs_store = FakeObservationStore()
        history = _make_obs(station.id, "water_level", 261.0, offset_minutes=10)
        current = _make_obs(station.id, "water_level", 261.2)
        obs_store.store_raw_observations([history])
        stored_history = obs_store.observations()[0]
        obs_store.update_qc(stored_history.id, QcStatus.QC_PASSED, [])
        obs_store.store_raw_observations([current])

        counts = _run_qc_task.fn(
            obs_store,
            FakeClimBaselineStore(),
            station.id,
            "water_level",
            qc_rules=_WATER_LEVEL_DATUM_RULES,
            now=_NOW,
            datum=260.0,
        )

        latest = sorted(obs_store.observations(), key=lambda obs: obs.timestamp)[-1]
        assert counts == {"passed": 1, "failed": 0, "suspect": 0}
        assert latest.value == 261.2
        assert latest.qc_rule_version == "1.1-datum"

    def test_null_water_level_datum_skips_datum_dependent_rules_only(self) -> None:
        station = make_station_config()
        obs_store = FakeObservationStore()
        history = _make_obs(station.id, "water_level", 261.0, offset_minutes=10)
        current = _make_obs(station.id, "water_level", 263.0)
        obs_store.store_raw_observations([history])
        stored_history = obs_store.observations()[0]
        obs_store.update_qc(stored_history.id, QcStatus.QC_PASSED, [])
        obs_store.store_raw_observations([current])

        counts = _run_qc_task.fn(
            obs_store,
            FakeClimBaselineStore(),
            station.id,
            "water_level",
            qc_rules=_WATER_LEVEL_DATUM_RULES,
            now=_NOW,
            datum=None,
        )

        latest = sorted(obs_store.observations(), key=lambda obs: obs.timestamp)[-1]
        assert counts == {"passed": 0, "failed": 0, "suspect": 1}
        assert [flag.rule_id for flag in latest.qc_flags] == ["rate_of_change"]
        assert latest.qc_rule_version == "1.1-datum-skip"

    def test_water_level_datum_is_not_applied_to_discharge(self) -> None:
        station = make_station_config(water_level_datum_masl=260.0)

        def run_discharge_qc(
            datum: float | None,
        ) -> tuple[dict[str, int], QcStatus, list[str]]:
            obs_store = FakeObservationStore()
            history = _make_obs(station.id, "discharge", 100.0, offset_minutes=10)
            current = _make_obs(station.id, "discharge", 101.0)
            obs_store.store_raw_observations([history])
            stored_history = obs_store.observations()[0]
            obs_store.update_qc(stored_history.id, QcStatus.QC_PASSED, [])
            obs_store.store_raw_observations([current])

            counts = _run_qc_task.fn(
                obs_store,
                FakeClimBaselineStore(),
                station.id,
                "discharge",
                qc_rules=_WATER_LEVEL_DATUM_RULES,
                now=_NOW,
                datum=datum,
            )
            latest = sorted(obs_store.observations(), key=lambda obs: obs.timestamp)[-1]
            return counts, latest.qc_status, [flag.rule_id for flag in latest.qc_flags]

        no_datum_result = run_discharge_qc(None)
        datum_result = run_discharge_qc(260.0)

        assert datum_result == no_datum_result
        assert datum_result == (
            {"passed": 1, "failed": 0, "suspect": 0},
            QcStatus.QC_PASSED,
            [],
        )

    def test_ingest_datum_lookup_is_keyed_by_station_and_parameter(self) -> None:
        station = make_station_config(water_level_datum_masl=260.0)
        station_store = FakeStationStore()
        station_store.store_station(station)
        obs_store = FakeObservationStore()
        history = _make_obs(station.id, "discharge", 100.0, offset_minutes=10)
        obs_store.store_raw_observations([history])
        stored_history = obs_store.observations()[0]
        obs_store.update_qc(stored_history.id, QcStatus.QC_PASSED, [])

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([_make_obs(station.id, "discharge", 101.0)]),
            qc_rules=_WATER_LEVEL_DATUM_RULES,
            clock=_fixed_clock,
        )

        latest = sorted(obs_store.observations(), key=lambda obs: obs.timestamp)[-1]
        assert result.qc_passed == 1
        assert result.qc_failed == 0
        assert latest.qc_rule_version == "1.0"
        assert latest.qc_flags == []

    def test_no_baselines_still_runs_range_check(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(s1)

        obs = _make_obs(s1.id, "discharge", 42.0)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),  # empty
            adapter=FakeStationDataSource([obs]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        # Range check still runs, value 42.0 within [0, 5000]
        assert result.qc_passed == 1

    def test_clock_injection_affects_since(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(s1)

        early_clock = lambda: ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))  # noqa: E731
        obs = _make_obs(s1.id, "discharge", 42.0)

        # Flow should run without error with a different clock
        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([obs]),
            qc_rules=_QC_RULES,
            clock=early_clock,
        )

        assert result.stations_polled == 1

    def test_onboarding_stations_excluded(self) -> None:
        from sapphire_flow.types.enums import StationStatus

        s1 = make_station_config(
            code="2135",
            name="Aare Bern",
            station_status=StationStatus.ONBOARDING,
        )
        station_store = FakeStationStore()
        station_store.store_station(s1)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        # Onboarding station excluded from polling
        assert result.stations_polled == 0

    def test_observation_alert_raised_when_enabled(self) -> None:
        from sapphire_flow.config.deployment import DeploymentConfig
        from sapphire_flow.types.domain import StationThreshold
        from sapphire_flow.types.enums import AlertSource, ThresholdSource

        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))
        station_store = FakeStationStore()
        station_store.store_station(s1)
        station_store.store_thresholds(
            [
                StationThreshold(
                    station_id=s1.id,
                    danger_level="yellow",
                    parameter="discharge",
                    value=100.0,
                    source=ThresholdSource.AUTHORITY,
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            ]
        )

        obs_store = FakeObservationStore()
        old_obs = _make_obs(s1.id, "discharge", 100.0, offset_minutes=10)
        obs_store.store_raw_observations([old_obs])
        for o in obs_store.observations():
            obs_store.update_qc(o.id, QcStatus.QC_PASSED, [])

        # offset_minutes=1 so timestamp < now (exclusive upper bound in fetch)
        above_obs = _make_obs(s1.id, "discharge", 150.0, offset_minutes=1)
        alert_store = FakeAlertStore()

        config = DeploymentConfig(
            max_retention_days=600,
            enable_observation_alerts=True,
        )

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            alert_store=alert_store,
            adapter=FakeStationDataSource([above_obs]),
            qc_rules=_QC_RULES,
            deployment_config=config,
            clock=_fixed_clock,
        )

        assert result.observations_stored == 1
        alerts = alert_store.fetch_active_alerts(
            station_id=s1.id, source=AlertSource.OBSERVATION
        )
        assert len(alerts) == 1
        assert alerts[0].alert_level == "yellow"
        assert alerts[0].trigger_value == 150.0

    def test_observation_alerts_disabled_by_default(self) -> None:
        from sapphire_flow.types.domain import StationThreshold
        from sapphire_flow.types.enums import AlertSource, ThresholdSource

        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))
        station_store = FakeStationStore()
        station_store.store_station(s1)
        station_store.store_thresholds(
            [
                StationThreshold(
                    station_id=s1.id,
                    danger_level="yellow",
                    parameter="discharge",
                    value=100.0,
                    source=ThresholdSource.AUTHORITY,
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            ]
        )

        obs_store = FakeObservationStore()
        old_obs = _make_obs(s1.id, "discharge", 100.0, offset_minutes=10)
        obs_store.store_raw_observations([old_obs])
        for o in obs_store.observations():
            obs_store.update_qc(o.id, QcStatus.QC_PASSED, [])

        above_obs = _make_obs(s1.id, "discharge", 150.0, offset_minutes=1)
        alert_store = FakeAlertStore()

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            alert_store=alert_store,
            adapter=FakeStationDataSource([above_obs]),
            qc_rules=_QC_RULES,
            deployment_config=None,
            clock=_fixed_clock,
        )

        assert result.observations_stored == 1
        alerts = alert_store.fetch_active_alerts(
            station_id=s1.id, source=AlertSource.OBSERVATION
        )
        assert len(alerts) == 0

    def test_lake_stations_ingested(self) -> None:
        lake = make_station_config(
            code="9000",
            name="Lake Zurich",
            station_kind=StationKind.LAKE,
            forecast_targets=frozenset({"water_level"}),
            measured_parameters=frozenset({"water_level"}),
            rng=random.Random(10),
        )

        station_store = FakeStationStore()
        station_store.store_station(lake)

        obs_store = FakeObservationStore()
        # Pre-populate history for QC time_step inference
        old = _make_obs(lake.id, "water_level", 400.0, offset_minutes=10)
        obs_store.store_raw_observations([old])
        for o in obs_store.observations():
            obs_store.update_qc(o.id, QcStatus.QC_PASSED, [])

        raw_obs = [_make_obs(lake.id, "water_level", 405.0)]

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource(raw_obs),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert result.stations_polled == 1
        assert result.observations_fetched == 1
        assert result.observations_stored == 1
        assert result.qc_passed == 1

    def test_mixed_river_and_lake_stations(self) -> None:
        river = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))
        lake = make_station_config(
            code="9001",
            name="Lake Thun",
            station_kind=StationKind.LAKE,
            forecast_targets=frozenset({"water_level"}),
            measured_parameters=frozenset({"water_level"}),
            rng=random.Random(11),
        )

        station_store = FakeStationStore()
        station_store.store_station(river)
        station_store.store_station(lake)

        obs_store = FakeObservationStore()
        # Pre-populate history for both stations
        history = {
            (river.id, "discharge"): 110.0,
            (lake.id, "water_level"): 500.0,
        }
        for (sid, param), val in history.items():
            old = _make_obs(sid, param, val, offset_minutes=10)
            obs_store.store_raw_observations([old])
        for o in obs_store.observations():
            obs_store.update_qc(o.id, QcStatus.QC_PASSED, [])

        raw_obs = [
            _make_obs(river.id, "discharge", 115.0),
            _make_obs(lake.id, "water_level", 505.0),
        ]

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource(raw_obs),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert result.stations_polled == 2
        assert result.observations_fetched == 2
        assert result.observations_stored == 2
        assert result.qc_passed == 2
        assert result.qc_failed == 0


class TestLoadAdapterEndpoint:
    def test_default_when_no_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        assert _load_adapter_endpoint() == "https://lindas.admin.ch/query"

    def test_reads_endpoint_from_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[adapters.river_stations]\nendpoint = "https://base.example/query"\n'
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_file))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        assert _load_adapter_endpoint() == "https://base.example/query"

    def test_overlay_patches_endpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[adapters.river_stations]\nendpoint = "https://base.example/query"\n'
        )
        overlay_file = tmp_path / "overlay.toml"
        overlay_file.write_text(
            '[adapters.river_stations]\nendpoint = "https://overlay.example/query"\n'
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_file))
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay_file))

        assert _load_adapter_endpoint() == "https://overlay.example/query"
