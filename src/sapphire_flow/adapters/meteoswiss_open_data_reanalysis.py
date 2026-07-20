# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""MeteoSwiss open-data daily reanalysis adapter (Plan 071 T4; Plan 115b1
extends it with RhiresD + SrelD, the archive asset family, R discovery, and
the writer-side product-scoped fetch).

Ingests the daily gridded products of the
``ch.meteoschweiz.ogd-surface-derived-grid`` STAC collection — RprelimD
(preliminary daily precipitation), RhiresD (DEFINITIVE daily precipitation,
monthly publication), TabsD (daily mean 2 m temperature), TminD, TmaxD, SrelD
(relative sunshine duration) — basin-averages each via an injected
``ExactExtractGridExtractor``, and assembles ``RawHistoricalForcing`` rows with
the correct per-product ``ForcingSource`` tag and a deterministic content-hash
``version``.

The CRS reprojection (LV95 -> WGS84) is a guarded no-op on grids that already
carry geographic ``latitude``/``longitude`` DIMENSIONS; native MeteoSwiss LV95
grids (real files carry N/E dimensions in metres, PLUS 2D curvilinear lon/lat
AUXILIARY coordinates that are not themselves a usable regular grid) are
reprojected via the ``rioxarray`` accessor.

**Daily per-day item addressing (Plan 128, RprelimD live-tail fix,
2026-07-19)**: RprelimD (and any other daily-only, non-archive-backed
product) is fetched by ``_fetch_day_feature`` via a **direct STAC item-id
GET** (``items/{YYYYMMDD}-ch``, e.g. ``items/20260520-ch``) — never a
``properties.datetime``-filtered search. Live probing confirmed that field
drifts ~2 months forward of the item's real data date, so a search filtering
on it silently returns 0 features for a day that in fact exists (the
now-fixed defect). HTTP 404 on the id-fetch is the genuine gap (RprelimD has
a rolling **~2-month retention window**; older days age out). Separately,
MeteoSwiss publishes a per-day item **before** attaching that day's product
asset (an item-then-asset publication race that by nature affects only the
newest day(s)) — ``_rows_for_product`` degrades that to a ``WARNING``-logged
gap on the daily path only, never raising, so the scheduled
``ingest-weather-history`` run cannot crash on it. The archive-backed path
(``_rows_for_href`` via the yearly archive / "last" monthly family) treats an
absent archive-year href as an ``archive_year_gap`` and an absent last-family
month href as an ``archive_month_gap`` — both return no rows, not an error;
only a failed archive item fetch or asset download is a hard ``AdapterError``.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import rioxarray  # type: ignore[import-untyped]  # noqa: F401
import structlog
import xarray as xr

from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.historical_forcing import RawHistoricalForcing

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
        ExactExtractGridExtractor,
    )
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource

log = structlog.get_logger(__name__)


_ITEM_ID_DATE_RE = re.compile(r"(?P<date>\d{4}-?\d{2}-?\d{2})")


def _feature_day(feature: dict[str, object]) -> date | None:
    """Derive a per-day STAC item's calendar date: prefer
    ``properties.datetime`` (the canonical STAC field), falling back to an
    ISO/compact date embedded in the item ``id`` (e.g. ``2026-04-10-ch`` or
    ``20260410-ch``). Returns ``None`` when neither is parseable — callers
    must treat that as "no span for this item", not a crash (Plan 115b3
    §4C)."""
    properties = feature.get("properties", {})
    raw_datetime = properties.get("datetime") if isinstance(properties, dict) else None
    if isinstance(raw_datetime, str) and raw_datetime:
        try:
            return datetime.fromisoformat(raw_datetime.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    item_id = feature.get("id")
    if isinstance(item_id, str):
        match = _ITEM_ID_DATE_RE.search(item_id)
        if match is not None:
            token = match.group("date").replace("-", "")
            try:
                return datetime.strptime(token, "%Y%m%d").date()
            except ValueError:
                return None
    return None


def _next_link(links: object) -> str | None:
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, dict) and link.get("rel") == "next":
            href = link.get("href")
            if isinstance(href, str) and href:
                return href
    return None


