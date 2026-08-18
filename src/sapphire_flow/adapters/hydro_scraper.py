from __future__ import annotations

import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from sapphire_flow.adapters.lindas_rate_limiter import (
    LindasLimiterConfig,
    TokenBucketLindasLimiter,
)
from sapphire_flow.exceptions import AdapterError, LindasRateLimitExhaustedError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause, ObservationSource, StationKind
from sapphire_flow.types.observation import (
    HydroScraperBatchResult,
    RawObservation,
    StationFetchOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.adapters.lindas_rate_limiter import LindasRateLimiter
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

    def __init__(
        self,
        endpoint: str,
        http_client: httpx.Client,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
        limiter: LindasRateLimiter | None = None,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError(f"SPARQL endpoint must use HTTPS, got: {endpoint!r}")
        self._endpoint = endpoint
        self._http_client = http_client
        # Plan 175 T1/T3: one shared limiter owns pacing + 429/5xx/transport
        # retry (D2/D3/D6) — every per-station POST goes through it, so a
        # single process's offered load to LINDAS is bounded by construction.
        self._limiter: LindasRateLimiter = limiter or TokenBucketLindasLimiter(
            config=LindasLimiterConfig(max_attempts=max_retries + 1),
            sleeper=sleeper,
        )

    def fetch_observations(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> list[RawObservation]:
        """`StationDataSource` Protocol façade (locked shape — Plan 175 T3):
        returns the flat observation list only. Delegates to
        ``fetch_observations_batch`` through the same limiter, so no caller
        of this method escapes pacing; per-station failure causes are only
        observable via the typed batch method."""
        return self.fetch_observations_batch(station_configs, since).observations

    def fetch_observations_batch(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> HydroScraperBatchResult:
        """Plan 175 T3/D9 — per-station typed outcomes (observations +
        failure cause), so the ingest flow can report a truthful
        `stations_failed` instead of silently returning whatever partial
        data survived. Not part of the `StationDataSource` Protocol."""
        eligible: list[StationConfig] = []
        for station_config in station_configs:
            if station_config.station_kind == StationKind.WEATHER:
                log.warning(
                    "observation.skip_weather_station",
                    station_id=str(station_config.id),
                    reason="LINDAS does not serve weather stations",
                )
                continue
            eligible.append(station_config)
        outcomes = tuple(self._fetch_one(sc) for sc in eligible)
        return HydroScraperBatchResult(outcomes=outcomes)

    def _fetch_one(self, station_config: StationConfig) -> StationFetchOutcome:
        station_id = station_config.id
        log.debug(
            "observation.fetch_started",
            station_id=str(station_id),
            station_kind=station_config.station_kind.value,
        )
        t0 = time.perf_counter()
        try:
            query = self._build_sparql_query(
                station_config.code, station_config.station_kind
            )
        except ValueError as exc:
            # Rejected before any request is sent (e.g. a site_code that
            # fails the SPARQL-injection guard) — not one of D9's network-
            # facing causes, but it must not raise past this adapter either;
            # MALFORMED_RESPONSE is the closest bucket (the request could
            # not even be constructed).
            log.warning(
                "observation.fetch_failed",
                station_id=str(station_id),
                error=str(exc),
                failure_cause=FetchOutcomeCause.MALFORMED_RESPONSE.value,
            )
            return StationFetchOutcome(
                station_id=station_id,
                observations=(),
                failure_cause=FetchOutcomeCause.MALFORMED_RESPONSE,
                failure_detail=str(exc),
            )

        def send(remaining_s: float) -> httpx.Response:
            # Not threaded into `timeout=`: HTTPX 0.28 applies a single
            # float independently to each of connect/read/write/pool, so
            # doing so would let ONE phase wait the whole remaining budget
            # while REPLACING this client's own (stricter) configured
            # timeouts. Those configured timeouts bound each attempt; the
            # limiter's deadline bounds when a new attempt may start.
            del remaining_s
            return self._http_client.post(
                self._endpoint,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            )

        try:
            response = self._limiter.call(send)
        except LindasRateLimitExhaustedError as exc:
            cause = (
                FetchOutcomeCause.RATE_LIMITED
                if exc.last_status == 429
                else (
                    FetchOutcomeCause.HTTP_STATUS_ERROR
                    if exc.last_status is not None
                    else FetchOutcomeCause.TRANSPORT_ERROR
                )
            )
            log.warning(
                "observation.fetch_failed",
                station_id=str(station_id),
                error=str(exc),
                failure_cause=cause.value,
            )
            return StationFetchOutcome(
                station_id=station_id,
                observations=(),
                failure_cause=cause,
                failure_detail=str(exc),
            )

        log.debug(
            "observation.http_response",
            station_id=str(station_id),
            url=self._endpoint,
            status_code=response.status_code,
            response_bytes=len(response.content),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.warning(
                "observation.fetch_failed",
                station_id=str(station_id),
                error=str(exc),
                failure_cause=FetchOutcomeCause.HTTP_STATUS_ERROR.value,
            )
            return StationFetchOutcome(
                station_id=station_id,
                observations=(),
                failure_cause=FetchOutcomeCause.HTTP_STATUS_ERROR,
                failure_detail=str(exc),
            )

        try:
            bindings = response.json()["results"]["bindings"]
        except (ValueError, KeyError, TypeError) as exc:
            # Major fix (round 2): TypeError covers a wrong-SHAPED (but
            # valid-JSON) envelope — a top-level list (`[]["results"]`), a
            # `results` that is itself a list (`[]["bindings"]`), or
            # `{"results": null}` (`None["bindings"]`) — where indexing with
            # a string key raises TypeError, not KeyError/ValueError. Must be
            # caught here so it becomes a typed MALFORMED_RESPONSE outcome,
            # never a bare TypeError that aborts the whole batch before this
            # station's health record is written.
            log.warning(
                "observation.fetch_failed",
                station_id=str(station_id),
                error=str(exc),
                failure_cause=FetchOutcomeCause.MALFORMED_RESPONSE.value,
            )
            return StationFetchOutcome(
                station_id=station_id,
                observations=(),
                failure_cause=FetchOutcomeCause.MALFORMED_RESPONSE,
                failure_detail=str(exc),
            )

        try:
            observations, cause, detail = self._parse_bindings_typed(
                bindings, station_id
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Major fix: `_parse_bindings_typed` already validates most shape
            # problems internally and returns a typed MALFORMED_RESPONSE, but
            # this is a last-resort backstop — an unanticipated shape must
            # become a typed failure, never an uncaught exception that aborts
            # the whole batch before its per-station health record is
            # written (the T3 silent-health-gap this plan closed).
            log.warning(
                "observation.fetch_failed",
                station_id=str(station_id),
                error=str(exc),
                failure_cause=FetchOutcomeCause.MALFORMED_RESPONSE.value,
            )
            return StationFetchOutcome(
                station_id=station_id,
                observations=(),
                failure_cause=FetchOutcomeCause.MALFORMED_RESPONSE,
                failure_detail=f"unparseable bindings: {exc}",
            )
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        if cause is not None:
            # Major fix: `_parse_bindings_typed` returning NO_DATA or
            # MALFORMED_RESPONSE is still a per-station failure — the batch's
            # own `.failed` property (and `ingest_observations_flow`'s
            # `stations_fetch_failed` count) already treat any non-None
            # `failure_cause` as failed. Logging `fetch_completed` here would
            # tell an operator this station succeeded while the typed outcome
            # simultaneously says it did not, and a list-façade caller (e.g.
            # `record_fixtures.py`, which only sees `[]`) would get zero
            # WARNING signal at all for a malformed/empty response.
            log.warning(
                "observation.fetch_failed",
                station_id=str(station_id),
                error=detail,
                failure_cause=cause.value,
            )
            return StationFetchOutcome(
                station_id=station_id,
                observations=tuple(observations),
                failure_cause=cause,
                failure_detail=detail,
            )
        log.info(
            "observation.fetch_completed",
            station_id=str(station_id),
            duration_ms=duration_ms,
            record_count=len(observations),
        )
        return StationFetchOutcome(
            station_id=station_id,
            observations=tuple(observations),
            failure_cause=cause,
            failure_detail=detail,
        )

    def verify_gauge_reachable(self, site_code: str, station_kind: StationKind) -> bool:
        log.info(
            "observation.verify_gauge_started",
            site_code=site_code,
            station_kind=station_kind.value,
        )
        query = self._build_sparql_query(site_code, station_kind)

        def send(remaining_s: float) -> httpx.Response:
            # Not threaded into `timeout=`: HTTPX 0.28 applies a single
            # float independently to each of connect/read/write/pool, so
            # doing so would let ONE phase wait the whole remaining budget
            # while REPLACING this client's own (stricter) configured
            # timeouts. Those configured timeouts bound each attempt; the
            # limiter's deadline bounds when a new attempt may start.
            del remaining_s
            return self._http_client.post(
                self._endpoint,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            )

        # Major fix: this probe used to POST directly, bypassing the shared
        # limiter entirely — a transient 429 was reported straight back as
        # "unreachable" instead of being paced/retried like every other
        # LINDAS caller (D6's "every in-process production LINDAS request
        # goes through one injectable limiter").
        try:
            response = self._limiter.call(send)
        except LindasRateLimitExhaustedError as exc:
            if exc.last_exc is not None:
                log.error(
                    "observation.verify_gauge_failed",
                    site_code=site_code,
                    station_kind=station_kind.value,
                    error=str(exc),
                )
                raise AdapterError(
                    f"LINDAS probe network failure for {site_code!r}: {exc.last_exc}"
                ) from exc
            log.info(
                "observation.verify_gauge_completed",
                site_code=site_code,
                station_kind=station_kind.value,
                status_code=exc.last_status,
                reachable=False,
            )
            return False

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
        except (ValueError, KeyError, TypeError) as exc:
            # Major fix (round 2): same wrong-shaped-envelope TypeError gap
            # as `_fetch_one` above — a top-level list/None `results` must
            # resolve to `reachable=False`, never escape as a raw TypeError.
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

    def _parse_bindings_typed(
        self, bindings: list[dict[str, Any]], station_id: StationId
    ) -> tuple[list[RawObservation], FetchOutcomeCause | None, str | None]:
        """Plan 175 D9 — splits what the pre-Plan-175 ``_parse_bindings``
        collapsed into a single empty list: NO_DATA (legitimately empty/
        incomplete bindings) vs MALFORMED_RESPONSE (an unparseable
        timestamp, or — major fix — any other shape LINDAS's response could
        take: not a list, a binding missing ``predicate``/``object``, a
        non-dict binding, or a non-numeric parameter value). ``bindings`` is
        untyped ``Any`` at the boundary (straight out of
        ``response.json()``); every failure mode here must become a typed
        MALFORMED_RESPONSE, never an uncaught KeyError/TypeError/ValueError
        that would abort the whole batch before its health record is
        written."""
        # The `list[dict[str, Any]]` annotation is aspirational — `bindings`
        # is really `Any` at runtime (straight out of `response.json()`,
        # itself untyped), so the isinstance check is NOT statically
        # redundant despite what the local annotation claims.
        if not isinstance(bindings, list):  # pyright: ignore[reportUnnecessaryIsInstance]
            return (
                [],
                FetchOutcomeCause.MALFORMED_RESPONSE,
                f"bindings must be a list, got {type(bindings).__name__}",
            )

        prefix = f"{_DIMENSION_URL}/"
        timestamp_str: str | None = None
        param_values: dict[str, float] = {}

        for raw in bindings:
            try:
                parsed = SparqlBinding(
                    predicate=raw["predicate"]["value"],
                    object=raw["object"]["value"],
                )
            except (KeyError, TypeError, ValidationError) as exc:
                return (
                    [],
                    FetchOutcomeCause.MALFORMED_RESPONSE,
                    f"malformed binding {raw!r}: {exc}",
                )
            local_name = parsed.predicate.removeprefix(prefix)
            if local_name == "measurementTime":
                timestamp_str = parsed.object
            elif local_name in self._PARAM_MAP:
                try:
                    param_values[self._PARAM_MAP[local_name]] = float(parsed.object)
                except (TypeError, ValueError) as exc:
                    return (
                        [],
                        FetchOutcomeCause.MALFORMED_RESPONSE,
                        f"non-numeric {local_name}: {parsed.object!r} ({exc})",
                    )

        if timestamp_str is None or not param_values:
            return [], FetchOutcomeCause.NO_DATA, "no usable bindings in response"

        try:
            ts = ensure_utc(datetime.fromisoformat(timestamp_str))
        except (ValueError, TypeError):
            log.warning(
                "observation.parse_failed",
                station_id=str(station_id),
                raw_timestamp=timestamp_str,
            )
            return (
                [],
                FetchOutcomeCause.MALFORMED_RESPONSE,
                f"unparseable measurementTime: {timestamp_str!r}",
            )

        observations = [
            RawObservation(
                station_id=station_id,
                timestamp=ts,
                parameter=param,
                value=value,
                source=ObservationSource.MEASURED,
            )
            for param, value in param_values.items()
        ]
        return observations, None, None
