"""Plan 115b2 — MeteoSwiss binding backfill (§2A) + chunked, resumable
1981-present backfill (§3A-§3D).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from shapely.geometry import box

from sapphire_flow.services.reanalysis_backfill import (
    BackfillSpan,
    bind_meteoswiss_reanalysis_fleet,
    discover_backfill_spans,
    eligible_meteoswiss_configs,
    run_backfill,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHistoricalForcingStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _valid_basin(network: str = "bafu") -> Basin:
    return Basin(
        id=BasinId(uuid4()),
        code=f"basin-{uuid4().hex[:6]}",
        name="Valid basin",
        geometry=box(6.0, 46.0, 10.0, 48.0),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=_EPOCH,
        network=network,
    )


def _invalid_basin(network: str = "bafu") -> Basin:
    return Basin(
        id=BasinId(uuid4()),
        code=f"basin-{uuid4().hex[:6]}",
        name="Invalid basin (no geometry)",
        geometry=None,
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=_EPOCH,
        network=network,
    )


class TestEligibleMeteoswissConfigs:
    def test_station_with_valid_basin_geometry_is_eligible(self) -> None:
        basin = _valid_basin()
        station = make_station_config(basin_id=basin.id)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        configs = eligible_meteoswiss_configs([station], basin_store)

        assert len(configs) == 1
        ws = configs[0]
        assert ws.station_id == station.id
        assert ws.nwp_source == "meteoswiss_open_data_reanalysis"
        assert ws.role is WeatherSourceRole.REANALYSIS
        assert ws.extraction_type is SpatialRepresentation.BASIN_AVERAGE

    def test_station_with_no_basin_id_is_excluded_not_dropped_silently(self) -> None:
        station = make_station_config(basin_id=None)
        basin_store = FakeBasinStore()

        configs = eligible_meteoswiss_configs([station], basin_store)

        assert configs == []

    def test_station_with_invalid_basin_geometry_is_excluded(self) -> None:
        basin = _invalid_basin()
        station = make_station_config(basin_id=basin.id)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        configs = eligible_meteoswiss_configs([station], basin_store)

        assert configs == []

    def test_station_with_dangling_basin_id_is_excluded(self) -> None:
        # basin_id points at a basin that was never stored.
        station = make_station_config(basin_id=BasinId(uuid4()))
        basin_store = FakeBasinStore()

        configs = eligible_meteoswiss_configs([station], basin_store)

        assert configs == []

    def test_a_mixed_set_writes_exactly_n_eligible_stations(self) -> None:
        valid_basin = _valid_basin()
        invalid_basin = _invalid_basin()
        basin_store = FakeBasinStore()
        basin_store.store_basin(valid_basin)
        basin_store.store_basin(invalid_basin)

        eligible_station = make_station_config(
            station_id=StationId(uuid4()), code="OK-1", basin_id=valid_basin.id
        )
        excluded_no_geom = make_station_config(
            station_id=StationId(uuid4()), code="BAD-1", basin_id=invalid_basin.id
        )
        excluded_no_basin = make_station_config(
            station_id=StationId(uuid4()), code="BAD-2", basin_id=None
        )

        configs = eligible_meteoswiss_configs(
            [eligible_station, excluded_no_geom, excluded_no_basin], basin_store
        )

        assert [c.station_id for c in configs] == [eligible_station.id]


class TestBindMeteoswissReanalysisFleet:
    def test_binds_every_eligible_existing_station(self) -> None:
        basin = _valid_basin()
        station = make_station_config(basin_id=basin.id)
        station_store = FakeStationStore()
        station_store.store_station(station)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        result = bind_meteoswiss_reanalysis_fleet(station_store, basin_store)

        assert result.stations_bound == 1
        assert result.stations_excluded == 0
        bindings = station_store.fetch_reanalysis_bindings(station.id)
        assert any(b.nwp_source == "meteoswiss_open_data_reanalysis" for b in bindings)

    def test_excludes_and_counts_stations_without_valid_geometry(self) -> None:
        station = make_station_config(basin_id=None)
        station_store = FakeStationStore()
        station_store.store_station(station)
        basin_store = FakeBasinStore()

        result = bind_meteoswiss_reanalysis_fleet(station_store, basin_store)

        assert result.stations_bound == 0
        assert result.stations_excluded == 1
        assert station_store.fetch_reanalysis_bindings(station.id) == []

    def test_idempotent_rerun(self) -> None:
        basin = _valid_basin()
        station = make_station_config(basin_id=basin.id)
        station_store = FakeStationStore()
        station_store.store_station(station)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        bind_meteoswiss_reanalysis_fleet(station_store, basin_store)
        result2 = bind_meteoswiss_reanalysis_fleet(station_store, basin_store)

        assert result2.stations_bound == 1
        bindings = station_store.fetch_reanalysis_bindings(station.id)
        assert len(bindings) == 1


class _FakeBackfillAdapter:
    """Test double for ``MeteoSwissBackfillAdapter`` — canned per-product
    high-water marks, and a spy on ``fetch_products`` calls so tests can
    assert exactly which (product, stations, window) chunks were fetched."""

    def __init__(
        self,
        *,
        boundaries: dict[ForcingSource, object],
        rows_by_call: dict[tuple, list] | None = None,
    ) -> None:
        self._boundaries = boundaries
        self._rows_by_call = rows_by_call or {}
        self.fetch_calls: list[tuple] = []

    def discover_product_boundary(self, product: ForcingSource):  # type: ignore[no-untyped-def]
        return self._boundaries.get(product)

    def fetch_products(self, products, station_configs, start, end, parameters):  # type: ignore[no-untyped-def]
        key = (
            tuple(p.value for p in products),
            tuple(sorted(c.station_id for c in station_configs)),
            start,
            end,
            tuple(parameters),
        )
        self.fetch_calls.append(key)
        return self._rows_by_call.get(key, [])


class TestDiscoverBackfillSpans:
    def test_split_rule_rhiresd_and_rprelimd_are_disjoint(self) -> None:
        r = ensure_utc(datetime(2026, 5, 31, tzinfo=UTC))
        rprelimd_hwm = ensure_utc(datetime(2026, 7, 13, tzinfo=UTC))
        adapter = _FakeBackfillAdapter(
            boundaries={
                ForcingSource.METEOSWISS_RHIRESD: r,
                ForcingSource.METEOSWISS_RPRELIMD: rprelimd_hwm,
                ForcingSource.METEOSWISS_TABSD: rprelimd_hwm,
                ForcingSource.METEOSWISS_TMIND: rprelimd_hwm,
                ForcingSource.METEOSWISS_TMAXD: rprelimd_hwm,
                ForcingSource.METEOSWISS_SRELD: rprelimd_hwm,
            }
        )

        spans = discover_backfill_spans(adapter)
        by_product = {s.product: s for s in spans}

        rhiresd = by_product[ForcingSource.METEOSWISS_RHIRESD]
        rprelimd = by_product[ForcingSource.METEOSWISS_RPRELIMD]
        assert rhiresd.start == ensure_utc(datetime(1981, 1, 1, tzinfo=UTC))
        assert rhiresd.end == ensure_utc(datetime(2026, 6, 1, tzinfo=UTC))
        assert rprelimd.start == rhiresd.end  # disjoint, half-open, no gap/overlap
        assert rprelimd.end == ensure_utc(datetime(2026, 7, 14, tzinfo=UTC))

    def test_product_with_no_published_asset_is_omitted_not_substituted(self) -> None:
        # Soundness: fails against an implementation that falls back to
        # "today" when a product's HWM discovery returns None (round-1
        # blocker 2 — never a single shared T).
        adapter = _FakeBackfillAdapter(
            boundaries={
                ForcingSource.METEOSWISS_RHIRESD: None,
                ForcingSource.METEOSWISS_RPRELIMD: None,
                ForcingSource.METEOSWISS_TABSD: None,
                ForcingSource.METEOSWISS_TMIND: None,
                ForcingSource.METEOSWISS_TMAXD: None,
                ForcingSource.METEOSWISS_SRELD: None,
            }
        )

        spans = discover_backfill_spans(adapter)

        assert spans == []

    def test_each_product_bounded_by_its_own_high_water_mark(self) -> None:
        # Different products publish to different dates — each span's END
        # must match ITS OWN discovered HWM, never a shared value.
        r = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        tabsd_hwm = ensure_utc(datetime(2026, 7, 10, tzinfo=UTC))
        tmind_hwm = ensure_utc(datetime(2026, 6, 30, tzinfo=UTC))
        adapter = _FakeBackfillAdapter(
            boundaries={
                ForcingSource.METEOSWISS_RHIRESD: r,
                ForcingSource.METEOSWISS_RPRELIMD: None,
                ForcingSource.METEOSWISS_TABSD: tabsd_hwm,
                ForcingSource.METEOSWISS_TMIND: tmind_hwm,
                ForcingSource.METEOSWISS_TMAXD: None,
                ForcingSource.METEOSWISS_SRELD: None,
            }
        )

        spans = discover_backfill_spans(adapter)
        by_product = {s.product: s for s in spans}

        assert by_product[ForcingSource.METEOSWISS_TABSD].end == ensure_utc(
            datetime(2026, 7, 11, tzinfo=UTC)
        )
        assert by_product[ForcingSource.METEOSWISS_TMIND].end == ensure_utc(
            datetime(2026, 7, 1, tzinfo=UTC)
        )
        assert ForcingSource.METEOSWISS_TMAXD not in by_product
        assert ForcingSource.METEOSWISS_SRELD not in by_product
        assert ForcingSource.METEOSWISS_RPRELIMD not in by_product


def _binding(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="meteoswiss_open_data_reanalysis",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


class TestRunBackfillChunkedResumable:
    def test_full_rerun_over_complete_data_performs_zero_fetches(self) -> None:
        # Soundness: fails against gap detection that never checks existing
        # coverage (would re-fetch every chunk on every run).
        sid = StationId(uuid4())
        ws = [_binding(sid)]
        span = BackfillSpan(
            product=ForcingSource.METEOSWISS_TABSD,
            parameter="temperature",
            start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            end=ensure_utc(datetime(2020, 1, 3, tzinfo=UTC)),
        )
        forcing_store = FakeHistoricalForcingStore()
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=sid,
                    source=ForcingSource.METEOSWISS_TABSD.value,
                    parameter="temperature",
                    valid_time=datetime(2020, 1, 1, tzinfo=UTC),
                ),
                make_raw_historical_forcing(
                    station_id=sid,
                    source=ForcingSource.METEOSWISS_TABSD.value,
                    parameter="temperature",
                    valid_time=datetime(2020, 1, 2, tzinfo=UTC),
                ),
            ]
        )
        adapter = _FakeBackfillAdapter(boundaries={})

        result = run_backfill(
            adapter=adapter,
            forcing_store=forcing_store,
            station_configs=ws,
            spans=[span],
        )

        assert adapter.fetch_calls == []
        assert result.chunks_processed == 0
        assert result.chunks_skipped == 1
        assert result.rows_written == 0

    def test_interrupted_run_fetches_and_inserts_only_the_missing_chunk(self) -> None:
        # Two stations share a (product, year) chunk; one already has full
        # coverage (simulating a previous, interrupted run that got that far)
        # and must NOT be re-requested; the other is missing and must be.
        covered_id = StationId(uuid4())
        missing_id = StationId(uuid4())
        configs = [_binding(covered_id), _binding(missing_id)]

        span = BackfillSpan(
            product=ForcingSource.METEOSWISS_TABSD,
            parameter="temperature",
            start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            end=ensure_utc(datetime(2020, 1, 2, tzinfo=UTC)),
        )
        forcing_store = FakeHistoricalForcingStore()
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=covered_id,
                    source=ForcingSource.METEOSWISS_TABSD.value,
                    parameter="temperature",
                    valid_time=datetime(2020, 1, 1, tzinfo=UTC),
                )
            ]
        )
        new_row = make_raw_historical_forcing(
            station_id=missing_id,
            source=ForcingSource.METEOSWISS_TABSD.value,
            parameter="temperature",
            valid_time=datetime(2020, 1, 1, tzinfo=UTC),
        )
        call_key = (
            (ForcingSource.METEOSWISS_TABSD.value,),
            (missing_id,),
            span.start,
            span.end,
            ("temperature",),
        )
        adapter = _FakeBackfillAdapter(
            boundaries={}, rows_by_call={call_key: [new_row]}
        )

        result = run_backfill(
            adapter=adapter,
            forcing_store=forcing_store,
            station_configs=configs,
            spans=[span],
        )

        assert adapter.fetch_calls == [call_key]  # only the missing station
        assert result.chunks_processed == 1
        assert result.rows_written == 1
        stored_ids = {
            r.station_id
            for r in forcing_store.fetch_forcing(
                missing_id,
                ForcingSource.METEOSWISS_TABSD.value,
                span.start,
                span.end,
            )
        }
        assert stored_ids == {missing_id}

    def test_never_writes_a_chunk_twice_no_chunk_skipped(self) -> None:
        # A run over a fresh (no prior coverage) station writes rows exactly
        # once even though the driver iterates per-year/per-batch — no
        # duplicate chunk processing.
        sid = StationId(uuid4())
        configs = [_binding(sid)]
        span = BackfillSpan(
            product=ForcingSource.METEOSWISS_SRELD,
            parameter="relative_sunshine_duration",
            start=ensure_utc(datetime(2021, 1, 1, tzinfo=UTC)),
            end=ensure_utc(datetime(2021, 1, 2, tzinfo=UTC)),
        )
        forcing_store = FakeHistoricalForcingStore()
        row = make_raw_historical_forcing(
            station_id=sid,
            source=ForcingSource.METEOSWISS_SRELD.value,
            parameter="relative_sunshine_duration",
            valid_time=datetime(2021, 1, 1, tzinfo=UTC),
        )
        call_key = (
            (ForcingSource.METEOSWISS_SRELD.value,),
            (sid,),
            span.start,
            span.end,
            ("relative_sunshine_duration",),
        )
        adapter = _FakeBackfillAdapter(boundaries={}, rows_by_call={call_key: [row]})

        result = run_backfill(
            adapter=adapter,
            forcing_store=forcing_store,
            station_configs=configs,
            spans=[span],
        )

        assert result.chunks_processed == 1
        assert result.rows_written == 1
        assert len(adapter.fetch_calls) == 1