def _days_in_range(start: UtcDatetime, end: UtcDatetime) -> list[date]:
    """Every calendar day whose midnight ``valid_time`` falls inside the
    half-open ``[start, end)`` window (the same inclusion rule the daily
    per-item loop uses)."""
    out: list[date] = []
    day = start.date()
    last_day = end.date()
    while day <= last_day:
        midnight = ensure_utc(datetime.combine(day, time(0, 0), tzinfo=UTC))
        if start <= midnight < end:
            out.append(day)
        day = day + timedelta(days=1)
    return out


@dataclass(frozen=True, kw_only=True, slots=True)
class _Product:
    """A daily MeteoSwiss product: raw NetCDF variable, canonical parameter,
    provenance tag, the STAC asset token used to locate its href, its grid
    family (documentation only — the CRS/reprojection path in ``_reproject``
    handles both families generically via the grid's own N/E dimensions), and
    whether it is served by the yearly ARCHIVE asset family.

    ``archive_backed`` is ``True`` for every product carried in the yearly
    archive (RhiresD + the ch01r temperature/sunshine grids) — these MUST be
    addressed through the archive/last family for any historical fetch, because
    the archive years are not published as per-day STAC items. Only ``RprelimD``
    (the preliminary live tail) is daily-only (Plan 115b1 §1B)."""

    token: str
    raw_var: str
    parameter: str
    source: ForcingSource
    grid: str
    archive_backed: bool


_PRODUCT_REGISTRY: tuple[_Product, ...] = (
    _Product(
        token="rprelimd",
        raw_var="RprelimD",
        parameter="precipitation",
        source=ForcingSource.METEOSWISS_RPRELIMD,
        grid="ch01h",
        archive_backed=False,
    ),
    _Product(
        token="rhiresd",
        raw_var="RhiresD",
        parameter="precipitation",
        source=ForcingSource.METEOSWISS_RHIRESD,
        grid="ch01h",
        archive_backed=True,
    ),
    _Product(
        token="tabsd",
        raw_var="TabsD",
        parameter="temperature",
        source=ForcingSource.METEOSWISS_TABSD,
        grid="ch01r",
        archive_backed=True,
    ),
    _Product(
        token="tmind",
        raw_var="TminD",
        parameter="temperature_min",
        source=ForcingSource.METEOSWISS_TMIND,
        grid="ch01r",
        archive_backed=True,
    ),
    _Product(
        token="tmaxd",
        raw_var="TmaxD",
        parameter="temperature_max",
        source=ForcingSource.METEOSWISS_TMAXD,
        grid="ch01r",
        archive_backed=True,
    ),
    _Product(
        token="sreld",
        raw_var="SrelD",
        parameter="relative_sunshine_duration",
        source=ForcingSource.METEOSWISS_SRELD,
        grid="ch01r",
        archive_backed=True,
    ),
)


# Every MeteoSwiss asset filename (archive / last-monthly / daily families)
# embeds a start/end date span — e.g.
# "...rhiresd_ch01h.swiss.lv95_20200101000000_20201231000000.nc", parameterised
# by product token + grid family. Captures BOTH tokens — ``discover_product_
# boundary`` uses only the max END (the high-water mark, §1D/§3A/§3C);
# ``discover_product_availability_range`` (Plan 115b3 §4C) additionally needs
# the min START (the earliest published date), since the RhiresD/RprelimD
# overlap window is bounded by RprelimD's rolling-tail start.
def _product_asset_span_re(token: str, grid: str) -> re.Pattern[str]:
    return re.compile(
        rf"{re.escape(token)}_{re.escape(grid)}\.swiss\.lv95_"
        rf"(?P<start>\d{{8}})\d{{6}}_(?P<end>\d{{8}})\d{{6}}"
    )


_ARCHIVE_ITEM_ID = "archive-ch"

_LV95_CRS = "EPSG:2056"
_WGS84_CRS = "EPSG:4326"


