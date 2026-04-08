from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sapphire_flow.flows.ingest_observations import (
    IngestResult,
    ingest_observations_flow,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.observation import RawObservation

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
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
        for o in list(obs_store._observations.values()):
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
        stored = list(obs_store._observations.values())[0]
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
        stored = list(obs_store._observations.values())[0]
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
        stored_obs = list(obs_store._observations.values())[0]
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
