# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""MeteoSwiss open-data daily reanalysis adapter (Plan 071 T4).

Ingests the four daily gridded products of the
``ch.meteoschweiz.ogd-surface-derived-grid`` STAC collection — RprelimD
(preliminary daily precipitation), TabsD (daily mean 2 m temperature), TminD,
TmaxD — basin-averages each via an injected ``ExactExtractGridExtractor``, and
assembles ``RawHistoricalForcing`` rows with the correct per-product
``ForcingSource`` tag and a deterministic content-hash ``version``.

The CRS reprojection (LV95 -> WGS84) is a guarded no-op on grids that already
carry geographic ``latitude``/``longitude`` coordinates; native MeteoSwiss LV95
grids are reprojected via the ``rioxarray`` accessor.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import rioxarray  # type: ignore[import-untyped]  # noqa: F401
import structlog
import xarray as xr

from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
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


def _item_updated(feature: dict[str, object]) -> str:
    properties = feature.get("properties", {})
    if isinstance(properties, dict):
        return str(properties.get("updated", ""))
    return ""


def _next_link(links: object) -> str | None:
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, dict) and link.get("rel") == "next":
            href = link.get("href")
            if isinstance(href, str) and href:
                return href
    return None


@dataclass(frozen=True, kw_only=True, slots=True)
class _Product:
    """A daily MeteoSwiss product: raw NetCDF variable, canonical parameter,
    provenance tag, and the STAC asset token used to locate its href."""

    token: str
    raw_var: str
    parameter: str
    source: ForcingSource


_PRODUCT_REGISTRY: tuple[_Product, ...] = (
    _Product(
        token="rprelimd",
        raw_var="RprelimD",
        parameter="precipitation",
        source=ForcingSource.METEOSWISS_RPRELIMD,
    ),
    _Product(
        token="tabsd",
        raw_var="TabsD",
        parameter="temperature",
        source=ForcingSource.METEOSWISS_TABSD,
    ),
    _Product(
        token="tmind",
        raw_var="TminD",
        parameter="temperature_min",
        source=ForcingSource.METEOSWISS_TMIND,
    ),
    _Product(
        token="tmaxd",
        raw_var="TmaxD",
        parameter="temperature_max",
        source=ForcingSource.METEOSWISS_TMAXD,
    ),
)

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

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        requested = set(parameters)
        products = [p for p in _PRODUCT_REGISTRY if p.parameter in requested]
        # No supported parameter requested → nothing can be produced; return
        # before any STAC download.
        if not products:
            return []
        cycle_time = self._clock()

        # Only process configs that declare THIS source, are active, AND request
        # basin-average extraction (the only representation this adapter emits —
        # _rows_for_product hard-codes BASIN_AVERAGE). A mixed config list
        # (ICON/CAMELS/inactive/non-basin sources, as service callers pass
        # through verbatim) must not yield reanalysis rows; no match returns []
        # without downloading.
        matching = [
            c
            for c in station_configs
            if c.nwp_source == self.NWP_SOURCE
            and c.status == WeatherSourceStatus.ACTIVE
            and c.extraction_type == SpatialRepresentation.BASIN_AVERAGE
        ]
        if not matching:
            return []

        rows: list[RawHistoricalForcing] = []
        # Daily MeteoSwiss rows are valid at midnight. Iterate every calendar
        # day whose midnight falls inside the half-open [start, end) window —
        # start.date()..end.date() INCLUSIVE as candidates, gated on the row's
        # actual valid instant. Backward-compatible for midnight-aligned ranges.
        day = start.date()
        last_day = end.date()
        while day <= last_day:
            midnight = ensure_utc(datetime.combine(day, time(0, 0), tzinfo=UTC))
            if start <= midnight < end:
                day_iso = day.isoformat()
                feature = self._fetch_day_feature(day_iso)
                if feature is not None:
                    for product in products:
                        rows.extend(
                            self._rows_for_product(
                                feature, product, day_iso, matching, cycle_time
                            )
                        )
            day = day + timedelta(days=1)

        log.info(
            "reanalysis.fetch_completed",
            nwp_source=self.NWP_SOURCE,
            start=start.isoformat(),
            end=end.isoformat(),
            row_count=len(rows),
        )
        return rows

    def _fetch_day_feature(self, day_iso: str) -> dict[str, object] | None:
        url: str | None = (
            f"{self._stac_base_url}/collections/{self._stac_collection}/items"
            f"?datetime={day_iso}T00:00:00Z&limit=100"
        )
        # Accumulate features across STAC pages, following ``rel == "next"``
        # links. The per-day query usually returns a single page; the loop is
        # capped and de-duplicates hrefs to guard against pagination cycles.
        features: list[dict[str, object]] = []
        seen: set[str] = set()
        max_pages = 50
        while url is not None and len(seen) < max_pages and url not in seen:
            seen.add(url)
            try:
                resp = self._http_client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                raise AdapterError(f"STAC search failed for {day_iso}: {exc}") from exc
            body = resp.json()
            features.extend(body.get("features", []))
            url = _next_link(body.get("links", []))

        if not features:
            log.info("reanalysis.day_gap", day=day_iso)
            return None
        # Most-recently-published item wins when a day has revised content;
        # the content-hash version makes exact-duplicate republications no-ops.
        return max(features, key=_item_updated)

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
            raise AdapterError(f"no asset for product={product.token} on day={day_iso}")

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
        # Real MeteoSwiss NetCDFs carry CF ancillary data variables (grid-mapping
        # / CRS var, *_bnds, swiss_lv95_coordinates, ...) alongside the product
        # var. The extractor iterates every data_var, so reduce the dataset to the
        # single renamed product var before reprojection/extraction.
        ds = ds[[product.parameter]]
        return self._reproject(ds)

    @staticmethod
    def _reproject(ds: xr.Dataset) -> xr.Dataset:
        # Grids already carrying geographic lat/lon are WGS84 — pass through.
        if "latitude" in ds.coords and "longitude" in ds.coords:
            return ds
        if "lat" in ds.coords and "lon" in ds.coords:
            return ds.rename({"lat": "latitude", "lon": "longitude"})
        try:
            reprojected = ds.rio.write_crs(_LV95_CRS).rio.reproject(_WGS84_CRS)
        except Exception as exc:
            raise AdapterError(f"LV95->WGS84 reprojection failed: {exc}") from exc
        return reprojected.rename({"x": "longitude", "y": "latitude"})