class MeteoSwissOpenDataReanalysisAdapter:
    """``WeatherReanalysisSource`` over the MeteoSwiss open-data daily grids."""

    NWP_SOURCE: ClassVar[str] = "meteoswiss_open_data_reanalysis"

    def __init__(
        self,
        *,
        stac_base_url: str,
        stac_collection: str,
        http_client: httpx.Client,
        extractor: ExactExtractGridExtractor,
        basins: dict[StationId, Basin],
        clock: Callable[[], UtcDatetime],
    ) -> None:
        self._stac_base_url = stac_base_url.rstrip("/")
        self._stac_collection = stac_collection
        self._http_client = http_client
        self._extractor = extractor
        self._basins = basins
        self._clock = clock
        self._archive_item_cache: dict[str, object] | None = None

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        requested = set(parameters)
        # Fail closed once a requested parameter maps to MORE THAN ONE product
        # (Plan 115b1 §0a/§1F) — this parameter-keyed path cannot disambiguate
        # which product a caller wants. RhiresD + RprelimD both serve
        # "precipitation"; that parameter must go through fetch_products(...)
        # instead. Every other canonical parameter still has exactly one
        # product and resolves here unchanged.
        ambiguous = sorted(
            parameter
            for parameter in requested
            if sum(1 for p in _PRODUCT_REGISTRY if p.parameter == parameter) > 1
        )
        if ambiguous:
            raise ConfigurationError(
                f"fetch_reanalysis: parameter(s) {ambiguous} map to more than "
                "one MeteoSwiss product — use fetch_products(...) instead "
                "(Plan 115b1 §1F)"
            )
        products = [p for p in _PRODUCT_REGISTRY if p.parameter in requested]
        # No supported parameter requested → nothing can be produced; return
        # before any STAC download.
        if not products:
            return []
        # Operational read path: only RhiresD has no per-day STAC item (it
        # publishes monthly), so it alone routes through the archive/last
        # family here. The ch01r temperature/sunshine grids still publish as
        # per-day items over this recent read window and resolve per-day —
        # unchanged from Plan 071. (Historical archive addressing for those
        # products is the WRITER path's job: see ``fetch_products``.)
        archive_products = [p for p in products if p.token == "rhiresd"]
        daily_products = [p for p in products if p.token != "rhiresd"]
        return self._fetch_range(
            archive_products, daily_products, station_configs, start, end
        )

    def fetch_products(
        self,
        products: list[ForcingSource],
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        """Writer-side product-scoped fetch (Plan 115b1 §0a/§1F).

        Selects EXACTLY the given ``products`` — the caller (Flow 6 ingest,
        the backfill) decides which product covers which date range, so this
        is never ambiguous the way parameter-keyed ``fetch_reanalysis`` would
        be once >1 product serves the same canonical parameter. ``parameters``
        additionally restricts which of those products' canonical parameters
        are actually wanted (both must match). Used ONLY by the writer side —
        the read-side ``WeatherReanalysisSource`` protocol is unaffected.
        """
        requested_products = set(products)
        requested_parameters = set(parameters)
        selected = [
            p
            for p in _PRODUCT_REGISTRY
            if p.source in requested_products and p.parameter in requested_parameters
        ]
        if not selected:
            return []
        # Writer path: EVERY archive-backed product (RhiresD + the ch01r
        # temperature/sunshine grids) resolves through the yearly archive/last
        # family so a historical product-year fetch selects
        # "...archive.<var>_<grid>...YYYY..." instead of silently gapping out on
        # absent per-day items (Plan 115b1 §1B — the 115b2 backfill depends on
        # this). Only RprelimD (the preliminary live tail) is daily-only.
        archive_products = [p for p in selected if p.archive_backed]
        daily_products = [p for p in selected if not p.archive_backed]
        return self._fetch_range(
            archive_products, daily_products, station_configs, start, end
        )

    def discover_rhiresd_boundary(self) -> UtcDatetime | None:
        """Discover R (Plan 115b1 §1D): the latest date for which the
        DEFINITIVE RhiresD product has been published.

        RhiresD publishes monthly (~3-6 week lag) — R is not a fixed offset,
        it is discovered by scanning every STAC item's assets for an
        ``rhiresd_ch01h...`` asset (across the archive / "last" monthly /
        daily asset families) and taking the maximum embedded END date.
        Returns ``None`` when no RhiresD asset exists yet (an empty
        collection) — callers must handle that (there is no definitive data
        at all; everything is preliminary). Thin wrapper over
        ``discover_product_boundary`` (Plan 115b2 §3A/§3C generalises this to
        every product's high-water mark).
        """
        return self.discover_product_boundary(ForcingSource.METEOSWISS_RHIRESD)

    def discover_product_boundary(self, product: ForcingSource) -> UtcDatetime | None:
        """Discover ``product``'s published high-water mark: the latest date
        for which ANY asset of that product has been published (Plan 115b2
        §3A/§3C — extends 1D's RhiresD-only ``R`` discovery to every product,
        so the chunked backfill never requests a date a product does not yet
        serve).

        Scans every STAC item's assets (across the archive / "last" monthly /
        daily asset families, following pagination) for an asset matching
        ``{token}_{grid}...`` and takes the maximum embedded END date. Returns
        ``None`` when no asset for this product exists yet (an empty
        collection) — callers must handle that.
        """
        spans = self._scan_product_asset_spans(product)
        if not spans:
            return None
        latest = max(end for _, end in spans)
        return ensure_utc(datetime.combine(latest, time(0, 0), tzinfo=UTC))

    def discover_product_availability_range(
        self, product: ForcingSource
    ) -> tuple[date, date] | None:
        """Discover ``product``'s full published availability: the earliest
        and latest date any asset of that product has ever covered (Plan
        115b3 §4C — generalises the same STAC-scanning infrastructure
        ``discover_product_boundary`` uses from "latest only" to a full
        ``(earliest_start, latest_end)`` range).

        Used to compute the RhiresD/RprelimD overlap window: the live-tail
        product (`RprelimD`) is a rolling ~2-month tail, so its *earliest*
        available date — not just its high-water mark — determines how much
        of RhiresD's definitive-but-lagged coverage it actually overlaps.
        Returns ``None`` when no asset for this product exists yet.
        """
        spans = self._scan_product_asset_spans(product)
        if not spans:
            return None
        earliest = min(start for start, _ in spans)
        latest = max(end for _, end in spans)
        return earliest, latest

    def _scan_product_asset_spans(
        self, product: ForcingSource
    ) -> list[tuple[date, date]]:
        """Scan every STAC item's assets (archive / "last" monthly / daily
        families, following pagination) for ``product``'s asset, returning
        every embedded ``(start, end)`` date span found. Shared by
        ``discover_product_boundary`` (latest END) and
        ``discover_product_availability_range`` (earliest START, latest END)
        so both read from the same single scan implementation.

        Archive/"last"-monthly assets embed a start/end span in the filename
        (matched by ``span_re``). Per-day items (RprelimD's daily-only family,
        Plan 115b1 §1B) do NOT — the filename carries a single date, e.g.
        ``RprelimD_ch.swiss.lv95_2026-04-10``. When no filename span matches
        but the item still carries this product's asset (same raw_var/token
        match ``_asset_href`` uses), fall back to a single-day ``(day, day)``
        span derived from the item's ``properties.datetime`` (or, failing
        that, an ISO date embedded in the item id) — otherwise a daily-only
        product like RprelimD would scan zero spans and
        ``discover_product_availability_range`` would silently report ``None``
        (Plan 115b3 §4C)."""
        prod = next((p for p in _PRODUCT_REGISTRY if p.source is product), None)
        if prod is None:
            raise AdapterError(f"unknown product source={product.value}")
        span_re = _product_asset_span_re(prod.token, prod.grid)

        url: str | None = (
            f"{self._stac_base_url}/collections/{self._stac_collection}/items?limit=100"
        )
        spans: list[tuple[date, date]] = []
        seen: set[str] = set()
        max_pages = 50
        while url is not None and len(seen) < max_pages and url not in seen:
            seen.add(url)
            try:
                resp = self._http_client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                raise AdapterError(
                    f"STAC search failed during boundary discovery for "
                    f"product={prod.token}: {exc}"
                ) from exc
            body = resp.json()
            for feature in body.get("features", []):
                spans.extend(self._spans_for_feature(feature, prod, span_re))
            url = _next_link(body.get("links", []))

        return spans

    def _spans_for_feature(
        self,
        feature: dict[str, object],
        prod: _Product,
        span_re: re.Pattern[str],
    ) -> list[tuple[date, date]]:
        assets = feature.get("assets", {})
        if not isinstance(assets, dict):
            return []
        # Parse BOTH the asset key AND its href filename — STAC keys may
        # be opaque (e.g. "data") while the date span lives only in the
        # href filename (or vice versa). Checking only one side would
        # miss the boundary and silently fall back to RprelimD for the
        # whole window (Plan 115b1 §1D).
        found: list[tuple[date, date]] = []
        for key, asset in assets.items():
            href = str(asset.get("href", "")) if isinstance(asset, dict) else ""
            for candidate in (str(key), href):
                match = span_re.search(candidate)
                if match is None:
                    continue
                start = datetime.strptime(match.group("start"), "%Y%m%d").date()
                end = datetime.strptime(match.group("end"), "%Y%m%d").date()
                found.append((start, end))
                break
        if found:
            return found

        # No archive-style span anywhere in this item — check whether it
        # still carries THIS product's asset (a per-day item) before
        # deriving a single-day fallback span.
        if self._asset_href(feature, prod) is None:
            return []
        day = _feature_day(feature)
        return [(day, day)] if day is not None else []

    def fetch_archive_year(
        self,
        product: ForcingSource,
        year: int,
        station_configs: list[StationWeatherSource],
    ) -> list[RawHistoricalForcing]:
        """Fetch one full year of ``product`` via the STAC ARCHIVE asset
        family (Plan 115b1 §1B) — a per-year NetCDF, not per-day items.

        Foundation for the 1981-present backfill (Plan 115b2 owns the actual
        multi-year orchestration); this method is the adapter capability it
        will call, addressed and extraction-tested on its own here.
        """
        prod = next((p for p in _PRODUCT_REGISTRY if p.source is product), None)
        if prod is None:
            raise AdapterError(f"unknown product source={product.value}")

        matching = self._matching_configs(station_configs)
        if not matching:
            return []

        feature = self._fetch_archive_item()
        href = self._archive_asset_href_for_year(feature, prod, year)
        if href is None:
            log.info("reanalysis.archive_year_gap", product=prod.token, year=year)
            return []

        cycle_time = self._clock()
        return self._rows_for_href(href, prod, matching, cycle_time)

    def _matching_configs(
        self, station_configs: list[StationWeatherSource]
    ) -> list[StationWeatherSource]:
        # Only process configs that declare THIS source, are REANALYSIS-role,
        # are active, AND request basin-average extraction (the only
        # representation this adapter emits — _rows_for_href hard-codes
        # BASIN_AVERAGE). A mixed config list (FORECAST/CAMELS/inactive/
        # non-basin sources, as service callers pass through verbatim) must
        # not yield reanalysis rows.
        return [
            c
            for c in station_configs
            if c.nwp_source == self.NWP_SOURCE
            and c.role is WeatherSourceRole.REANALYSIS
            and c.status == WeatherSourceStatus.ACTIVE
            and c.extraction_type == SpatialRepresentation.BASIN_AVERAGE
        ]

    def _fetch_range(
        self,
        archive_products: list[_Product],
        daily_products: list[_Product],
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[RawHistoricalForcing]:
        """Fetch a date range, routing ``archive_products`` through the yearly
        archive/last family (per-year NetCDFs — the only addressing that works
        for historical years, which are not published as per-day items) and
        ``daily_products`` through the per-day STAC item loop. The caller
        (``fetch_reanalysis`` vs ``fetch_products``) decides the split (Plan
        115b1 §1B)."""
        matching = self._matching_configs(station_configs)
        if not matching:
            return []

        rows: list[RawHistoricalForcing] = []
        for product in archive_products:
            rows.extend(self._fetch_archive_backed_range(product, matching, start, end))
        if daily_products:
            rows.extend(
                self._fetch_daily_items_range(daily_products, matching, start, end)
            )

        log.info(
            "reanalysis.fetch_completed",
            nwp_source=self.NWP_SOURCE,
            start=start.isoformat(),
            end=end.isoformat(),
            row_count=len(rows),
        )
        return rows

    def _fetch_daily_items_range(
        self,
        products: list[_Product],
        matching: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[RawHistoricalForcing]:
        cycle_time = self._clock()
        rows: list[RawHistoricalForcing] = []
        for day in _days_in_range(start, end):
            day_iso = day.isoformat()
            feature = self._fetch_day_feature(day_iso)
            if feature is not None:
                for product in products:
                    rows.extend(
                        self._rows_for_product(
                            feature, product, day_iso, matching, cycle_time
                        )
                    )
        return rows

    def _fetch_archive_backed_range(
        self,
        product: _Product,
        matching: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[RawHistoricalForcing]:
        """Address an archive-backed product via the yearly ARCHIVE family
        where the year is already fully archived, falling back to the monthly
        "last" family for the current/recent months that are not (Plan
        115b1 §1B/§1G). Applies to RhiresD (which has no per-day item at all)
        and to the ch01r temperature/sunshine grids for any historical fetch —
        their archive years are likewise not published as per-day items. Each
        resolved asset covers a whole year/month; fetched rows are filtered
        back down to ``[start, end)``."""
        cycle_time = self._clock()
        days = _days_in_range(start, end)
        if not days:
            return []

        years = sorted({d.year for d in days})
        months = sorted({(d.year, d.month) for d in days})

        rows: list[RawHistoricalForcing] = []
        covered_years: set[int] = set()
        archive_feature = self._fetch_archive_item()
        for year in years:
            href = self._archive_asset_href_for_year(archive_feature, product, year)
            if href is not None:
                rows.extend(self._rows_for_href(href, product, matching, cycle_time))
                covered_years.add(year)

        for year, month in months:
            if year in covered_years:
                continue
            href = self._last_family_asset_href(product, year, month)
            if href is None:
                log.info(
                    "reanalysis.archive_month_gap",
                    product=product.token,
                    year=year,
                    month=month,
                )
                continue
            rows.extend(self._rows_for_href(href, product, matching, cycle_time))

        return [r for r in rows if start <= r.valid_time < end]

    def _fetch_day_feature(self, day_iso: str) -> dict[str, object] | None:
        """Fetch the per-day STAC item by its deterministic id
        (``{YYYYMMDD}-ch``, e.g. ``20260520-ch``) — a direct ``GET``, never a
        ``properties.datetime``-filtered search. MeteoSwiss keys per-day items
        by the DATA date in the item id, but ``properties.datetime`` drifts
        ~2 months forward of that date in production (Plan 128 Probe A) —
        a search filtering on ``properties.datetime`` silently returns 0
        features for the requested day. HTTP 404 on the id-fetch is the
        genuine "no data for this day" gap (not yet published, or aged out of
        the ~2-month rolling retention window). No bounded ``?datetime=``
        range fallback (Plan 128 grill-me #1) — that filter is the defect."""
        item_id = f"{day_iso.replace('-', '')}-ch"
        url = (
            f"{self._stac_base_url}/collections/{self._stac_collection}/items/{item_id}"
        )
        try:
            resp = self._http_client.get(url)
        except Exception as exc:
            raise AdapterError(f"STAC item fetch failed for {item_id}: {exc}") from exc
        if resp.status_code == 404:
            log.info("reanalysis.day_gap", day=day_iso)
            return None
        try:
            resp.raise_for_status()
        except Exception as exc:
            raise AdapterError(f"STAC item fetch failed for {item_id}: {exc}") from exc
        feature: dict[str, object] = resp.json()
        return feature

    def _rows_for_product(
        self,
        feature: dict[str, object],
        product: _Product,
        day_iso: str,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> list[RawHistoricalForcing]:
        href = self._asset_href(feature, product)
        if href is None:
            # Daily path only (Plan 128 A1(2), Probe B, live 2026-07-19):
            # MeteoSwiss publishes a per-day item BEFORE attaching the
            # product's asset — a routine item-then-asset publication race
            # affecting only the newest day(s), not a matcher bug. Degrade to
            # a gap at WARNING (operator-visible, distinct from the routine
            # INFO `day_gap`) so the scheduled ingest never crashes on it. A
            # genuine asset-matcher bug would instead fail EVERY day, which
            # surfaces as sustained warnings. The archive path
            # (``_rows_for_href`` called directly, never through here) is
            # unchanged — an absent archive/monthly asset href is an
            # ``archive_year_gap`` / ``archive_month_gap`` (returns no rows);
            # only a failed item fetch or asset download is a hard
            # ``AdapterError``.
            log.warning(
                "reanalysis.day_asset_absent",
                product=product.token,
                day=day_iso,
            )
            return []
        return self._rows_for_href(href, product, station_configs, cycle_time)

    def _rows_for_href(
        self,
        href: str,
        product: _Product,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> list[RawHistoricalForcing]:
        data = self._download(href)
        version = hashlib.sha256(data).hexdigest()[:16]
        grid = self._open_grid(data, product)
        extracted = self._extractor.extract(
            grid,
            station_configs,
            self._basins,
            cycle_time,
            self.NWP_SOURCE,
        )

        rows: list[RawHistoricalForcing] = []
        for station_id, forecast in extracted.items():
            for record in forecast.values.iter_rows(named=True):
                rows.append(
                    RawHistoricalForcing(
                        station_id=station_id,
                        source=product.source.value,
                        version=version,
                        valid_time=ensure_utc(record["valid_time"]),
                        parameter=product.parameter,
                        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                        band_id=None,
                        member_id=None,
                        value=float(record["value"]),
                    )
                )
        return rows

    @staticmethod
    def _asset_href(feature: dict[str, object], product: _Product) -> str | None:
        assets = feature.get("assets", {})
        if not isinstance(assets, dict):
            return None
        for key, asset in assets.items():
            href = str(asset.get("href", "")) if isinstance(asset, dict) else ""
            if str(key).startswith(f"{product.raw_var}_") or (
                f"{product.token}_" in href
            ):
                return href
        return None

    def _fetch_archive_item(self) -> dict[str, object]:
        """Fetch (and cache) the single ``archive-ch`` STAC item — one item
        carrying one asset per (product, year) for every fully-archived year
        (Plan 115b1 §1B)."""
        if self._archive_item_cache is not None:
            return self._archive_item_cache
        url = (
            f"{self._stac_base_url}/collections/{self._stac_collection}"
            f"/items/{_ARCHIVE_ITEM_ID}"
        )
        try:
            resp = self._http_client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            raise AdapterError(f"STAC archive item fetch failed: {exc}") from exc
        feature = resp.json()
        self._archive_item_cache = feature
        return feature

    @staticmethod
    def _archive_asset_href_for_year(
        feature: dict[str, object], product: _Product, year: int
    ) -> str | None:
        assets = feature.get("assets", {})
        if not isinstance(assets, dict):
            return None
        # Archive filenames span the full year, e.g.
        # "...rhiresd_ch01h.swiss.lv95_19810101000000_19811231000000.nc" — key
        # on the product token plus the year-start timestamp, which is unique
        # to a single (product, year) asset.
        year_start_token = f"{product.token}_{product.grid}.swiss.lv95_{year}0101"
        for key, asset in assets.items():
            href = str(asset.get("href", "")) if isinstance(asset, dict) else ""
            if year_start_token in str(key) or year_start_token in href:
                return href
        return None

    def _last_family_asset_href(
        self, product: _Product, year: int, month: int
    ) -> str | None:
        """Resolve ``product``'s asset for one month via the "last" monthly
        family item (``{YYYYMM}-ch``) — the recent months not yet folded into
        the yearly archive (Plan 115b1 §1B/§1G)."""
        item_id = f"{year:04d}{month:02d}-ch"
        url = (
            f"{self._stac_base_url}/collections/{self._stac_collection}/items/{item_id}"
        )
        try:
            resp = self._http_client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            raise AdapterError(
                f"STAC 'last'-family item fetch failed for {item_id}: {exc}"
            ) from exc
        feature = resp.json()
        assets = feature.get("assets", {})
        if not isinstance(assets, dict):
            return None
        month_start_token = (
            f"{product.token}_{product.grid}.swiss.lv95_{year:04d}{month:02d}01"
        )
        for key, asset in assets.items():
            href = str(asset.get("href", "")) if isinstance(asset, dict) else ""
            if month_start_token in str(key) or month_start_token in href:
                return href
        return None

    def _download(self, href: str) -> bytes:
        try:
            resp = self._http_client.get(href)
            resp.raise_for_status()
        except Exception as exc:
            raise AdapterError(f"asset download failed for {href}: {exc}") from exc
        return resp.content

    def _open_grid(self, data: bytes, product: _Product) -> xr.Dataset:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as fh:
            fh.write(data)
            path = Path(fh.name)
        try:
            ds = xr.open_dataset(path).load()
        except Exception as exc:
            raise AdapterError(
                f"failed to parse NetCDF for product={product.token}: {exc}"
            ) from exc
        finally:
            path.unlink(missing_ok=True)

        if product.raw_var not in ds.data_vars:
            raise AdapterError(
                f"expected raw variable {product.raw_var!r} absent from NetCDF "
                f"for product={product.token}; got {list(ds.data_vars)}"
            )
        ds = ds.rename({product.raw_var: product.parameter})
        # Real MeteoSwiss NetCDFs carry the time dimension as "time"; the
        # extraction path (and the synthetic test fixtures) expect
        # "valid_time". Synthetic fixtures already use "valid_time" directly,
        # so this is a no-op for them.
        if "time" in ds.dims and "valid_time" not in ds.dims:
            ds = ds.rename({"time": "valid_time"})
        # Real MeteoSwiss NetCDFs carry CF ancillary data variables (grid-mapping
        # / CRS var, *_bnds, swiss_lv95_coordinates, ...) alongside the product
        # var. The extractor iterates every data_var, so reduce the dataset to the
        # single renamed product var before reprojection/extraction. (This also
        # keeps any 2D curvilinear lat/lon AUXILIARY coordinates real MeteoSwiss
        # grids carry — see ``_reproject`` — since they share the product's dims.)
        ds = ds[[product.parameter]]
        return self._reproject(ds)

    @staticmethod
    def _reproject(ds: xr.Dataset) -> xr.Dataset:
        # Grids already carrying geographic lat/lon as actual DIMENSIONS are
        # WGS84 already — pass through. Checked via ds.dims, NOT ds.coords: a
        # real MeteoSwiss LV95 grid ALSO carries 2D curvilinear lon/lat
        # AUXILIARY coordinates (present in ds.coords) alongside its true N/E
        # dimensions — those must not be mistaken for a usable regular grid;
        # only DIMENSION coordinates index a grid exactextract can consume.
        if "latitude" in ds.dims and "longitude" in ds.dims:
            return ds
        if "lat" in ds.dims and "lon" in ds.dims:
            return ds.rename({"lat": "latitude", "lon": "longitude"})
        # Native MeteoSwiss LV95 grids carry N (northing) / E (easting) as the
        # TRUE regular spatial dimensions, in metres (both the ch01h and
        # ch01r grid families — they differ only in resolution/extent, not in
        # dimension naming). Drop the stale 2D curvilinear lon/lat auxiliary
        # coordinates first (they do not survive reprojection onto the new
        # grid anyway, and would otherwise collide with the renamed x/y).
        if "E" in ds.dims and "N" in ds.dims:
            ds = ds.drop_vars([c for c in ("lat", "lon") if c in ds.coords])
            ds = ds.rename({"E": "x", "N": "y"})
        try:
            reprojected = ds.rio.write_crs(_LV95_CRS).rio.reproject(_WGS84_CRS)
        except Exception as exc:
            raise AdapterError(f"LV95->WGS84 reprojection failed: {exc}") from exc
        return reprojected.rename({"x": "longitude", "y": "latitude"})
