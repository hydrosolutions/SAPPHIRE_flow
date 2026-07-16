"""MeteoSwiss reanalysis binding + chunked historical backfill (Plan 115b2).

Two responsibilities, deliberately kept in one module because they share the
same eligibility rule (§3D) and the same adapter/store boundary:

- **Binding backfill (§2A/§2B)** — insert the four-field MeteoSwiss
  ``StationWeatherSource`` binding (``nwp_source``, ``role=REANALYSIS``,
  ``status=ACTIVE``, ``extraction_type=BASIN_AVERAGE``) for every eligible
  station. Idempotent (``store_weather_source`` upserts).
- **Chunked, resumable 1981-present backfill (§3A-§3D)** — work units are
  ``(product, year, station-batch)``; each chunk is persisted before the next
  (never holds the full multi-decade series in memory); gap detection keys on
  the LOGICAL key (station, source, valid_time, parameter, spatial_type) —
  excluding ``version`` — so a re-run of an interrupted backfill fetches and
  inserts only what is missing, and a full re-run over already-complete data
  performs zero network fetches.

"Eligible" (§3D) means "every station with a valid basin polygon" — no
``station_kind`` carve-out. A station lacking one is excluded and logged, not
silently dropped (``ExactExtractGridExtractor`` would otherwise do that
silently for a mixed batch).
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

#: Nwp_source token the MeteoSwiss reanalysis binding matches on — the
#: adapter's own class attribute (avoids importing the adapter class just for
#: this string, which would pull in httpx/rioxarray at module load).
NWP_SOURCE = "meteoswiss_open_data_reanalysis"

#: Owner decision (Plan 115b §0): backfill depth matches CAMELS' 1981-2020
#: window exactly (RhiresD/TabsD reach back to 1961/1961, but there is no
#: CAMELS counterpart before 1981 to validate against, and TminD/TmaxD only
#: begin in 1971 regardless).
BACKFILL_START: date = date(1981, 1, 1)

#: Work-unit granularity (§3A) — bounds per-chunk memory. One archive-year
#: NetCDF is a national grid; batching stations keeps the per-chunk row count
#: (station_batch_size x ~365 days) bounded regardless of fleet size.
_STATION_BATCH_SIZE = 50

# The four single-product archive parameters (temperature family + sunshine).
# Precipitation is handled separately (RhiresD/RprelimD split, see
# discover_backfill_spans).
_ARCHIVE_PRODUCTS: tuple[tuple[ForcingSource, str], ...] = (
    (ForcingSource.METEOSWISS_TABSD, "temperature"),
    (ForcingSource.METEOSWISS_TMIND, "temperature_min"),
    (ForcingSource.METEOSWISS_TMAXD, "temperature_max"),
    (ForcingSource.METEOSWISS_SRELD, "relative_sunshine_duration"),
)


class MeteoSwissBackfillAdapter(Protocol):
    """Structural view of the adapter capability the backfill needs: the
    writer-side product-scoped fetch (Plan 115b1 §1F) and per-product
    high-water-mark discovery (§1D, generalised by §3A/§3C)."""

    def fetch_products(
        self,
        products: list[ForcingSource],
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]: ...

    def discover_product_boundary(
        self, product: ForcingSource
    ) -> UtcDatetime | None: ...


def _has_valid_geometry(geometry: object) -> bool:
    return isinstance(geometry, (Polygon, MultiPolygon))


def _make_binding(station: StationConfig) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station.id,
        nwp_source=NWP_SOURCE,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


def eligible_meteoswiss_configs(
    stations: list[StationConfig], basin_store: BasinStore
) -> list[StationWeatherSource]:
    """Pre-enumerate the eligible set (§3D): every station in ``stations``
    with a valid basin polygon. Stations lacking one are logged and excluded
    — never silently dropped."""
    configs: list[StationWeatherSource] = []
    for station in stations:
        if station.basin_id is None:
            log.warning(
                "reanalysis_backfill.station_excluded",
                station_id=str(station.id),
                code=station.code,
                reason="no_basin_id",
            )
            continue
        basin = basin_store.fetch_basin(station.basin_id)
        if basin is None or not _has_valid_geometry(basin.geometry):
            log.warning(
                "reanalysis_backfill.station_excluded",
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


def bind_meteoswiss_reanalysis_fleet(
    station_store: StationStore, basin_store: BasinStore
) -> BindingBackfillResult:
    """§2A — one-shot data backfill: insert the MeteoSwiss reanalysis binding
    for every eligible EXISTING station. Idempotent — safe to re-run
    (``store_weather_source`` upserts on ``(station_id, nwp_source)``)."""
    all_stations = station_store.fetch_all_stations()
    configs = eligible_meteoswiss_configs(all_stations, basin_store)
    for ws in configs:
        station_store.store_weather_source(ws)
    return BindingBackfillResult(
        stations_bound=len(configs),
        stations_excluded=len(all_stations) - len(configs),
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class BackfillSpan:
    """One (product, parameter) window to backfill, half-open [start, end).

    Bounded by the product's own STAC-published high-water mark (§3A round-1
    blocker 2) — never a single shared ``T``.
    """

    product: ForcingSource
    parameter: str
    start: UtcDatetime
    end: UtcDatetime


def discover_backfill_spans(
    adapter: MeteoSwissBackfillAdapter,
) -> list[BackfillSpan]:
    """The split rule (Plan 115b §0a, 115b2 §3A), as half-open ranges bounded
    by PER-PRODUCT high-water marks:

    - precipitation: RhiresD over [1981-01-01, R+1d), RprelimD over
      [R+1d, hwm(rprelimd)+1d) — disjoint by construction.
    - TabsD/TminD/TmaxD/SrelD: one product each over
      [1981-01-01, hwm(p)+1d).

    A product with no published asset yet (``hwm is None``) is omitted
    entirely — never silently substituted with "today".
    """
    backfill_start = ensure_utc(
        datetime.combine(BACKFILL_START, datetime.min.time(), tzinfo=UTC)
    )
    spans: list[BackfillSpan] = []

    r = adapter.discover_product_boundary(ForcingSource.METEOSWISS_RHIRESD)
    rhiresd_end = backfill_start
    if r is not None:
        rhiresd_end = ensure_utc(r + timedelta(days=1))
        if backfill_start < rhiresd_end:
            spans.append(
                BackfillSpan(
                    product=ForcingSource.METEOSWISS_RHIRESD,
                    parameter="precipitation",
                    start=backfill_start,
                    end=rhiresd_end,
                )
            )

    rprelimd_hwm = adapter.discover_product_boundary(ForcingSource.METEOSWISS_RPRELIMD)
    if rprelimd_hwm is not None:
        rprelimd_end = ensure_utc(rprelimd_hwm + timedelta(days=1))
        rprelimd_start = max(rhiresd_end, backfill_start)
        if rprelimd_start < rprelimd_end:
            spans.append(
                BackfillSpan(
                    product=ForcingSource.METEOSWISS_RPRELIMD,
                    parameter="precipitation",
                    start=rprelimd_start,
                    end=rprelimd_end,
                )
            )

    for product, parameter in _ARCHIVE_PRODUCTS:
        hwm = adapter.discover_product_boundary(product)
        if hwm is None:
            continue
        end = ensure_utc(hwm + timedelta(days=1))
        if backfill_start < end:
            spans.append(
                BackfillSpan(
                    product=product, parameter=parameter, start=backfill_start, end=end
                )
            )

    return spans


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
    """Every calendar day whose midnight UTC falls inside the half-open
    ``[start, end)`` window — mirrors the adapter's own day-inclusion rule
    (``meteoswiss_open_data_reanalysis._days_in_range``), duplicated here to
    keep this module's day-set/gap-detection logic free of a heavy adapter
    import (httpx/rioxarray/xarray) at module load time."""
    out: list[date] = []
    day = start.date()
    last_day = end.date()
    while day <= last_day:
        midnight = ensure_utc(datetime.combine(day, datetime.min.time(), tzinfo=UTC))
        if start <= midnight < end:
            out.append(day)
        day = day + timedelta(days=1)
    return out


def run_backfill(
    *,
    adapter: MeteoSwissBackfillAdapter,
    forcing_store: HistoricalForcingStore,
    station_configs: list[StationWeatherSource],
    spans: list[BackfillSpan] | None = None,
    station_batch_size: int = _STATION_BATCH_SIZE,
) -> BackfillResult:
    """The chunked, resumable driver (§3A-§3C).

    ``spans`` defaults to a fresh ``discover_backfill_spans(adapter)`` call;
    accept it as a parameter so callers (and tests) needing a fixed span set
    can bypass repeated STAC discovery.
    """
    resolved_spans = spans if spans is not None else discover_backfill_spans(adapter)
    chunks_processed = 0
    chunks_skipped = 0
    rows_written = 0

    for span in resolved_spans:
        first_year = span.start.year
        last_year = (span.end - timedelta(microseconds=1)).year
        for year in range(first_year, last_year + 1):
            year_start, year_end = _year_bounds(year)
            window_start = max(span.start, year_start)
            window_end = min(span.end, year_end)
            if window_start >= window_end:
                continue
            expected_days = set(_days_in_range(window_start, window_end))

            for batch in _chunk(station_configs, station_batch_size):
                station_ids = [c.station_id for c in batch]
                covered = forcing_store.fetch_covered_days(
                    station_ids=station_ids,
                    source=span.product.value,
                    parameter=span.parameter,
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

                rows = adapter.fetch_products(
                    [span.product], missing, window_start, window_end, [span.parameter]
                )
                to_insert = [
                    r
                    for r in rows
                    if r.valid_time.date() not in covered.get(r.station_id, set())
                ]
                if to_insert:
                    forcing_store.store_forcing(to_insert)
                    rows_written += len(to_insert)
                chunks_processed += 1
                log.info(
                    "reanalysis_backfill.chunk_complete",
                    product=span.product.value,
                    parameter=span.parameter,
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
