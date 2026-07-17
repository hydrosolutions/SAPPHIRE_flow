"""LOCKED acceptance tests for the Plan-071 weather-history rolling-ingest flow.

Design choices pinned by these tests (see the task summary for rationale):

* **Flow**: a single rolling-ingest flow ``ingest_weather_history_flow`` at
  ``sapphire_flow/flows/ingest_weather_history.py`` (collapses the plan's
  catchup + daily-append flows into one rolling-ingest flow deployed daily on
  branch ``fix/071-ingest-flow``).
* **Window**: ``[clock() - 60 days, clock()]`` — the rolling 60-day window
  ending at the injected clock's "now".
* **Canonical parameters (Plan 115b1 §1A/§1G)**: five —
  ``{"precipitation", "temperature", "temperature_min", "temperature_max",
  "relative_sunshine_duration"}``. Precipitation is split across TWO products
  by the discovered boundary R (§0a/§1D): ``fetch_products([RHIRESD], ...)``
  over ``[start, min(R+1d, now))`` and ``fetch_products([RPRELIMD], ...)`` over
  ``[max(start, R+1d), now)``; the other four parameters go through ONE more
  ``fetch_products`` call (never the parameter-keyed ``fetch_reanalysis``,
  which fails closed on "precipitation" once RhiresD exists — the whole point
  of this rewrite, round-1 blocker).
* **Station-config sourcing**: from the injected ``station_store``; the flow
  filters station weather-sources to those whose ``nwp_source`` matches the
  adapter's ``NWP_SOURCE`` and passes only those to the adapter. No reanalysis-
  bound sources => no fetch, no write (and — self-containment — no STAC call
  at all: R discovery is skipped too).
* **Idempotency**: persistence relies on the merged ``HistoricalForcingStore``
  content-hash ``version`` supersession — a republished day (same logical key,
  new version) reads back as a single latest-version row.
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
from sapphire_flow.types.enums import (
    PipelineCheckType,
    PipelineHealthStatus,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.historical_forcing import (
    HistoricalForcingRecord,
    RawHistoricalForcing,
)
from sapphire_flow.types.ids import BasinId, HistoricalForcingId
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakePipelineHealthStore,
    FakeStationStore,
)

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
    "relative_sunshine_duration",
}

_REANALYSIS_SOURCE_TAGS = {
    ForcingSource.METEOSWISS_RHIRESD.value,
    ForcingSource.METEOSWISS_RPRELIMD.value,
    ForcingSource.METEOSWISS_TABSD.value,
    ForcingSource.METEOSWISS_TMIND.value,
    ForcingSource.METEOSWISS_TMAXD.value,
    ForcingSource.METEOSWISS_SRELD.value,
}


def _fixed_clock() -> UtcDatetime:
    return _NOW


# ---------------------------------------------------------------------------
# Fakes — deterministic, fakes-over-mocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class _ProductsCall:
    products: list[ForcingSource]
    station_configs: list[StationWeatherSource]
    start: UtcDatetime
    end: UtcDatetime
    parameters: list[str]


class _SpyReanalysisAdapter:
    """Fake ``_ReanalysisAdapter`` (Plan 115b1 §1G shape) recording every
    ``fetch_products``/``discover_rhiresd_boundary`` call and replaying
    pre-canned ``RawHistoricalForcing`` responses, keyed by WHICH product set
    was requested (the flow issues up to three distinct calls per run:
    RhiresD-scoped, RprelimD-scoped, and the four non-precip products).

    Exposes ``NWP_SOURCE`` so the flow can filter station weather-sources by
    the adapter's source identity (mirrors the real adapter ClassVar).
    """

    NWP_SOURCE: ClassVar[str] = _REANALYSIS_SOURCE

    def __init__(
        self,
        *,
        rhiresd_rows: list[RawHistoricalForcing] | None = None,
        rprelimd_rows: list[RawHistoricalForcing] | None = None,
        other_rows: list[RawHistoricalForcing] | None = None,
        rhiresd_responses: list[list[RawHistoricalForcing]] | None = None,
        rprelimd_responses: list[list[RawHistoricalForcing]] | None = None,
        other_responses: list[list[RawHistoricalForcing]] | None = None,
        rhiresd_boundary: UtcDatetime | None = None,
    ) -> None:
        # ``*_responses`` is a per-product-set QUEUE consumed one entry per
        # matching call (the last entry repeats once exhausted) — for tests
        # that invoke the flow more than once and expect a different result
        # each time. ``*_rows`` is sugar for the common single-response case.
        self._rhiresd_responses = (
            rhiresd_responses if rhiresd_responses is not None else [rhiresd_rows or []]
        )
        self._rprelimd_responses = (
            rprelimd_responses
            if rprelimd_responses is not None
            else [rprelimd_rows or []]
        )
        self._other_responses = (
            other_responses if other_responses is not None else [other_rows or []]
        )
        self._rhiresd_boundary = rhiresd_boundary
        self.calls: list[_ProductsCall] = []
        self.boundary_calls = 0
        self._rhiresd_idx = 0
        self._rprelimd_idx = 0
        self._other_idx = 0

    def fetch_products(
        self,
        products: list[ForcingSource],
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        self.calls.append(
            _ProductsCall(
                products=list(products),
                station_configs=list(station_configs),
                start=start,
                end=end,
                parameters=list(parameters),
            )
        )
        if ForcingSource.METEOSWISS_RHIRESD in products:
            idx = min(self._rhiresd_idx, len(self._rhiresd_responses) - 1)
            self._rhiresd_idx += 1
            return list(self._rhiresd_responses[idx])
        if ForcingSource.METEOSWISS_RPRELIMD in products:
            idx = min(self._rprelimd_idx, len(self._rprelimd_responses) - 1)
            self._rprelimd_idx += 1
            return list(self._rprelimd_responses[idx])
        idx = min(self._other_idx, len(self._other_responses) - 1)
        self._other_idx += 1
        return list(self._other_responses[idx])

    def discover_rhiresd_boundary(self) -> UtcDatetime | None:
        self.boundary_calls += 1
        return self._rhiresd_boundary


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

    def fetch_latest_valid_time(
        self,
        station_ids: list[StationId],
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> UtcDatetime | None:
        station_id_set = set(station_ids)
        candidates = [
            r.valid_time
            for _, r in self._rows
            if r.station_id in station_id_set
            and r.source == source
            and start <= r.valid_time < end
        ]
        return max(candidates) if candidates else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _weather_source(
    station_id: StationId,
    *,
    nwp_source: str = _REANALYSIS_SOURCE,
    status: WeatherSourceStatus = WeatherSourceStatus.ACTIVE,
    role: WeatherSourceRole | None = None,
) -> StationWeatherSource:
    # Mirrors the migration 0030 backfill rule: icon_ch2_eps is the only
    # FORECAST source; everything else here is REANALYSIS. Callers testing
    # the defense-in-depth role guard (§7) can override the derived role
    # explicitly — e.g. to construct a FORECAST binding that (invalidly,
    # per D1) shares a reanalysis source name.
    if role is None:
        role = (
            WeatherSourceRole.FORECAST
            if nwp_source == "icon_ch2_eps"
            else WeatherSourceRole.REANALYSIS
        )
    return StationWeatherSource(
        station_id=station_id,
        nwp_source=nwp_source,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=status,
        role=role,
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

        precip_row = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=12.5,
            version="v1",
        )
        other_row = _row(
            station.id,
            parameter="temperature",
            source=ForcingSource.METEOSWISS_TABSD.value,
            value=9.0,
            version="v1",
        )
        # No rhiresd_boundary => R is undiscovered => the whole window is
        # preliminary (RprelimD-scoped only; the RhiresD-scoped call is
        # skipped, §0a/§1G).
        adapter = _SpyReanalysisAdapter(
            rprelimd_rows=[precip_row], other_rows=[other_row]
        )
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        # Two fetches (RprelimD-scoped + the four non-precip products), both
        # over the rolling 60-day window ending at now; no RhiresD-scoped
        # call (R is undiscovered).
        assert len(adapter.calls) == 2
        assert all(c.start == _START and c.end == _NOW for c in adapter.calls)
        assert not any(
            ForcingSource.METEOSWISS_RHIRESD in c.products for c in adapter.calls
        )
        rprelimd_calls = [
            c for c in adapter.calls if ForcingSource.METEOSWISS_RPRELIMD in c.products
        ]
        assert len(rprelimd_calls) == 1
        assert rprelimd_calls[0].parameters == ["precipitation"]
        other_calls = [
            c for c in adapter.calls if ForcingSource.METEOSWISS_TABSD in c.products
        ]
        assert len(other_calls) == 1
        assert set(other_calls[0].parameters) == {
            "temperature",
            "temperature_min",
            "temperature_max",
            "relative_sunshine_duration",
        }
        # Pin the EXACT non-precip product set (not just the parameters) —
        # otherwise dropping e.g. METEOSWISS_SRELD from _NON_PRECIP_PRODUCTS
        # would leave the parameter list unchanged and slip through silently
        # (Plan 115b1 §1A — SrelD is one of the four non-precip products).
        assert set(other_calls[0].products) == {
            ForcingSource.METEOSWISS_TABSD,
            ForcingSource.METEOSWISS_TMIND,
            ForcingSource.METEOSWISS_TMAXD,
            ForcingSource.METEOSWISS_SRELD,
        }

        # The adapter's rows were persisted verbatim (known-answer).
        assert _project(store.records) == _project_raw([precip_row, other_row])

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
            rprelimd_rows=[
                _row(
                    bound.id,
                    parameter="precipitation",
                    source=ForcingSource.METEOSWISS_RPRELIMD.value,
                    value=3.0,
                    version="v1",
                )
            ]
        )
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert len(adapter.calls) == 2
        for call in adapter.calls:
            config_station_ids = {c.station_id for c in call.station_configs}
            assert config_station_ids == {bound.id}

    def test_no_op_when_no_reanalysis_bound_stations(self) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(
            _weather_source(station.id, nwp_source="icon_ch2_eps")
        )

        adapter = _SpyReanalysisAdapter()
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        # No reanalysis-bound source => no fetch, no write, and — self-
        # containment — no R discovery either (zero-station short-circuit).
        assert adapter.calls == []
        assert adapter.boundary_calls == 0
        assert store.records == []

    def test_excludes_forecast_binding_sharing_the_reanalysis_source_name(
        self,
    ) -> None:
        # Defense-in-depth (§7): even a binding whose nwp_source string equals
        # the reanalysis adapter's NWP_SOURCE must be excluded when its role is
        # FORECAST — role, not name equality, is authoritative. D1 says one
        # nwp_source string should carry exactly one role, but this proves the
        # flow-level filter does not silently trust name equality alone.
        # Soundness: fails against `_reanalysis_sources` filtering only on
        # `source.nwp_source == nwp_source` (the pre-115a implementation),
        # which would route this FORECAST-role binding straight into the fetch.
        forecast_role_station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(forecast_role_station)
        station_store.store_weather_source(
            _weather_source(forecast_role_station.id, role=WeatherSourceRole.FORECAST)
        )

        adapter = _SpyReanalysisAdapter()
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert adapter.calls == []
        assert store.records == []

    def test_no_op_when_no_stations(self) -> None:
        adapter = _SpyReanalysisAdapter()
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
        adapter = _SpyReanalysisAdapter(rprelimd_responses=[[v1], [v2]])
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
        adapter = _SpyReanalysisAdapter(rprelimd_rows=[row])
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

        precip_row = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=2.0,
            version="v1",
        )
        tmin_row = _row(
            station.id,
            parameter="temperature_min",
            source=ForcingSource.METEOSWISS_TMIND.value,
            value=1.0,
            version="v1",
        )
        adapter = _SpyReanalysisAdapter(
            rprelimd_rows=[precip_row], other_rows=[tmin_row]
        )
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
            rprelimd_rows=[
                _row(
                    station.id,
                    parameter="precipitation",
                    source=ForcingSource.METEOSWISS_RPRELIMD.value,
                    value=1.0,
                    version="v1",
                )
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

        # Two calls (RprelimD-scoped + non-precip); no RhiresD-scoped call
        # (undiscovered R -> default spy boundary is None).
        assert len(spy.calls) == 2
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


# ---------------------------------------------------------------------------
# Plan 115b1 §0a/§1D/§1G — precipitation split by the discovered boundary R
# ---------------------------------------------------------------------------


class TestPrecipitationSplitByBoundary:
    def test_boundary_inside_window_splits_both_calls(self) -> None:
        # R = 2026-05-20 -> RhiresD covers [_START, 2026-05-21), RprelimD
        # covers [2026-05-21, _NOW).
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        r = ensure_utc(datetime(2026, 5, 20, tzinfo=UTC))
        split = ensure_utc(datetime(2026, 5, 21, tzinfo=UTC))
        adapter = _SpyReanalysisAdapter(rhiresd_boundary=r)
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert adapter.boundary_calls == 1
        assert len(adapter.calls) == 3
        rhiresd_call = next(
            c for c in adapter.calls if ForcingSource.METEOSWISS_RHIRESD in c.products
        )
        assert rhiresd_call.start == _START
        assert rhiresd_call.end == split
        assert rhiresd_call.parameters == ["precipitation"]

        rprelimd_call = next(
            c for c in adapter.calls if ForcingSource.METEOSWISS_RPRELIMD in c.products
        )
        assert rprelimd_call.start == split
        assert rprelimd_call.end == _NOW
        assert rprelimd_call.parameters == ["precipitation"]

    def test_boundary_at_or_after_now_skips_rprelimd_call(self) -> None:
        # RhiresD already covers the entire window -> the RprelimD-scoped
        # call's span collapses to empty and must be SKIPPED, not issued with
        # a degenerate [now, now) range.
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        r = ensure_utc(datetime(2026, 6, 20, tzinfo=UTC))  # after _NOW
        adapter = _SpyReanalysisAdapter(rhiresd_boundary=r)
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert len(adapter.calls) == 2  # RhiresD-scoped + non-precip only
        rhiresd_call = next(
            c for c in adapter.calls if ForcingSource.METEOSWISS_RHIRESD in c.products
        )
        assert rhiresd_call.start == _START
        assert rhiresd_call.end == _NOW
        assert not any(
            ForcingSource.METEOSWISS_RPRELIMD in c.products for c in adapter.calls
        )

    def test_boundary_before_window_skips_rhiresd_call(self) -> None:
        # R sits entirely before the rolling window -> RhiresD's span
        # collapses to empty and must be SKIPPED; the whole window is
        # preliminary.
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        r = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))  # long before _START
        adapter = _SpyReanalysisAdapter(rhiresd_boundary=r)
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert len(adapter.calls) == 2  # RprelimD-scoped + non-precip only
        assert not any(
            ForcingSource.METEOSWISS_RHIRESD in c.products for c in adapter.calls
        )
        rprelimd_call = next(
            c for c in adapter.calls if ForcingSource.METEOSWISS_RPRELIMD in c.products
        )
        assert rprelimd_call.start == _START
        assert rprelimd_call.end == _NOW


# ---------------------------------------------------------------------------
# Plan 115b1 §1G — self-containment (round-1 blocker)
# ---------------------------------------------------------------------------


class TestSelfContainment:
    """After 115b1 lands, Flow 6's ingest runs WITHOUT raising — it uses
    ``fetch_products`` (Plan 115b1 §1F), not the now-fail-closed parameter
    path — through the REAL adapter, with a REAL station binding (not the
    zero-station short-circuit, which would mask the old broken call shape).

    Soundness: fails against a 115b1 that adds the fail-closed guard to
    ``fetch_reanalysis`` but leaves the flow's call at
    ``fetch_reanalysis(_CANONICAL_PARAMETERS)`` (which includes
    "precipitation") in place — that call would raise ``ConfigurationError``
    the moment ANY station is bound (proven directly against the real
    adapter class here, not a fake that simply lacks the old method).
    """

    def test_flow_completes_through_real_adapter_with_a_bound_station(self) -> None:
        import httpx

        from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
            ExactExtractGridExtractor,
        )

        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))
        store = _SupersedingForcingStore()

        # Every STAC query returns "no features" (a documented gap, per the
        # adapter's own contract) — real, empty responses, not a raised
        # network error. If the flow had regressed to the pre-115b1 single
        # ``fetch_reanalysis(_CANONICAL_PARAMETERS)`` call instead of
        # ``fetch_products``, THIS specific real adapter would raise
        # ``ConfigurationError`` (fail-closed on "precipitation") before ever
        # reaching this handler — proving the call-shape rewrite landed.
        def _handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/items/archive-ch"):
                return httpx.Response(200, json={"assets": {}})
            return httpx.Response(200, json={"features": [], "links": []})

        real_adapter = MeteoSwissOpenDataReanalysisAdapter(
            stac_base_url=_DEFAULT_REANALYSIS_STAC_BASE_URL,
            stac_collection=_DEFAULT_REANALYSIS_STAC_COLLECTION,
            http_client=httpx.Client(transport=httpx.MockTransport(_handler)),
            extractor=ExactExtractGridExtractor(),
            basins={},
            clock=_fixed_clock,
        )

        # Must NOT raise ConfigurationError (the §1F fail-closed guard) even
        # though a REANALYSIS-role, ACTIVE, BASIN_AVERAGE binding exists.
        result = ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=real_adapter,
            clock=_fixed_clock,
        )

        assert result.stations_targeted == 1
        # No RhiresD ever published (R discovery found nothing) and every
        # day query is a documented gap -> nothing to store — but the
        # important assertion is that the call above did not raise.
        assert result.rows_stored == 0


class TestParametricBackfillWindow:
    """Plan 082 Task 3B item 4: explicit window_days for multi-year Nepal
    historical back-extraction; the Swiss 60-day default stays unchanged."""

    def test_window_days_730_starts_730_days_before_now(self) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        adapter = _SpyReanalysisAdapter()
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
            window_days=730,
        )

        expected_start = ensure_utc(_NOW - timedelta(days=730))
        assert len(adapter.calls) == 2
        assert all(c.start == expected_start and c.end == _NOW for c in adapter.calls)

    def test_default_window_unchanged_at_60_days(self) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        adapter = _SpyReanalysisAdapter()
        store = _SupersedingForcingStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
        )

        assert len(adapter.calls) == 2
        assert all(c.start == _START and c.end == _NOW for c in adapter.calls)


# ---------------------------------------------------------------------------
# Plan 115b4 §6A/§6B — health-by-EFFECT, never rows_stored
# ---------------------------------------------------------------------------


class TestWeatherHistoryHealthByEffect:
    """Plan 115b4 §6B: health is measured by ACTUAL EFFECT (a real DB
    readback), never ``rows_stored`` (which is ``len(records)`` post
    ``on_conflict_do_nothing`` and looks healthy even for a pure-duplicate
    re-fetch). Two distinct UNHEALTHY reasons distinguished: nobody bound
    (config fault) vs bound but the run had zero effect on the store's
    on-disk horizon (silent failure).
    """

    def test_zero_stations_bound_is_unhealthy(self) -> None:
        adapter = _SpyReanalysisAdapter()
        store = _SupersedingForcingStore()
        health = FakePipelineHealthStore()

        ingest_weather_history_flow(
            station_store=FakeStationStore(),
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
            pipeline_health_store=health,
        )

        records = health.fetch_recent(PipelineCheckType.WEATHER_HISTORY_INGEST)
        assert len(records) == 1
        assert records[0].status == PipelineHealthStatus.CRITICAL
        assert records[0].detail["reason"] == "no_stations_bound"

    def test_bound_stations_but_zero_effect_is_unhealthy(self) -> None:
        # A station is bound, but the adapter returns nothing AND the store
        # has never held a row for the sources this run targets — the run
        # had ZERO effect on the store's on-disk horizon, which
        # rows_stored == 0 would ALSO catch here, but critically this is
        # asserted via the store's actual MAX(valid_time) readback (§6B),
        # not the flow's own in-memory counter.
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        adapter = _SpyReanalysisAdapter()  # returns nothing for every call
        store = _SupersedingForcingStore()
        health = FakePipelineHealthStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
            pipeline_health_store=health,
        )

        records = health.fetch_recent(PipelineCheckType.WEATHER_HISTORY_INGEST)
        assert len(records) == 1
        assert records[0].status == PipelineHealthStatus.CRITICAL
        assert records[0].detail["reason"] == "no_horizon_advance"

    def test_bound_stations_with_effect_is_healthy(self) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        row = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=5.0,
            version="v1",
        )
        adapter = _SpyReanalysisAdapter(rprelimd_rows=[row])
        store = _SupersedingForcingStore()
        health = FakePipelineHealthStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
            pipeline_health_store=health,
        )

        records = health.fetch_recent(PipelineCheckType.WEATHER_HISTORY_INGEST)
        assert len(records) == 1
        assert records[0].status == PipelineHealthStatus.OK

    def test_duplicate_rerun_that_advances_nothing_new_is_unhealthy(self) -> None:
        # A second identical (pure-duplicate) re-fetch — same clock, same
        # adapter rows, so the store's MAX(valid_time) for the targeted
        # source in this run's window is IDENTICAL before and after — must be
        # flagged UNHEALTHY, even though the store already holds a row for
        # that source within the window (from the first run) and this run's
        # own ``rows_stored`` counter is identically shaped to the first
        # run's. A "row exists" check alone would wrongly call this healthy;
        # only a before/after comparison catches a run with zero EFFECT.
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        row = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=5.0,
            version="v1",
        )
        adapter = _SpyReanalysisAdapter(rprelimd_rows=[row])
        store = _SupersedingForcingStore()
        health = FakePipelineHealthStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
            pipeline_health_store=health,
        )
        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
            pipeline_health_store=health,
        )

        records = health.fetch_recent(PipelineCheckType.WEATHER_HISTORY_INGEST)
        assert len(records) == 2
        # The first run advances the horizon from nothing to the row's
        # valid_time (healthy); the second run is a stuck duplicate with
        # IDENTICAL before/after MAX(valid_time) — zero effect, unhealthy.
        assert records[0].status == PipelineHealthStatus.OK
        assert records[1].status == PipelineHealthStatus.CRITICAL
        assert records[1].detail["reason"] == "no_horizon_advance"

    def test_second_run_with_genuinely_new_data_is_healthy(self) -> None:
        # Guards against an over-broad fix: a second run that DOES land a
        # later row for the targeted source (e.g. the next day's data
        # appearing) must still be reported healthy — the before/after
        # comparison detects advancement, not merely "ran twice".
        station = make_station_config(code="2135", name="Aare Bern")
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_weather_source(_weather_source(station.id))

        first_row = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=5.0,
            version="v1",
            valid_time=_DAY,
        )
        second_row = _row(
            station.id,
            parameter="precipitation",
            source=ForcingSource.METEOSWISS_RPRELIMD.value,
            value=6.0,
            version="v1",
            valid_time=ensure_utc(_DAY + timedelta(days=1)),
        )
        adapter = _SpyReanalysisAdapter(rprelimd_responses=[[first_row], [second_row]])
        store = _SupersedingForcingStore()
        health = FakePipelineHealthStore()

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
            pipeline_health_store=health,
        )

        ingest_weather_history_flow(
            station_store=station_store,
            forcing_store=store,
            adapter=adapter,
            clock=_fixed_clock,
            pipeline_health_store=health,
        )

        records = health.fetch_recent(PipelineCheckType.WEATHER_HISTORY_INGEST)
        assert len(records) == 2
        assert records[0].status == PipelineHealthStatus.OK
        assert records[1].status == PipelineHealthStatus.OK
