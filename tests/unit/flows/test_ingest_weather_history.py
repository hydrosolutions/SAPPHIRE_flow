"""LOCKED acceptance tests for the Plan-071 weather-history rolling-ingest flow.

Design choices pinned by these tests (see the task summary for rationale):

* **Flow**: a single rolling-ingest flow ``ingest_weather_history_flow`` at
  ``sapphire_flow/flows/ingest_weather_history.py`` (collapses the plan's
  catchup + daily-append flows into one rolling-ingest flow deployed daily on
  branch ``fix/071-ingest-flow``).
* **Window**: ``[clock() - 60 days, clock()]`` — the rolling 60-day window
  ending at the injected clock's "now".
* **Canonical parameters**: ``{"precipitation", "temperature",
  "temperature_min", "temperature_max"}`` (the four MeteoSwiss daily products).
* **Station-config sourcing**: from the injected ``station_store``; the flow
  filters station weather-sources to those whose ``nwp_source`` matches the
  adapter's ``NWP_SOURCE`` and passes only those to the adapter. No reanalysis-
  bound sources => no fetch, no write.
* **Idempotency**: persistence relies on the merged ``HistoricalForcingStore``
  content-hash ``version`` supersession — a republished day (same logical key,
  new version) reads back as a single latest-version row.

These tests fail-first: the flow module does not exist yet, so collection
raises ``ModuleNotFoundError`` until the implementer creates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

import pytest
from shapely.geometry import box

from sapphire_flow.adapters.meteoswiss_open_data_reanalysis import (
    MeteoSwissOpenDataReanalysisAdapter,
)
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.ingest_weather_history import (
    _DEFAULT_REANALYSIS_STAC_BASE_URL,
    _DEFAULT_REANALYSIS_STAC_COLLECTION,
    _load_reanalysis_stac_config,
    _ReanalysisStacConfig,
    build_production_reanalysis_adapter,
    ingest_weather_history_flow,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.historical_forcing import (
    HistoricalForcingRecord,
    RawHistoricalForcing,
)
from sapphire_flow.types.ids import BasinId, HistoricalForcingId
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import FakeBasinStore, FakeStationStore

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId

# ---------------------------------------------------------------------------
# Constants — known-answer fixtures
# ---------------------------------------------------------------------------

_NOW = ensure_utc(datetime(2026, 6, 15, 6, 0, tzinfo=UTC))
_START = ensure_utc(_NOW - timedelta(days=60))  # 2026-04-16T06:00Z
# A valid-time that falls inside the rolling [_START, _NOW) window.
_DAY = ensure_utc(datetime(2026, 6, 14, 0, 0, tzinfo=UTC))

_REANALYSIS_SOURCE = "meteoswiss_open_data_reanalysis"

_CANONICAL_PARAMS = {
    "precipitation",
    "temperature",
    "temperature_min",
    "temperature_max",
}

_REANALYSIS_SOURCE_TAGS = {
    ForcingSource.METEOSWISS_RPRELIMD.value,
    ForcingSource.METEOSWISS_TABSD.value,
    ForcingSource.METEOSWISS_TMIND.value,
    ForcingSource.METEOSWISS_TMAXD.value,
}


def _fixed_clock() -> UtcDatetime:
    return _NOW


# ---------------------------------------------------------------------------
# Fakes — deterministic, fakes-over-mocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class _AdapterCall:
    station_configs: list[StationWeatherSource]
    start: UtcDatetime
    end: UtcDatetime
    parameters: list[str]


class _SpyReanalysisAdapter:
    """Fake ``WeatherReanalysisSource`` recording every ``fetch_reanalysis``
    call and replaying pre-canned ``RawHistoricalForcing`` responses.

    Exposes ``NWP_SOURCE`` so the flow can filter station weather-sources by
    the adapter's source identity (mirrors the real adapter ClassVar).
    """

    NWP_SOURCE: ClassVar[str] = _REANALYSIS_SOURCE

    def __init__(self, responses: list[list[RawHistoricalForcing]]) -> None:
        self._responses = responses
        self.calls: list[_AdapterCall] = []

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        idx = len(self.calls)
        self.calls.append(
            _AdapterCall(
                station_configs=list(station_configs),
                start=start,
                end=end,
                parameters=list(parameters),
            )
        )
        if not self._responses:
            return []
        return self._responses[min(idx, len(self._responses) - 1)]


class _SupersedingForcingStore:
    """In-test ``HistoricalForcingStore`` mirroring ``PgHistoricalForcingStore``
    supersession semantics: ``store_forcing`` appends; ``fetch_forcing`` without
    an explicit ``version`` collapses to the latest version per logical key
    (insertion order == ``created_at`` proxy)."""

    def __init__(self) -> None:
        self._rows: list[tuple[int, HistoricalForcingRecord]] = []
        self._seq = 0

    @property
    def records(self) -> list[HistoricalForcingRecord]:
        return [r for _, r in self._rows]

    def store_forcing(self, records: list[RawHistoricalForcing]) -> None:
        for raw in records:
            self._seq += 1
            record = HistoricalForcingRecord(
                id=HistoricalForcingId(uuid4()),
                station_id=raw.station_id,
                source=raw.source,
                version=raw.version,
                valid_time=raw.valid_time,
                parameter=raw.parameter,
                spatial_type=raw.spatial_type,
                band_id=raw.band_id,
                member_id=raw.member_id,
                value=raw.value,
                created_at=raw.valid_time,
            )
            self._rows.append((self._seq, record))

    def fetch_forcing(
        self,
        station_id: StationId,
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str] | None = None,
        version: str | None = None,
        member_id: int | None = None,
    ) -> list[HistoricalForcingRecord]:
        matched = [
            (seq, r)
            for seq, r in self._rows
            if r.station_id == station_id
            and r.source == source
            and start <= r.valid_time < end
            and (parameters is None or r.parameter in parameters)
            and (member_id is None or r.member_id == member_id)
        ]
        if version is not None:
            return [r for _, r in matched if r.version == version]
        latest: dict[tuple[object, ...], tuple[int, HistoricalForcingRecord]] = {}
        for seq, r in matched:
            key = (
                r.station_id,
                r.source,
                r.valid_time,
                r.parameter,
                r.spatial_type,
                r.band_id,
                r.member_id,
            )
            if key not in latest or seq > latest[key][0]:
                latest[key] = (seq, r)
        return [r for _, r in latest.values()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _weather_source(
    station_id: StationId,
    *,
    nwp_source: str = _REANALYSIS_SOURCE,
    status: WeatherSourceStatus = WeatherSourceStatus.ACTIVE,
) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source=nwp_source,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=status,
    )


def _row(
    station_id: StationId,
    *,
    parameter: str,
    source: str,
    value: float,
    version: str,
    valid_time: UtcDatetime = _DAY,
) -> RawHistoricalForcing:
    return make_raw_historical_forcing(
        station_id=station_id,
        source=source,
        version=version,
        valid_time=valid_time,
        parameter=parameter,
        value=value,
    )


def _project(records: list[HistoricalForcingRecord]) -> set[tuple[object, ...]]:
    return {
        (r.station_id, r.source, r.valid_time, r.parameter, r.value, r.version)
        for r in records
    }


def _project_raw(rows: list[RawHistoricalForcing]) -> set[tuple[object, ...]]:
    return {
        (r.station_id, r.source, r.valid_time, r.parameter, r.value, r.version)
        for r in rows
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIngestWeatherHistoryFlow:
    def test_fetches_rolling_60_day_window_and_persists(self) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        rows = [
            _row(
                station.id,
                parameter="precipitation",
                source=ForcingSource.METEOSWISS_RPRELIMD.value,
                value=12.5,
                version="v1",
            ),
            _row(
                station.id,
                parameter="temperature",
                source=ForcingSource.METEOSWISS_TABSD.value,
                value=9.0,
                version="v1",
            ),
        ]
        adapter = _SpyReanalysisAdapter([rows])
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        # Exactly one fetch over the rolling 60-day window ending at now.
        assert len(adapter.calls) == 1
        call = adapter.calls[0]
        assert call.start == _START
        assert call.end == _NOW
        assert set(call.parameters) == _CANONICAL_PARAMS

        # The adapter's rows were persisted verbatim (known-answer).
        assert _project(store.records) == _project_raw(rows)

    def test_targets_only_reanalysis_bound_stations(self) -> None:
        bound = make_station_config(code="2135", name="Aare Bern")
        other = make_station_config(code="2289", name="Rhein Basel")
        station_store = FakeStationStore()
        station_store.store_station(bound)
        station_store.store_station(other)
        station_store.store_weather_source(_weather_source(bound.id))
        # A different NWP source must NOT be routed to the reanalysis adapter.
        station_store.store_weather_source(
            _weather_source(other.id, nwp_source="icon_ch2_eps")
        )

        adapter = _SpyReanalysisAdapter(
            [
                [
                    _row(
                        bound.id,
                        parameter="precipitation",
                        source=ForcingSource.METEOSWISS_RPRELIMD.value,
                        value=3.0,
                        version="v1",
                    )
                ]
            ]
        )
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert len(adapter.calls) == 1
        config_station_ids = {c.station_id for c in adapter.calls[0].station_configs}
        assert config_station_ids == {bound.id}

    def test_no_op_when_no_reanalysis_bound_stations(self) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(
            _weather_source(station.id, nwp_source="icon_ch2_eps")
        )

        adapter = _SpyReanalysisAdapter([])
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        # No reanalysis-bound source => no fetch, no write.
        assert adapter.calls == []
        assert store.records == []

    def test_no_op_when_no_stations(self) -> None:
        adapter = _SpyReanalysisAdapter([])
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=FakeStationStore(),
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert adapter.calls == []
        assert store.records == []

    def test_republished_day_supersedes_to_latest_version(self) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        # Same logical key (station, day, parameter, source), new content hash.
        v1 = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=10.0,
            version="hash-v1",
        )
        v2 = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=11.0,
            version="hash-v2",
        )
        adapter = _SpyReanalysisAdapter([[v1], [v2]])
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )
        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        read = store.fetch_forcing(
            station.id,
            ForcingSource.METEOSWISS_RPRELIMD.value,
            _START,
            _NOW,
        )
        # No duplicate logical rows; latest version wins.
        assert len(read) == 1
        assert read[0].version == "hash-v2"
        assert read[0].value == 11.0

    def test_identical_rerun_is_idempotent(self) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        row = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=10.0,
            version="hash-v1",
        )
        adapter = _SpyReanalysisAdapter([[row]])
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )
        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        read = store.fetch_forcing(
            station.id,
            ForcingSource.METEOSWISS_RPRELIMD.value,
            _START,
            _NOW,
        )
        assert len(read) == 1
        assert read[0].version == "hash-v1"

    def test_persists_only_reanalysis_tagged_basin_average_rows(self) -> None:
        # Reanalysis history must land in historical_forcing only — every
        # persisted row carries a reanalysis source tag and BASIN_AVERAGE
        # representation (the flow has no weather_forecasts sink).
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        rows = [
            _row(
                station.id,
                parameter="precipitation",
                source=ForcingSource.METEOSWISS_RPRELIMD.value,
                value=2.0,
                version="v1",
            ),
            _row(
                station.id,
                parameter="temperature_min",
                source=ForcingSource.METEOSWISS_TMIND.value,
                value=1.0,
                version="v1",
            ),
        ]
        adapter = _SpyReanalysisAdapter([rows])
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert store.records, "expected reanalysis rows to be persisted"
        for record in store.records:
            assert record.source in _REANALYSIS_SOURCE_TAGS
            assert record.spatial_type == SpatialRepresentation.BASIN_AVERAGE


# ---------------------------------------------------------------------------
# Production adapter factory + production (adapter=None) path
# ---------------------------------------------------------------------------


def _make_basin() -> Basin:
    return Basin(
        id=BasinId(uuid4()),
        code="test_basin",
        name="Test Basin",
        geometry=box(6.0, 46.0, 10.0, 48.0),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        network="test",
    )


class TestBuildProductionReanalysisAdapter:
    def test_constructs_adapter_from_config_and_stores_without_network(self) -> None:
        basin = _make_basin()
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        station = make_station_config(code="2135", name="Aare Bern", basin_id=basin.id)
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        config = _ReanalysisStacConfig(
            stac_base_url=_DEFAULT_REANALYSIS_STAC_BASE_URL,
            stac_collection=_DEFAULT_REANALYSIS_STAC_COLLECTION,
        )

        # Construction must not touch the network (the httpx.Client is only used
        # at fetch_reanalysis time).
        adapter = build_production_reanalysis_adapter(
            config=config,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
        )

        assert isinstance(adapter, MeteoSwissOpenDataReanalysisAdapter)
        assert adapter.NWP_SOURCE == _REANALYSIS_SOURCE
        assert adapter._stac_collection == _DEFAULT_REANALYSIS_STAC_COLLECTION
        # The basin for the station carrying a basin_id was wired in.
        assert station.id in adapter._basins

    def test_default_stac_config_when_no_config_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        config = _load_reanalysis_stac_config()
        assert config.stac_base_url == _DEFAULT_REANALYSIS_STAC_BASE_URL
        assert config.stac_collection == _DEFAULT_REANALYSIS_STAC_COLLECTION


class TestIngestWeatherHistoryProductionPath:
    def test_production_path_builds_adapter_and_reaches_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # adapter=None is the scheduled/production path: it must NOT raise, and
        # must construct (via the factory) and reach the adapter's fetch.
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)

        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))
        store = _SupersedingForcingStore()

        spy = _SpyReanalysisAdapter(
            [
                [
                    _row(
                        station.id,
                        parameter="precipitation",
                        source=ForcingSource.METEOSWISS_RPRELIMD.value,
                        value=1.0,
                        version="v1",
                    )
                ]
            ]
        )
        captured: dict[str, _ReanalysisStacConfig] = {}

        def _fake_factory(
            *,
            config: _ReanalysisStacConfig,
            station_store: object,
            basin_store: object,
            clock: object,
        ) -> _SpyReanalysisAdapter:
            captured["config"] = config
            return spy

        monkeypatch.setattr(
            "sapphire_flow.flows.ingest_weather_history."
            "build_production_reanalysis_adapter",
            _fake_factory,
        )

        result = ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            basin_store=FakeBasinStore(),
            adapter=None,
            clock=_fixed_clock,
        )

        assert len(spy.calls) == 1
        assert result.rows_fetched == 1
        assert captured["config"].stac_collection == _DEFAULT_REANALYSIS_STAC_COLLECTION

    def test_production_path_builds_real_adapter_and_no_ops_without_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The REAL factory builds a REAL adapter (no monkeypatch). With no
        # reanalysis-bound stations the flow stays a no-op — proving the None
        # path constructs a working adapter without raising or hitting network.
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)

        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(
            _weather_source(station.id, nwp_source="icon_ch2_eps")
        )
        store = _SupersedingForcingStore()

        result = ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            basin_store=FakeBasinStore(),
            adapter=None,
            clock=_fixed_clock,
        )

        assert result.stations_targeted == 0
        assert store.records == []

    def test_production_path_without_basin_store_raises_clear_error(self) -> None:
        station_store = FakeStationStore()
        store = _SupersedingForcingStore()

        with pytest.raises(ConfigurationError, match="basin_store"):
            ingest_weather_history_flow(
                station_store=station_store,
                forcing_store=store,
                basin_store=None,
                adapter=None,
                clock=_fixed_clock,
            )
