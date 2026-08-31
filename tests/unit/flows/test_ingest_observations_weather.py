"""Plan 217 (M-G1) — weather-station observation ingest: station selection,
eligibility and cursor. Does NOT test precipitation QC rules (M-I4) or a
real DHM adapter (M-G2); see docs/plans/217-weather-station-observation-ingest.md.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import polars as pl
import pytest

from sapphire_flow.adapters.replay.station import ReplayStationAdapter
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.ingest_observations import (
    IngestResult,
    ingest_observations_flow,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import (
    GaugingStatus,
    ObservationSource,
    PipelineCheckType,
    QcStatus,
    StationKind,
    StationStatus,
)
from sapphire_flow.types.observation import (
    HydroScraperBatchResult,
    RawObservation,
    StationFetchOutcome,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2026, 8, 31, 8, 0, tzinfo=UTC))

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
    parameter: str,
    value: float = 1.0,
    offset_minutes: int = 0,
) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=ensure_utc(_NOW - timedelta(minutes=offset_minutes)),
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
    )


def _write_replay_fixture(path: Path, rows: list[dict]) -> None:  # type: ignore[type-arg]
    pl.DataFrame(
        rows,
        schema={
            "station_code": pl.Utf8,
            "timestamp": pl.Datetime("us", "UTC"),
            "parameter": pl.Utf8,
            "value": pl.Float64,
            "source": pl.Utf8,
        },
    ).write_parquet(path)


class TestWeatherStationEligibility:
    """T1 — D1 (WEATHER joins the fetch) + D2 (gauging_status gates RIVER/LAKE
    only; WEATHER gates on station_status alone)."""

    def test_ungauged_weather_station_is_eligible_ungauged_river_is_not(self) -> None:
        weather = make_station_config(
            code="WX-1",
            name="Weather 1",
            station_kind=StationKind.WEATHER,
            gauging_status=GaugingStatus.UNGAUGED,
            station_status=StationStatus.OPERATIONAL,
            rng=random.Random(1),
        )
        river = make_station_config(
            code="RV-1",
            name="River 1",
            station_kind=StationKind.RIVER,
            gauging_status=GaugingStatus.UNGAUGED,
            station_status=StationStatus.OPERATIONAL,
            rng=random.Random(2),
        )
        station_store = FakeStationStore()
        station_store.store_station(weather)
        station_store.store_station(river)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert isinstance(result, IngestResult)
        # Only the weather station is eligible: the river station is
        # UNGAUGED and gauging still gates RIVER/LAKE.
        assert result.stations_polled == 1

    def test_river_lake_only_deployment_counts_unchanged(self) -> None:
        """D1/D2 must be a no-op for existing RIVER/LAKE-only deployments."""
        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(3))
        s2 = make_station_config(
            code="LK-1",
            name="Lake 1",
            station_kind=StationKind.LAKE,
            rng=random.Random(4),
        )
        station_store = FakeStationStore()
        station_store.store_station(s1)
        station_store.store_station(s2)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert result.stations_polled == 2


class TestCursorParameterMapping:
    """T2 — D3: the `fetch_latest_timestamp` cursor is keyed by an explicit
    StationKind mapping, not the old LAKE/discharge binary."""

    def test_weather_station_cursor_reads_under_precipitation_not_discharge(
        self,
    ) -> None:
        weather = make_station_config(
            code="WX-2",
            name="Weather 2",
            station_kind=StationKind.WEATHER,
            rng=random.Random(5),
        )
        station_store = FakeStationStore()
        station_store.store_station(weather)

        obs_store = FakeObservationStore()
        # A stale "discharge" watermark that must NOT be read for a weather
        # station, and the real "precipitation" watermark that must be.
        stale_discharge = _make_obs(
            weather.id, "discharge", value=999.0, offset_minutes=500
        )
        real_precip = _make_obs(
            weather.id, "precipitation", value=1.2, offset_minutes=30
        )
        obs_store.store_raw_observations([stale_discharge, real_precip])
        for o in obs_store.observations():
            obs_store.update_qc(o.id, QcStatus.QC_PASSED, [])

        class _RecordingAdapter:
            def __init__(self) -> None:
                self.since: dict[StationId, UtcDatetime] = {}

            def fetch_observations_batch(
                self,
                station_configs: list[StationConfig],
                since: dict[StationId, UtcDatetime],
            ) -> HydroScraperBatchResult:
                self.since = dict(since)
                outcomes = tuple(
                    StationFetchOutcome(
                        station_id=sc.id,
                        observations=(),
                        failure_cause=None,
                        failure_detail=None,
                    )
                    for sc in station_configs
                )
                return HydroScraperBatchResult(outcomes=outcomes)

        adapter = _RecordingAdapter()

        ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=adapter,
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )

        assert adapter.since.get(weather.id) == real_precip.timestamp
        assert adapter.since.get(weather.id) != stale_discharge.timestamp

    def test_unhandled_station_kind_raises_not_defaults(self) -> None:
        try:
            from sapphire_flow.flows.ingest_observations import (
                _cursor_parameter_for_kind,
            )
        except ImportError:
            pytest.fail(
                "_cursor_parameter_for_kind is not implemented yet "
                "(Plan 217 T2 — the explicit StationKind cursor mapping)"
            )

        with pytest.raises(ConfigurationError, match="cursor parameter"):
            _cursor_parameter_for_kind(cast("StationKind", "not-a-real-kind"))


class TestWeatherStationEndToEndReplay:
    """T3 — the replay path: eligibility, cursor, storage and IngestResult
    counts for a weather station. Deliberately does NOT assert any QC-defect
    behaviour (stuck-high, false-zero, etc.) — that is M-I4's, after the
    rule set exists. This only asserts that QC ran and passed because no
    rule currently matches "precipitation" (the honest M-G1 end-state)."""

    def test_weather_observation_stored_under_precipitation_passes_qc_unmatched(
        self, tmp_path: Path
    ) -> None:
        weather = make_station_config(
            code="WX-100",
            name="Weather 100",
            station_kind=StationKind.WEATHER,
            gauging_status=GaugingStatus.UNGAUGED,
            rng=random.Random(9),
        )
        station_store = FakeStationStore()
        station_store.store_station(weather)

        fixture = tmp_path / "weather_obs.parquet"
        _write_replay_fixture(
            fixture,
            [
                {
                    "station_code": "WX-100",
                    "timestamp": _NOW - timedelta(minutes=30),
                    "parameter": "precipitation",
                    "value": 3.4,
                    "source": "measured",
                }
            ],
        )
        adapter = ReplayStationAdapter(fixture, simulated_time=_fixed_clock)
        health_store = FakePipelineHealthStore()
        obs_store = FakeObservationStore()

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=adapter,
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
            pipeline_health_store=health_store,
        )

        assert isinstance(result, IngestResult)
        assert result.stations_polled == 1
        assert result.observations_fetched == 1
        assert result.observations_stored == 1
        assert result.stations_failed == 0

        stored = [o for o in obs_store.observations() if o.station_id == weather.id]
        assert len(stored) == 1
        assert stored[0].parameter == "precipitation"

        # No rule matches "precipitation" yet (M-I4's job) -> passes by
        # default, with no flags raised.
        assert stored[0].qc_status == QcStatus.QC_PASSED
        assert stored[0].qc_flags == []
        assert result.qc_passed == 1
        assert result.qc_failed == 0

        # D8: the fetch health record is written before QC, and still is
        # with a weather station in the mix.
        records = health_store.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)
        assert len(records) == 1
