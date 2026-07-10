"""Route-C collector adapter for BAFU's public operational forecast plots
(Plan 111, Status override 2026-07-10).

Scrapes the unauthenticated ``hydrodaten.admin.ch`` forecast endpoints:

1. ``GET /web-hydro-maps/hydro_sensor_pq_forecast.geojson`` — the ~54-station
   forecast inventory + ``meta.produced_at``.
2. ``GET /plots/{variant}/{key}_{variant}_en.json`` — the Plotly figure for
   one station's ``q_forecast`` (discharge, all stations) or ``p_forecast``
   (level, lake/level stations only). A missing variant returns HTTP 404,
   which is *not* an error — the caller gets ``None`` back.

EVALUATION-ONLY. This adapter never writes to the operational DB and never
mints a ``ModelId`` — see ``flows/collect_bafu_forecasts.py`` for the
quarantined archive write path.

Safeguard #1 (identifying User-Agent) and #4 (polite retry cap) are
implemented here; safeguards #2 (quarantined archive) and #4's rate-limit
delay live in the flow, which controls fetch cadence across stations.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from sapphire_flow import __version__
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.bafu_forecast import (
    BafuForecastRow,
    BafuForecastStation,
    BafuForecastVariant,
    BafuIcon,
    BafuMetric,
    BafuStationInventory,
    BafuVariantFetch,
)
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

# Safeguard #1 (Plan 111 Status override): an honest, identifying User-Agent
# on every request, so BAFU can see who is scraping and object. Sent
# per-request (not just at client construction) so it is present regardless
# of how the caller built the injected httpx.Client.
USER_AGENT = (
    f"SAPPHIRE-Flow/{__version__} (hydrosolutions; marti@hydrosolutions.ch) "
    "forecast-benchmark-collector"
)

_STATIONS_URL = (
    "https://www.hydrodaten.admin.ch/web-hydro-maps/hydro_sensor_pq_forecast.geojson"
)
_PLOT_URL_TEMPLATE = (
    "https://www.hydrodaten.admin.ch/plots/{variant}/{key}_{variant}_en.json"
)

# The variant defines the parameter of the series, independent of the
# station's own declared "primary" metric on the map (a lake station's
# q_forecast, if present, is still discharge).
_VARIANT_METRIC: dict[BafuForecastVariant, BafuMetric] = {
    "q_forecast": "discharge_ms",
    "p_forecast": "masl",
}
# Fallback unit when a trace's own meta.unit is empty (seen on the
# percentile-band fill trace, which carries no unit of its own).
_VARIANT_DEFAULT_UNIT: dict[BafuForecastVariant, str] = {
    "q_forecast": "m³/s",
    "p_forecast": "m ü.M.",
}

_FORECAST_ANNOTATION_PREFIX = "Forecast as of"

# The station key is interpolated into archive filesystem paths, and it is
# parsed from an unauthenticated public feed. Constrain it to the shape BAFU
# actually uses (mirrors adapters/hydro_scraper.py's _SITE_CODE_RE) so a
# spoofed/MITM'd feed cannot smuggle a path-traversal key (e.g. "../../etc/x")
# out of the quarantined archive dir.
_STATION_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")


class _NotFoundError(Exception):
    """Internal signal: HTTP 404 — the variant is absent for this station,
    not an error."""


# ---------------------------------------------------------------------------
# Pydantic boundary models
# ---------------------------------------------------------------------------


class _StationPropertiesModel(BaseModel):
    key: str
    label: str
    icon: BafuIcon
    metric: BafuMetric
    unit: str
    plot: str


class _StationFeatureModel(BaseModel):
    properties: _StationPropertiesModel


class _StationMetaModel(BaseModel):
    produced_at: str


class _StationCollectionModel(BaseModel):
    features: list[_StationFeatureModel]
    meta: _StationMetaModel


class _PlotTraceModel(BaseModel):
    name: str
    x: list[str]
    y: list[float | None]
    meta: dict[str, Any] = {}


class _PlotAnnotationModel(BaseModel):
    text: str | None = None
    # `x` is ISO8601 for the issue-time annotation, but a bare paper-relative
    # float (e.g. 0.0) for other annotations (the y-axis unit label) — accept
    # both and only ever parse it as a date once we've matched the "Forecast
    # as of" text prefix.
    x: str | float | None = None


class _PlotLayoutModel(BaseModel):
    annotations: list[_PlotAnnotationModel] = []


class _PlotFigureModel(BaseModel):
    layout: _PlotLayoutModel
    data: list[_PlotTraceModel]


class _PlotPayloadModel(BaseModel):
    plot: _PlotFigureModel


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class BafuForecastAdapter:
    def __init__(
        self,
        http_client: httpx.Client,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        retry_delay_seconds: float = 1.0,
        max_retries: int = 2,
    ) -> None:
        self._http_client = http_client
        self._sleeper = sleeper
        self._retry_delay_seconds = retry_delay_seconds
        self._max_retries = max_retries

    def fetch_station_inventory(self) -> BafuStationInventory:
        log.info("bafu_forecast.inventory_fetch_started", url=_STATIONS_URL)
        response = self._get_with_retries(_STATIONS_URL)

        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"BAFU forecast station GeoJSON at {_STATIONS_URL} is not "
                f"valid JSON: {exc}"
            ) from exc

        try:
            parsed = _StationCollectionModel.model_validate(payload)
        except ValidationError as exc:
            raise AdapterError(
                f"BAFU forecast station GeoJSON at {_STATIONS_URL} failed "
                f"schema validation: {exc}"
            ) from exc

        try:
            produced_at = ensure_utc(datetime.fromisoformat(parsed.meta.produced_at))
        except (ValueError, TypeError) as exc:
            raise AdapterError(
                "BAFU forecast station GeoJSON meta.produced_at is not a "
                f"parseable ISO8601 timestamp: {parsed.meta.produced_at!r}"
            ) from exc

        stations = [
            BafuForecastStation(
                key=self._validate_station_key(feature.properties.key),
                label=feature.properties.label,
                icon=feature.properties.icon,
                metric=feature.properties.metric,
                unit=feature.properties.unit,
                plot_path=feature.properties.plot,
            )
            for feature in parsed.features
        ]
        log.info(
            "bafu_forecast.inventory_fetch_completed",
            station_count=len(stations),
            produced_at=produced_at.isoformat(),
        )
        return BafuStationInventory(stations=stations, produced_at=produced_at)

    def fetch_variant_forecast(
        self,
        station_key: str,
        variant: BafuForecastVariant,
        produced_at: UtcDatetime,
    ) -> BafuVariantFetch | None:
        url = _PLOT_URL_TEMPLATE.format(variant=variant, key=station_key)
        try:
            response = self._get_with_retries(url)
        except _NotFoundError:
            log.debug(
                "bafu_forecast.variant_absent", station_key=station_key, variant=variant
            )
            return None

        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"BAFU {variant} payload for station {station_key} at {url} "
                f"is not valid JSON: {exc}"
            ) from exc

        try:
            parsed = _PlotPayloadModel.model_validate(payload)
        except ValidationError as exc:
            raise AdapterError(
                f"BAFU {variant} payload for station {station_key} at {url} "
                f"failed schema validation: {exc}"
            ) from exc

        issued_at = self._extract_issued_at(parsed, station_key, variant)
        metric = _VARIANT_METRIC[variant]
        rows = [
            BafuForecastRow(
                station_key=station_key,
                metric=metric,
                unit=self._trace_unit(trace, variant),
                issued_at=issued_at,
                produced_at=produced_at,
                valid_time=self._parse_valid_time(x, station_key, variant),
                trace_name=trace.name,
                point_index=point_index,
                value=y,
            )
            for trace in parsed.plot.data
            for point_index, (x, y) in enumerate(
                self._trace_points(trace, station_key, variant)
            )
        ]
        return BafuVariantFetch(
            station_key=station_key,
            variant=variant,
            metric=metric,
            issued_at=issued_at,
            rows=rows,
            raw_payload=payload,
        )

    @staticmethod
    def _trace_points(
        trace: _PlotTraceModel, station_key: str, variant: BafuForecastVariant
    ) -> list[tuple[str, float | None]]:
        # zip(strict=True) raises a bare ValueError on an x/y length mismatch;
        # wrap it in AdapterError so a single malformed trace is isolated by the
        # flow's per-station AdapterError handler instead of aborting the run.
        try:
            return list(zip(trace.x, trace.y, strict=True))
        except ValueError as exc:
            raise AdapterError(
                f"BAFU {variant} payload for station {station_key} trace "
                f"{trace.name!r} has mismatched x/y lengths "
                f"({len(trace.x)} vs {len(trace.y)})"
            ) from exc

    @staticmethod
    def _validate_station_key(key: str) -> str:
        if not _STATION_KEY_RE.match(key):
            raise AdapterError(
                f"BAFU station key {key!r} does not match the expected "
                f"format {_STATION_KEY_RE.pattern} — refusing to interpolate "
                "it into an archive path (possible spoofed/hijacked feed)"
            )
        return key

    @staticmethod
    def _parse_valid_time(
        x: str, station_key: str, variant: BafuForecastVariant
    ) -> UtcDatetime:
        # Wrapped in AdapterError (not a bare ValueError) so a single station
        # with a malformed trace timestamp is isolated by the flow's per-station
        # AdapterError handler instead of aborting the whole collection run.
        try:
            return ensure_utc(datetime.fromisoformat(x))
        except (ValueError, TypeError) as exc:
            raise AdapterError(
                f"BAFU {variant} payload for station {station_key} has an "
                f"unparseable trace timestamp: {x!r}"
            ) from exc

    @staticmethod
    def _trace_unit(trace: _PlotTraceModel, variant: BafuForecastVariant) -> str:
        unit = trace.meta.get("unit")
        if isinstance(unit, str) and unit:
            return unit
        return _VARIANT_DEFAULT_UNIT[variant]

    @staticmethod
    def _extract_issued_at(
        parsed: _PlotPayloadModel, station_key: str, variant: BafuForecastVariant
    ) -> UtcDatetime:
        for annotation in parsed.plot.layout.annotations:
            if annotation.text is None or not annotation.text.startswith(
                _FORECAST_ANNOTATION_PREFIX
            ):
                continue
            if not isinstance(annotation.x, str):
                break
            try:
                return ensure_utc(datetime.fromisoformat(annotation.x))
            except (ValueError, TypeError) as exc:
                raise AdapterError(
                    f"BAFU {variant} payload for station {station_key} has an "
                    f"unparseable issue-time annotation.x: {annotation.x!r}"
                ) from exc
        raise AdapterError(
            f"BAFU {variant} payload for station {station_key} has no "
            f"'{_FORECAST_ANNOTATION_PREFIX}' annotation to derive issued_at from"
        )

    def _get_with_retries(self, url: str) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http_client.get(
                    url, headers={"User-Agent": USER_AGENT}
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning(
                    "bafu_forecast.request_failed",
                    url=url,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < self._max_retries:
                    self._sleeper(self._retry_delay_seconds)
                continue

            if response.status_code == 404:
                raise _NotFoundError(url)

            if response.status_code >= 500 and attempt < self._max_retries:
                log.warning(
                    "bafu_forecast.request_retrying",
                    url=url,
                    status_code=response.status_code,
                    attempt=attempt,
                )
                self._sleeper(self._retry_delay_seconds)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise AdapterError(
                    f"BAFU request to {url} failed with status "
                    f"{response.status_code}: {exc}"
                ) from exc
            return response

        raise AdapterError(
            f"BAFU request to {url} failed after {self._max_retries + 1} "
            f"attempt(s): {last_exc}"
        ) from last_exc
