"""Acceptance tests for the Plan 146 dedicated recap-reanalysis snow ingest flow.

Locks the KEY acceptance criteria from
docs/plans/146-antecedent-snow-reanalysis-channel.md Phase 2b: health-by-EFFECT
via fetch_covered_days, the D5 outcome
classification (subscription_not_found excluded / source_data_missing
partial+total loss / no-horizon-advance / config-auth-raises), the D5
station-resolution reconciliation guard, D2a backfill mechanics, D5a's
ordered production construction (Recap config touched ONLY on the non-empty
path), and boundary validation (item B).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.ingest_recap_reanalysis import (
    DEFAULT_VARIABLES,
    ingest_recap_reanalysis_flow,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    PipelineCheckType,
    PipelineHealthStatus,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import (
    FakeHistoricalForcingStore,
    FakePipelineHealthStore,
    FakeStationStore,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing

_NOW = ensure_utc(datetime(2026, 6, 15, 6, 0, tzinfo=UTC))
_START = ensure_utc(_NOW - timedelta(days=21))
_HRU = "hru_dhm_west_v001"
_HRU_B = "hru_dhm_east_v001"


def _clock() -> UtcDatetime:
    return _NOW


@dataclass(frozen=True, kw_only=True, slots=True)
class _FakeResult:
    rows: list[RawHistoricalForcing]
    unavailable: dict[str, dict[str, str]]
    attempted: dict[str, frozenset[str]]
    resolved: dict[StationId, str]
    skipped: dict[StationId, str]


class _FakeSnowAdapter:
    def __init__(self, result: _FakeResult | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    def fetch_snow_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        variables: list[str] | None = None,
    ) -> _FakeResult:
        self.calls.append(
            {
                "station_configs": station_configs,
                "start": start,
                "end": end,
                "variables": variables,
            }
        )
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _station_store_with_binding(
    *station_ids: StationId, nwp_source: str = "era5_land"
) -> FakeStationStore:
    store = FakeStationStore()
    for sid in station_ids:
        cfg = make_station_config(station_id=sid)
        store.store_station(cfg)
        store.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source=nwp_source,
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
    return store


class TestZeroStationsNoOp:
    def test_completes_with_no_recap_config_touched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Item (vi): no SAPPHIRE_CONFIG / API key present at all — the D5a
        # step-3 benign no-op must never construct the Recap adapter.
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("RECAP_API_KEY", raising=False)
        station_store = FakeStationStore()
        forcing_store = FakeHistoricalForcingStore()
        health_store = FakePipelineHealthStore()

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=None,
            pipeline_health_store=health_store,
            adapter=None,
            clock=_clock,
        )

        assert result.stations_targeted == 0
        assert result.status is PipelineHealthStatus.OK
        records = health_store.fetch_recent(
            check_type=PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.OK


class TestHealthByEffectAdvance:
    def test_ok_when_every_attempted_key_advances(self) -> None:
        sid = StationId(uuid4())
        station_store = _station_store_with_binding(sid)
        forcing_store = FakeHistoricalForcingStore()
        health_store = FakePipelineHealthStore()

        rows = [
            make_raw_historical_forcing(
                station_id=sid,
                source="recap_snow_reanalysis",
                parameter=param,
                valid_time=datetime(2026, 6, 1, tzinfo=UTC),
            )
            for param in DEFAULT_VARIABLES
        ]
        fake_result = _FakeResult(
            rows=rows,
            unavailable={},
            attempted={_HRU: frozenset(DEFAULT_VARIABLES)},
            resolved={sid: _HRU},
            skipped={},
        )
        adapter = _FakeSnowAdapter(fake_result)

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=health_store,
            adapter=adapter,
            clock=_clock,
        )

        assert result.status is PipelineHealthStatus.OK
        assert result.rows_stored == 3
        records = health_store.fetch_recent(
            check_type=PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST
        )
        assert records[-1].status is PipelineHealthStatus.OK

    def test_no_advance_yields_warning_not_ok(self) -> None:
        # Fetch reports rows but the store readback shows NO new covered day
        # (a stuck duplicate re-fetch) — never OK on rows_fetched/stored alone.
        sid = StationId(uuid4())
        station_store = _station_store_with_binding(sid)
        forcing_store = FakeHistoricalForcingStore()
        # Pre-seed the store so "after" looks identical to "before".
        pre_existing = make_raw_historical_forcing(
            station_id=sid,
            source="recap_snow_reanalysis",
            parameter="swe",
            valid_time=datetime(2026, 6, 1, tzinfo=UTC),
        )
        forcing_store.store_forcing([pre_existing])
        health_store = FakePipelineHealthStore()

        fake_result = _FakeResult(
            rows=[],  # nothing new stored this run
            unavailable={},
            attempted={_HRU: frozenset(DEFAULT_VARIABLES)},
            resolved={sid: _HRU},
            skipped={},
        )
        adapter = _FakeSnowAdapter(fake_result)

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=health_store,
            adapter=adapter,
            clock=_clock,
        )

        assert result.status is PipelineHealthStatus.WARNING
        records = health_store.fetch_recent(
            check_type=PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST
        )
        assert records[-1].detail["reason"] == "no_horizon_advance"


class TestOutcomeClassification:
    def test_subscription_not_found_excluded_from_warning(self) -> None:
        sid = StationId(uuid4())
        station_store = _station_store_with_binding(sid)
        forcing_store = FakeHistoricalForcingStore()
        health_store = FakePipelineHealthStore()

        rows = [
            make_raw_historical_forcing(
                station_id=sid,
                source="recap_snow_reanalysis",
                parameter=param,
                valid_time=datetime(2026, 6, 1, tzinfo=UTC),
            )
            for param in ("swe", "snowmelt")
        ]
        fake_result = _FakeResult(
            rows=rows,
            unavailable={_HRU: {"snow_depth": "subscription_not_found"}},
            attempted={_HRU: frozenset(DEFAULT_VARIABLES)},
            resolved={sid: _HRU},
            skipped={},
        )
        adapter = _FakeSnowAdapter(fake_result)

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=health_store,
            adapter=adapter,
            clock=_clock,
        )

        # swe/snowmelt advanced; snow_depth is structurally excluded -> OK.
        assert result.status is PipelineHealthStatus.OK

    def test_source_data_missing_partial_yields_warning(self) -> None:
        sid = StationId(uuid4())
        station_store = _station_store_with_binding(sid)
        forcing_store = FakeHistoricalForcingStore()
        health_store = FakePipelineHealthStore()

        rows = [
            make_raw_historical_forcing(
                station_id=sid,
                source="recap_snow_reanalysis",
                parameter=param,
                valid_time=datetime(2026, 6, 1, tzinfo=UTC),
            )
            for param in ("swe", "snowmelt")
        ]
        fake_result = _FakeResult(
            rows=rows,
            unavailable={_HRU: {"snow_depth": "source_data_missing"}},
            attempted={_HRU: frozenset(DEFAULT_VARIABLES)},
            resolved={sid: _HRU},
            skipped={},
        )
        adapter = _FakeSnowAdapter(fake_result)

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=health_store,
            adapter=adapter,
            clock=_clock,
        )

        assert result.status is PipelineHealthStatus.WARNING

    def test_unanticipated_adapter_error_propagates_uncaught(self) -> None:
        sid = StationId(uuid4())
        station_store = _station_store_with_binding(sid)
        forcing_store = FakeHistoricalForcingStore()
        adapter = _FakeSnowAdapter(RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            ingest_recap_reanalysis_flow(
                station_store=station_store,
                forcing_store=forcing_store,
                gateway_polygon_store=object(),
                pipeline_health_store=None,
                adapter=adapter,
                clock=_clock,
            )


class TestMultiHruPartialStall:
    def test_one_key_advances_one_stalls_yields_named_warning(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        station_store = _station_store_with_binding(sid_a, sid_b)
        forcing_store = FakeHistoricalForcingStore()
        # Pre-seed HRU B's swe so its "after" looks unchanged (stalled).
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=sid_b,
                    source="recap_snow_reanalysis",
                    parameter="swe",
                    valid_time=datetime(2026, 6, 1, tzinfo=UTC),
                )
            ]
        )
        health_store = FakePipelineHealthStore()

        rows = [
            make_raw_historical_forcing(
                station_id=sid_a,
                source="recap_snow_reanalysis",
                parameter="swe",
                valid_time=datetime(2026, 6, 1, tzinfo=UTC),
            )
        ]
        fake_result = _FakeResult(
            rows=rows,
            unavailable={},
            attempted={_HRU: frozenset({"swe"}), _HRU_B: frozenset({"swe"})},
            resolved={sid_a: _HRU, sid_b: _HRU_B},
            skipped={},
        )
        adapter = _FakeSnowAdapter(fake_result)

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=health_store,
            adapter=adapter,
            clock=_clock,
        )

        assert result.status is PipelineHealthStatus.WARNING
        records = health_store.fetch_recent(
            check_type=PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST
        )
        stalled = records[-1].detail["stalled_keys"]
        assert f"{_HRU_B}/swe" in stalled
        assert f"{_HRU}/swe" not in stalled


class TestResolutionReconciliation:
    def test_dropped_station_surfaces_as_warning_naming_reason(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        station_store = _station_store_with_binding(sid_a, sid_b)
        forcing_store = FakeHistoricalForcingStore()
        health_store = FakePipelineHealthStore()

        rows = [
            make_raw_historical_forcing(
                station_id=sid_a,
                source="recap_snow_reanalysis",
                parameter=param,
                valid_time=datetime(2026, 6, 1, tzinfo=UTC),
            )
            for param in DEFAULT_VARIABLES
        ]
        fake_result = _FakeResult(
            rows=rows,
            unavailable={},
            attempted={_HRU: frozenset(DEFAULT_VARIABLES)},
            resolved={sid_a: _HRU},
            skipped={sid_b: "unmapped"},
        )
        adapter = _FakeSnowAdapter(fake_result)

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=health_store,
            adapter=adapter,
            clock=_clock,
        )

        assert result.status is PipelineHealthStatus.WARNING
        records = health_store.fetch_recent(
            check_type=PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST
        )
        detail = records[-1].detail
        assert detail["reason"] == "station_resolution_dropped"
        assert str(sid_b) in detail["dropped_stations"]
        assert detail["skipped"][str(sid_b)] == "unmapped"


class TestD2aBackfill:
    def test_station_ids_subset_scopes_the_run(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        station_store = _station_store_with_binding(sid_a, sid_b)
        forcing_store = FakeHistoricalForcingStore()

        fake_result = _FakeResult(
            rows=[],
            unavailable={},
            attempted={},
            resolved={},
            skipped={},
        )
        adapter = _FakeSnowAdapter(fake_result)

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=None,
            adapter=adapter,
            clock=_clock,
            window_days=730,
            station_ids=[str(sid_b)],
        )

        assert result.stations_targeted == 1
        targeted_configs = adapter.calls[0]["station_configs"]
        assert {cfg.station_id for cfg in targeted_configs} == {sid_b}


class TestProductionConstruction:
    def test_missing_sapphire_config_raises_on_non_empty_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        sid = StationId(uuid4())
        station_store = _station_store_with_binding(sid)
        forcing_store = FakeHistoricalForcingStore()

        with pytest.raises(ConfigurationError, match="SAPPHIRE_CONFIG"):
            ingest_recap_reanalysis_flow(
                station_store=station_store,
                forcing_store=forcing_store,
                gateway_polygon_store=object(),
                pipeline_health_store=None,
                adapter=None,
                clock=_clock,
            )

    def test_missing_gateway_polygon_store_raises_on_non_empty_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_CONFIG", "/nonexistent/config.toml")
        sid = StationId(uuid4())
        station_store = _station_store_with_binding(sid)
        forcing_store = FakeHistoricalForcingStore()

        with pytest.raises(ConfigurationError, match="gateway_polygon_store"):
            ingest_recap_reanalysis_flow(
                station_store=station_store,
                forcing_store=forcing_store,
                gateway_polygon_store=None,
                pipeline_health_store=None,
                adapter=None,
                clock=_clock,
            )

    def test_health_store_none_still_completes(self) -> None:
        sid = StationId(uuid4())
        station_store = _station_store_with_binding(sid)
        forcing_store = FakeHistoricalForcingStore()
        fake_result = _FakeResult(
            rows=[], unavailable={}, attempted={}, resolved={}, skipped={}
        )
        adapter = _FakeSnowAdapter(fake_result)

        result = ingest_recap_reanalysis_flow(
            station_store=station_store,
            forcing_store=forcing_store,
            gateway_polygon_store=object(),
            pipeline_health_store=None,
            adapter=adapter,
            clock=_clock,
        )

        assert result.stations_targeted == 1


class TestBoundaryValidation:
    def test_empty_variables_raises_before_fetch(self) -> None:
        with pytest.raises(ConfigurationError, match="non-empty"):
            ingest_recap_reanalysis_flow(
                station_store=FakeStationStore(),
                forcing_store=FakeHistoricalForcingStore(),
                clock=_clock,
                variables=(),
            )

    def test_unknown_variable_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="unknown"):
            ingest_recap_reanalysis_flow(
                station_store=FakeStationStore(),
                forcing_store=FakeHistoricalForcingStore(),
                clock=_clock,
                variables=("not_a_snow_variable",),
            )

    def test_duplicate_variable_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="duplicate"):
            ingest_recap_reanalysis_flow(
                station_store=FakeStationStore(),
                forcing_store=FakeHistoricalForcingStore(),
                clock=_clock,
                variables=("swe", "swe"),
            )

    def test_non_positive_window_days_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="window_days"):
            ingest_recap_reanalysis_flow(
                station_store=FakeStationStore(),
                forcing_store=FakeHistoricalForcingStore(),
                clock=_clock,
                window_days=0,
            )

    def test_malformed_station_id_raises_before_adapter_construction(self) -> None:
        with pytest.raises(ConfigurationError, match="station_ids"):
            ingest_recap_reanalysis_flow(
                station_store=FakeStationStore(),
                forcing_store=FakeHistoricalForcingStore(),
                clock=_clock,
                station_ids=["not-a-uuid"],
            )
