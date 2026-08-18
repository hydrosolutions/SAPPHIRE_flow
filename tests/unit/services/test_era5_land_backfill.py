"""Plan 183 T2 — ERA5-Land chunked, resumable backfill acceptance tests.

Red-first (Plan-105-safe): every test guards its import so a missing symbol
fails as a genuine assertion, never a collection-time ``ImportError``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from shapely.geometry import Polygon, box

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


def _import_module():
    try:
        from sapphire_flow.services import era5_land_backfill
    except ImportError:
        pytest.fail(
            "sapphire_flow.services.era5_land_backfill is not implemented yet — "
            "expected a chunked, resumable per-year backfill (Plan 115b2 pattern) "
            "that gap-detects via fetch_covered_days and never re-fetches "
            "already-covered days."
        )
    return era5_land_backfill


def _valid_basin() -> Basin:
    return Basin(
        id=BasinId(uuid4()),
        code=f"basin-{uuid4().hex[:6]}",
        name="Valid basin",
        geometry=box(6.0, 46.0, 10.0, 48.0),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=_EPOCH,
        network="bafu",
    )


def _invalid_basin() -> Basin:
    return Basin(
        id=BasinId(uuid4()),
        code=f"basin-{uuid4().hex[:6]}",
        name="Invalid basin",
        geometry=Polygon(),
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=_EPOCH,
        network="bafu",
    )


def _binding(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source=ForcingSource.ERA5_LAND.value,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


class _FakeAdapter:
    def __init__(
        self,
        *,
        boundary,
        rows_by_call: dict[tuple, list] | None = None,
    ) -> None:
        self._boundary = boundary
        self._rows_by_call = rows_by_call or {}
        self.fetch_calls: list[tuple] = []

    def discover_boundary(self):
        return self._boundary

    def fetch_reanalysis(self, station_configs, start, end, parameters):
        key = (
            tuple(sorted(c.station_id for c in station_configs)),
            start,
            end,
            tuple(parameters),
        )
        self.fetch_calls.append(key)
        return self._rows_by_call.get(key, [])


class TestEligibleEra5LandConfigs:
    def test_station_with_valid_basin_geometry_is_eligible(self) -> None:
        mod = _import_module()
        basin = _valid_basin()
        station = make_station_config(basin_id=basin.id)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        configs = mod.eligible_era5_land_configs([station], basin_store)

        assert len(configs) == 1
        ws = configs[0]
        assert ws.station_id == station.id
        assert ws.nwp_source == ForcingSource.ERA5_LAND.value
        assert ws.role is WeatherSourceRole.REANALYSIS

    def test_station_without_valid_geometry_is_excluded(self) -> None:
        mod = _import_module()
        basin = _invalid_basin()
        station = make_station_config(basin_id=basin.id)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        configs = mod.eligible_era5_land_configs([station], basin_store)

        assert configs == []


class TestBindEra5LandReanalysisFleet:
    def test_binds_every_eligible_existing_station(self) -> None:
        mod = _import_module()
        basin = _valid_basin()
        station = make_station_config(basin_id=basin.id)
        station_store = FakeStationStore()
        station_store.store_station(station)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)

        result = mod.bind_era5_land_reanalysis_fleet(station_store, basin_store)

        assert result.stations_bound == 1
        assert result.stations_excluded == 0
        bindings = station_store.fetch_reanalysis_bindings(station.id)
        assert any(b.nwp_source == ForcingSource.ERA5_LAND.value for b in bindings)


class TestRunEra5LandBackfillValidation:
    """Minor (second fixer round): a non-positive ``station_batch_size``
    makes ``_chunk()`` (``range(0, len(items), size)``) come back empty —
    the backfill would silently report success having done zero work."""

    def test_negative_station_batch_size_raises(self) -> None:
        mod = _import_module()
        adapter = _FakeAdapter(boundary=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)))

        with pytest.raises(ValueError, match="station_batch_size"):
            mod.run_era5_land_backfill(
                adapter=adapter,
                forcing_store=FakeHistoricalForcingStore(),
                station_configs=[_binding(StationId(uuid4()))],
                station_batch_size=-1,
            )

    def test_zero_station_batch_size_raises(self) -> None:
        mod = _import_module()
        adapter = _FakeAdapter(boundary=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)))

        with pytest.raises(ValueError, match="station_batch_size"):
            mod.run_era5_land_backfill(
                adapter=adapter,
                forcing_store=FakeHistoricalForcingStore(),
                station_configs=[_binding(StationId(uuid4()))],
                station_batch_size=0,
            )


class TestRunEra5LandBackfillChunkedResumable:
    def test_no_boundary_yet_writes_nothing(self) -> None:
        mod = _import_module()
        adapter = _FakeAdapter(boundary=None)
        forcing_store = FakeHistoricalForcingStore()

        result = mod.run_era5_land_backfill(
            adapter=adapter,
            forcing_store=forcing_store,
            station_configs=[_binding(StationId(uuid4()))],
        )

        assert adapter.fetch_calls == []
        assert result.rows_written == 0

    def test_full_rerun_over_complete_data_performs_zero_fetches(self) -> None:
        mod = _import_module()
        sid = StationId(uuid4())
        configs = [_binding(sid)]
        boundary = ensure_utc(datetime(2020, 1, 2, tzinfo=UTC))
        forcing_store = FakeHistoricalForcingStore()
        for day, param in (
            (1, "precipitation"),
            (2, "precipitation"),
            (1, "temperature"),
            (2, "temperature"),
        ):
            forcing_store.store_forcing(
                [
                    make_raw_historical_forcing(
                        station_id=sid,
                        source=ForcingSource.ERA5_LAND.value,
                        parameter=param,
                        valid_time=datetime(2020, 1, day, tzinfo=UTC),
                    )
                ]
            )
        adapter = _FakeAdapter(boundary=boundary)

        result = mod.run_era5_land_backfill(
            adapter=adapter,
            forcing_store=forcing_store,
            station_configs=configs,
            start=datetime(2020, 1, 1).date(),
        )

        assert adapter.fetch_calls == []
        assert result.chunks_processed == 0
        assert result.rows_written == 0

    def test_interrupted_run_fetches_and_inserts_only_the_missing_station(
        self,
    ) -> None:
        mod = _import_module()
        covered_id = StationId(uuid4())
        missing_id = StationId(uuid4())
        configs = [_binding(covered_id), _binding(missing_id)]
        boundary = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))

        forcing_store = FakeHistoricalForcingStore()
        for param in ("precipitation", "temperature"):
            forcing_store.store_forcing(
                [
                    make_raw_historical_forcing(
                        station_id=covered_id,
                        source=ForcingSource.ERA5_LAND.value,
                        parameter=param,
                        valid_time=datetime(2020, 1, 1, tzinfo=UTC),
                    )
                ]
            )

        new_row = make_raw_historical_forcing(
            station_id=missing_id,
            source=ForcingSource.ERA5_LAND.value,
            parameter="precipitation",
            valid_time=datetime(2020, 1, 1, tzinfo=UTC),
        )
        window_start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        # Bounded by discover_boundary()+1day (2020-01-02), NOT the full
        # calendar year — the backfill never requests past the store's own
        # high-water mark.
        window_end = ensure_utc(datetime(2020, 1, 2, tzinfo=UTC))
        precip_call = (
            (missing_id,),
            window_start,
            window_end,
            ("precipitation",),
        )
        temp_call = (
            (missing_id,),
            window_start,
            window_end,
            ("temperature",),
        )
        adapter = _FakeAdapter(
            boundary=boundary,
            rows_by_call={precip_call: [new_row], temp_call: []},
        )

        result = mod.run_era5_land_backfill(
            adapter=adapter,
            forcing_store=forcing_store,
            station_configs=configs,
            start=datetime(2020, 1, 1).date(),
        )

        assert sorted(adapter.fetch_calls) == sorted([precip_call, temp_call])
        assert result.rows_written == 1
        stored = forcing_store.fetch_forcing(
            missing_id,
            ForcingSource.ERA5_LAND.value,
            window_start,
            window_end,
        )
        assert len(stored) == 1
        assert stored[0].parameter == "precipitation"

    def test_rerun_after_interruption_resumes_without_refetching_or_duplicating(
        self,
    ) -> None:
        """Minor (fixer round): a single run that only fetches the missing
        station does not demonstrate RESUME — it never proves a genuinely
        interrupted run (adapter dies mid-chunk, having written nothing for
        the missing station) recovers cleanly on a second call, without
        re-fetching already-covered chunks or duplicating rows."""
        mod = _import_module()
        covered_id = StationId(uuid4())
        missing_id = StationId(uuid4())
        configs = [_binding(covered_id), _binding(missing_id)]
        boundary = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))

        forcing_store = FakeHistoricalForcingStore()
        for param in ("precipitation", "temperature"):
            forcing_store.store_forcing(
                [
                    make_raw_historical_forcing(
                        station_id=covered_id,
                        source=ForcingSource.ERA5_LAND.value,
                        parameter=param,
                        valid_time=datetime(2020, 1, 1, tzinfo=UTC),
                    )
                ]
            )

        new_precip_row = make_raw_historical_forcing(
            station_id=missing_id,
            source=ForcingSource.ERA5_LAND.value,
            parameter="precipitation",
            valid_time=datetime(2020, 1, 1, tzinfo=UTC),
        )
        new_temp_row = make_raw_historical_forcing(
            station_id=missing_id,
            source=ForcingSource.ERA5_LAND.value,
            parameter="temperature",
            valid_time=datetime(2020, 1, 1, tzinfo=UTC),
        )
        window_start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        window_end = ensure_utc(datetime(2020, 1, 2, tzinfo=UTC))
        precip_call = ((missing_id,), window_start, window_end, ("precipitation",))
        temp_call = ((missing_id,), window_start, window_end, ("temperature",))

        # First run: simulates an interruption — the adapter successfully
        # returns the precipitation row, but crashes (returns nothing, as if
        # never reached) for temperature.
        interrupted_adapter = _FakeAdapter(
            boundary=boundary,
            rows_by_call={precip_call: [new_precip_row], temp_call: []},
        )
        first_result = mod.run_era5_land_backfill(
            adapter=interrupted_adapter,
            forcing_store=forcing_store,
            station_configs=configs,
            start=datetime(2020, 1, 1).date(),
        )
        assert sorted(interrupted_adapter.fetch_calls) == sorted(
            [precip_call, temp_call]
        )
        assert first_result.rows_written == 1

        # Second run against a FRESH adapter (simulating the operator
        # re-invoking the script): precipitation is now covered and must NOT
        # be re-fetched; only the still-missing temperature chunk is.
        resumed_adapter = _FakeAdapter(
            boundary=boundary,
            rows_by_call={temp_call: [new_temp_row]},
        )
        second_result = mod.run_era5_land_backfill(
            adapter=resumed_adapter,
            forcing_store=forcing_store,
            station_configs=configs,
            start=datetime(2020, 1, 1).date(),
        )

        assert resumed_adapter.fetch_calls == [temp_call]
        assert second_result.rows_written == 1

        stored = forcing_store.fetch_forcing(
            missing_id, ForcingSource.ERA5_LAND.value, window_start, window_end
        )
        # No duplicates: exactly one precipitation + one temperature row for
        # the previously-missing station, matching a single uninterrupted run.
        assert sorted(r.parameter for r in stored) == ["precipitation", "temperature"]

    def test_spans_multiple_years_as_separate_chunks(self) -> None:
        mod = _import_module()
        sid = StationId(uuid4())
        configs = [_binding(sid)]
        boundary = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        forcing_store = FakeHistoricalForcingStore()
        adapter = _FakeAdapter(boundary=boundary, rows_by_call={})

        mod.run_era5_land_backfill(
            adapter=adapter,
            forcing_store=forcing_store,
            station_configs=configs,
            start=datetime(2020, 6, 1).date(),
        )

        years_requested = {call[1].year for call in adapter.fetch_calls}
        assert years_requested == {2020, 2021}
