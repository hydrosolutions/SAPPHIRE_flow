from __future__ import annotations

import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import structlog
from pydantic import BaseModel

from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource, StationKind
from sapphire_flow.types.observation import RawObservation

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger()

_GRAPH_URI = "https://lindas.admin.ch/foen/hydro"
_BASE_URL = "https://environment.ld.admin.ch/foen/hydro"
_DIMENSION_URL = f"{_BASE_URL}/dimension"
_SITE_CODE_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")


class SparqlBinding(BaseModel):
    predicate: str
    object: str


class HydroScraperAdapter:
    _RIVER_PARAMS: ClassVar[list[str]] = [
        "discharge",
        "measurementTime",
        "waterLevel",
        "waterTemperature",
    ]
    _LAKE_PARAMS: ClassVar[list[str]] = [
        "measurementTime",
        "waterLevel",
    ]
    _PARAM_MAP: ClassVar[dict[str, str]] = {
        "discharge": "discharge",
        "waterLevel": "water_level",
        "waterTemperature": "water_temperature",
    }

    def __init__(self, endpoint: str, http_client: httpx.Client) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError(f"SPARQL endpoint must use HTTPS, got: {endpoint!r}")
        self._endpoint = endpoint
        self._http_client = http_client

    def fetch_observations(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> list[RawObservation]:
        results: list[RawObservation] = []
        for station_config in station_configs:
            station_id = station_config.id
            if station_config.station_kind == StationKind.WEATHER:
                log.warning(
                    "observation.skip_weather_station",
                    station_id=str(station_id),
                    reason="LINDAS does not serve weather stations",
                )
                continue
            try:
                log.debug(
                    "observation.fetch_started",
                    station_id=str(station_id),
                    station_kind=station_config.station_kind.value,
                )
                t0 = time.perf_counter()
                query = self._build_sparql_query(
                    station_config.code, station_config.station_kind
                )
                response = self._http_client.post(
                    self._endpoint,
                    data={"query": query},
                    headers={"Accept": "application/sparql-results+json"},
                )
                log.debug(
                    "observation.http_response",
                    station_id=str(station_id),
                    url=self._endpoint,
                    status_code=response.status_code,
                    response_bytes=len(response.content),
                )
                response.raise_for_status()
                bindings = response.json()["results"]["bindings"]
                observations = self._parse_bindings(bindings, station_id)
                duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                log.info(
                    "observation.fetch_completed",
                    station_id=str(station_id),
                    duration_ms=duration_ms,
                    record_count=len(observations),
                )
                results.extend(observations)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.warning(
                    "observation.fetch_failed",
                    station_id=str(station_id),
                    error=str(exc),
                )
        return results

    def verify_gauge_reachable(self, site_code: str, station_kind: StationKind) -> bool:
        log.info(
            "observation.verify_gauge_started",
            site_code=site_code,
            station_kind=station_kind.value,
        )
        query = self._build_sparql_query(site_code, station_kind)
        try:
            response = self._http_client.post(
                self._endpoint,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            )
        except httpx.RequestError as exc:
            log.error(
                "observation.verify_gauge_failed",
                site_code=site_code,
                station_kind=station_kind.value,
                error=str(exc),
            )
            raise AdapterError(
                f"LINDAS probe network failure for {site_code!r}: {exc}"
            ) from exc

        status_code = response.status_code
        if not (200 <= status_code < 300):
            log.info(
                "observation.verify_gauge_completed",
                site_code=site_code,
                station_kind=station_kind.value,
                status_code=status_code,
                reachable=False,
            )
            return False

        try:
            bindings = response.json()["results"]["bindings"]
        except (ValueError, KeyError) as exc:
            log.info(
                "observation.verify_gauge_completed",
                site_code=site_code,
                station_kind=station_kind.value,
                status_code=status_code,
                reachable=False,
                parse_error=str(exc),
            )
            return False

        reachable = len(bindings) >= 1
        log.info(
            "observation.verify_gauge_completed",
            site_code=site_code,
            station_kind=station_kind.value,
            status_code=status_code,
            reachable=reachable,
            binding_count=len(bindings),
        )
        return reachable

    def _build_sparql_query(self, site_code: str, station_kind: StationKind) -> str:
        if not _SITE_CODE_RE.match(site_code):
            raise ValueError(f"Invalid site_code for SPARQL query: {site_code!r}")
        kind_path = station_kind.value  # "river" or "lake"
        subject_uri = f"{_BASE_URL}/{kind_path}/observation/{site_code}"
        params = (
            self._RIVER_PARAMS
            if station_kind == StationKind.RIVER
            else self._LAKE_PARAMS
        )
        predicates = ", ".join(f"<{_DIMENSION_URL}/{name}>" for name in params)
        return (
            f"SELECT ?predicate ?object\n"
            f"FROM <{_GRAPH_URI}>\n"
            f"WHERE {{\n"
            f"  BIND(<{subject_uri}> AS ?subject)\n"
            f"  ?subject ?predicate ?object .\n"
            f"  FILTER (?predicate IN ({predicates}))\n"
            f"}}"
        )

    def _parse_bindings(
        self, bindings: list[dict[str, Any]], station_id: StationId
    ) -> list[RawObservation]:
        prefix = f"{_DIMENSION_URL}/"
        timestamp_str: str | None = None
        param_values: dict[str, float] = {}

        for raw in bindings:
            parsed = SparqlBinding(
                predicate=raw["predicate"]["value"],
                object=raw["object"]["value"],
            )
            local_name = parsed.predicate.removeprefix(prefix)
            if local_name == "measurementTime":
                timestamp_str = parsed.object
            elif local_name in self._PARAM_MAP:
                param_values[self._PARAM_MAP[local_name]] = float(parsed.object)

        if timestamp_str is None or not param_values:
            return []

        try:
            ts = ensure_utc(datetime.fromisoformat(timestamp_str))
        except (ValueError, TypeError):
            log.warning(
                "observation.parse_failed",
                station_id=str(station_id),
                raw_timestamp=timestamp_str,
            )
            return []

        return [
            RawObservation(
                station_id=station_id,
                timestamp=ts,
                parameter=param,
                value=value,
                source=ObservationSource.MEASURED,
            )
            for param, value in param_values.items()
        ]
