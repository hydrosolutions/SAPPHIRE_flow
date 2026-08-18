"""ERA5-Land (sloth-dynamic store) binding + chunked backfill (Plan 183 T2).

Mirrors ``services/reanalysis_backfill.py``'s chunked, resumable backfill
pattern (Plan 115b2): work units are ``(year, parameter, station-batch)``,
each chunk persisted before the next so the full span — roughly
40 yr x 296 basins x 2 params ~= 8.6M rows — is never held in memory at once.
Gap detection keys on the LOGICAL key (station, source, valid_time,
parameter, spatial_type) — excluding ``version`` — exactly like the
MeteoSwiss backfill, so a re-run of an interrupted backfill only fetches and
inserts what is missing.

Deliberately NOT a generalisation of ``reanalysis_backfill.py``: ERA5-Land
has no product/live-tail split (one store, two operational parameters per
Plan 183 D2) and no STAC-published high-water mark — its upper bound is the
store's own ``discover_boundary()`` (the zarr's own time-axis max), not a
per-product asset scan.

"Eligible" means "every station with a valid basin polygon" — the same rule
Plan 115b2 uses for MeteoSwiss, deliberately duplicated (not imported) here:
these are two independently-evolving backfills that happen to share a
geometry-validity rule today, not a shared abstraction worth coupling them
to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import structlog
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.station import StationWeatherSource

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import (
        BasinStore,
        HistoricalForcingStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)

#: Nwp_source token the ERA5-Land binding matches on — mirrors
#: ``Era5LandReanalysisAdapter.NWP_SOURCE`` without importing the adapter
#: module (which pulls xarray/shapely/aquacast at import time).
NWP_SOURCE = ForcingSource.ERA5_LAND.value

#: Owner decision (carried over from Plan 115b §0, matches CAMELS/Caravan's
#: training-window start): the upper bound is discovered from the store
#: itself (``Era5LandReanalysisAdapter.discover_boundary()``), not fixed.
BACKFILL_START: date = date(1981, 1, 1)

_STATION_BATCH_SIZE = 50

#: D2 (owner, 2026-08-18): only these two parameters are backfilled —
#: radiation is deferred.
PARAMETERS: tuple[str, ...] = ("precipitation", "temperature")


class Era5LandBackfillAdapter(Protocol):
    """Structural view of the adapter capability the backfill needs."""

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]: ...

    def discover_boundary(self) -> UtcDatetime | None: ...


def _has_valid_geometry(geometry: object) -> bool:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        return False
    return not geometry.is_empty and geometry.is_valid


def _make_binding(station: StationConfig) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station.id,
        nwp_source=NWP_SOURCE,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


def eligible_era5_land_configs(
    stations: list[StationConfig], basin_store: BasinStore
) -> list[StationWeatherSource]:
    """Every station in ``stations`` with a valid basin polygon. Stations
    lacking one are logged and excluded — never silently dropped."""
    configs: list[StationWeatherSource] = []
    for station in stations:
        if station.basin_id is None:
            log.warning(
                "era5_land_backfill.station_excluded",
                station_id=str(station.id),
                code=station.code,
                reason="no_basin_id",
            )
            continue
        basin = basin_store.fetch_basin(station.basin_id)
        if basin is None or not _has_valid_geometry(basin.geometry):
            log.warning(
                "era5_land_backfill.station_excluded",
                station_id=str(station.id),
                code=station.code,
                reason="no_valid_basin_geometry",
            )
            continue
        configs.append(_make_binding(station))
    return configs


@dataclass(frozen=True, kw_only=True, slots=True)
class BindingBackfillResult:
    stations_bound: int
    stations_excluded: int


def bind_era5_land_reanalysis_fleet(
    station_store: StationStore, basin_store: BasinStore
) -> BindingBackfillResult:
    """One-shot binding backfill: insert the ERA5-Land reanalysis binding
    for every eligible existing station. Idempotent (``store_weather_source``
    upserts)."""
    all_stations = station_store.fetch_all_stations()
    configs = eligible_era5_land_configs(all_stations, basin_store)
    for ws in configs:
        station_store.store_weather_source(ws)
    return BindingBackfillResult(
        stations_bound=len(configs),
        stations_excluded=len(all_stations) - len(configs),
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class BackfillResult:
    chunks_processed: int
    chunks_skipped: int
    rows_written: int
    stations: int


def _chunk(
    items: list[StationWeatherSource], size: int
) -> list[list[StationWeatherSource]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _year_bounds(year: int) -> tuple[UtcDatetime, UtcDatetime]:
    start = ensure_utc(datetime(year, 1, 1, tzinfo=UTC))
    end = ensure_utc(datetime(year + 1, 1, 1, tzinfo=UTC))
    return start, end


def _days_in_range(start: UtcDatetime, end: UtcDatetime) -> list[date]:
    out: list[date] = []
    day = start.date()
    last_day = end.date()
    while day <= last_day:
        midnight = ensure_utc(datetime.combine(day, datetime.min.time(), tzinfo=UTC))
        if start <= midnight < end:
            out.append(day)
        day = day + timedelta(days=1)
    return out


def run_era5_land_backfill(
    *,
    adapter: Era5LandBackfillAdapter,
    forcing_store: HistoricalForcingStore,
    station_configs: list[StationWeatherSource],
    start: date = BACKFILL_START,
    end: UtcDatetime | None = None,
    station_batch_size: int = _STATION_BATCH_SIZE,
) -> BackfillResult:
    """The chunked, resumable driver. ``end`` defaults to
    ``adapter.discover_boundary()`` — the store's own high-water mark —
    accepted as a parameter so tests (and a caller with a fixed target) can
    bypass that discovery call."""
    resolved_end = end if end is not None else adapter.discover_boundary()
    empty = BackfillResult(
        chunks_processed=0,
        chunks_skipped=0,
        rows_written=0,
        stations=len(station_configs),
    )
    if resolved_end is None:
        return empty

    window_end_bound = ensure_utc(resolved_end + timedelta(days=1))
    span_start = ensure_utc(datetime.combine(start, datetime.min.time(), tzinfo=UTC))
    if span_start >= window_end_bound:
        return empty

    chunks_processed = 0
    chunks_skipped = 0
    rows_written = 0

    for year in range(span_start.year, window_end_bound.year + 1):
        year_start, year_end = _year_bounds(year)
        window_start = max(span_start, year_start)
        window_end = min(window_end_bound, year_end)
        if window_start >= window_end:
            continue
        expected_days = set(_days_in_range(window_start, window_end))

        for batch in _chunk(station_configs, station_batch_size):
            station_ids = [c.station_id for c in batch]
            for parameter in PARAMETERS:
                covered = forcing_store.fetch_covered_days(
                    station_ids=station_ids,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter=parameter,
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    start=window_start,
                    end=window_end,
                )
                missing = [
                    c
                    for c in batch
                    if covered.get(c.station_id, set()) != expected_days
                ]
                if not missing:
                    chunks_skipped += 1
                    continue

                rows = adapter.fetch_reanalysis(
                    missing, window_start, window_end, [parameter]
                )
                to_insert = [
                    r
                    for r in rows
                    if r.parameter == parameter
                    and r.valid_time.date() not in covered.get(r.station_id, set())
                ]
                if to_insert:
                    forcing_store.store_forcing(to_insert)
                    rows_written += len(to_insert)
                chunks_processed += 1
                log.info(
                    "era5_land_backfill.chunk_complete",
                    parameter=parameter,
                    year=year,
                    station_count=len(missing),
                    rows_written=len(to_insert),
                )

    return BackfillResult(
        chunks_processed=chunks_processed,
        chunks_skipped=chunks_skipped,
        rows_written=rows_written,
        stations=len(station_configs),
    )
