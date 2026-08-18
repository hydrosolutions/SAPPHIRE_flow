from __future__ import annotations

import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, cast

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from sapphire_flow.adapters.lindas_hydro_query import QUERY_LIMIT as _QUERY_LIMIT
from sapphire_flow.adapters.lindas_hydro_query import (
    build_whole_graph_query,
    parse_subject_key,
)
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
        """Plan 186 D1/D4 — one whole-graph SPARQL request per call, then a
        local filter to the passed `station_configs` (grouped by `(code,
        station_kind)`), replacing the former one-request-per-station loop.
        `since` stays accepted-but-unread (D3 — LINDAS serves current values
        only; no per-station time-window semantics exist to preserve).

        Per-station typed outcomes (observations + failure cause) so the
        ingest flow can report a truthful `stations_failed`. Not part of the
        `StationDataSource` Protocol.

        Outcome generation always iterates `eligible` directly (never the
        grouping index's `.values()`/`.items()`) so that two eligible
        configs sharing one `(code, station_kind)` key — e.g. the same
        physical gauge onboarded under two `StationConfig` rows — each still
        get their own outcome instead of one silently shadowing the other
        (D4's "exactly one outcome per eligible config, always")."""
        del since
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

        # D4 step 4: no eligible config remains -> no request at all.
        if not eligible:
            return HydroScraperBatchResult(outcomes=())

        # D4 steps 2/3: every remaining valid config is eligible for the
        # whole-response fetch, regardless of whether its code will
        # ultimately match a subject in the response (a miss becomes
        # NO_DATA at extraction time, not a pre-request rejection). A LIST
        # per key (not a single `StationConfig`) so a `(code, station_kind)`
        # collision groups configs for lookup without dropping either one —
        # the outcome loop below iterates `eligible`, not this index.
        index: dict[tuple[str, str], list[StationConfig]] = {}
        for sc in eligible:
            index.setdefault((sc.code, sc.station_kind.value), []).append(sc)

        log.debug("observation.whole_graph_fetch_started", station_count=len(eligible))
        t0 = time.perf_counter()
        query = build_whole_graph_query()

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
            # D4: a transport/HTTP fault is now WHOLE-BATCH — every eligible
            # config gets the same cause, since there is no per-subject
            # information to isolate the failure to.
            cause = (
                FetchOutcomeCause.RATE_LIMITED
                if exc.last_status == 429
                else (
                    FetchOutcomeCause.HTTP_STATUS_ERROR
                    if exc.last_status is not None
                    else FetchOutcomeCause.TRANSPORT_ERROR
                )
            )
            return self._whole_batch_failure(eligible, cause, str(exc))

        log.debug(
            "observation.whole_graph_http_response",
            url=self._endpoint,
            status_code=response.status_code,
            response_bytes=len(response.content),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return self._whole_batch_failure(
                eligible, FetchOutcomeCause.HTTP_STATUS_ERROR, str(exc)
            )

        try:
            bindings = response.json()["results"]["bindings"]
        except (ValueError, KeyError, TypeError) as exc:
            # Same wrong-shaped-envelope TypeError gap as the collector: no
            # subjects exist yet at this point, so this is necessarily a
            # whole-batch fault.
            return self._whole_batch_failure(
                eligible, FetchOutcomeCause.MALFORMED_RESPONSE, str(exc)
            )
        if not isinstance(bindings, list):
            return self._whole_batch_failure(
                eligible,
                FetchOutcomeCause.MALFORMED_RESPONSE,
                f"bindings must be a list, got {type(bindings).__name__}",
            )
        bindings = cast("list[Any]", bindings)

        if len(bindings) >= _QUERY_LIMIT:
            # Mirrors the collector's coverage guard (adapters/
            # bafu_observation.py): a cap-sized response means the graph is
            # likely truncated this cycle, so every eligible config gets a
            # whole-batch failure instead of quietly falling through to
            # per-subject NO_DATA (which would hide a coverage regression
            # behind ordinary "nothing new published" noise).
            return self._whole_batch_failure(
                eligible,
                FetchOutcomeCause.MALFORMED_RESPONSE,
                f"whole-graph fetch hit the safety LIMIT ({_QUERY_LIMIT}) — "
                "response is likely truncated (coverage failure)",
            )

        grouped = self._group_by_requested_subject(bindings, index)
        outcomes = tuple(
            self._extract_one(
                station_config,
                grouped.get(
                    (station_config.code, station_config.station_kind.value), []
                ),
            )
            for station_config in eligible
        )
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        log.info(
            "observation.whole_graph_fetch_completed",
            station_count=len(eligible),
            duration_ms=duration_ms,
        )
        return HydroScraperBatchResult(outcomes=outcomes)

    @staticmethod
    def _whole_batch_failure(
        eligible: list[StationConfig],
        cause: FetchOutcomeCause,
        detail: str,
    ) -> HydroScraperBatchResult:
        outcomes: list[StationFetchOutcome] = []
        for station_config in eligible:
            log.warning(
                "observation.fetch_failed",
                station_id=str(station_config.id),
                error=detail,
                failure_cause=cause.value,
            )
            outcomes.append(
                StationFetchOutcome(
                    station_id=station_config.id,
                    observations=(),
                    failure_cause=cause,
                    failure_detail=detail,
                )
            )
        return HydroScraperBatchResult(outcomes=tuple(outcomes))

    @staticmethod
    def _group_by_requested_subject(
        bindings: list[Any],
        index: dict[tuple[str, str], list[StationConfig]],
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """D2 — groups by *usable subject only*, using the shared
        non-raising key helper: a raw binding whose subject is missing,
        malformed, unrecognised, or simply not one of `index`'s requested
        stations is never added to any group and therefore never validated
        by `_parse_bindings_typed` — a malformed binding for a gauge we do
        not poll is never looked at."""
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for raw in bindings:
            if not isinstance(raw, dict):
                continue
            raw = cast("dict[str, Any]", raw)
            subject_field = raw.get("subject")
            if not isinstance(subject_field, dict):
                continue
            subject_field = cast("dict[str, Any]", subject_field)
            subject_value = subject_field.get("value")
            if not isinstance(subject_value, str):
                continue
            key = parse_subject_key(subject_value)
            if key is None or key not in index:
                continue
            grouped.setdefault(key, []).append(raw)
        return grouped

    @classmethod
    def _filter_bindings_for_kind(
        cls, raw_group: list[dict[str, Any]], station_kind: StationKind
    ) -> list[dict[str, Any]]:
        """Fixer round (post-186 review) — grouping by subject URI alone
        does not stop a matched subject's bindings from carrying a
        cross-kind-only dimension predicate (`discharge`/`waterTemperature`
        are RIVER-only; a LAKE station must never emit them, even under
        schema drift). Malformed bindings (no parseable predicate name)
        pass through UNFILTERED so `_parse_bindings_typed` still reports
        them as MALFORMED_RESPONSE — this only drops well-formed bindings
        whose predicate belongs exclusively to the OTHER kind."""
        allowed = frozenset(
            cls._RIVER_PARAMS if station_kind == StationKind.RIVER else cls._LAKE_PARAMS
        )
        cross_kind_only = frozenset(cls._RIVER_PARAMS) - allowed
        if not cross_kind_only:
            return raw_group
        return [
            raw
            for raw in raw_group
            if cls._predicate_local_name(raw) not in cross_kind_only
        ]

    @staticmethod
    def _predicate_local_name(raw: dict[str, Any]) -> str | None:
        # `raw` is aspirationally `dict[str, Any]` but really `Any` at
        # runtime (an element of `raw_group`, itself sourced from
        # `response.json()`) — same non-redundant-in-practice isinstance
        # pattern as `_parse_bindings_typed` above.
        if not isinstance(raw, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            return None
        predicate_field = raw.get("predicate")
        if not isinstance(predicate_field, dict):
            return None
        predicate_field = cast("dict[str, Any]", predicate_field)
        value = predicate_field.get("value")
        if not isinstance(value, str):
            return None
        return value.removeprefix(f"{_DIMENSION_URL}/")

    def _extract_one(
        self,
        station_config: StationConfig,
        raw_group: list[dict[str, Any]],
    ) -> StationFetchOutcome:
        """D4: NO_DATA and MALFORMED_RESPONSE stay subject-local — `raw_group`
        is already scoped to this one station's bindings (or empty, for a
        subject absent from the response / an unmatched code, D4 step 2)."""
        station_id = station_config.id
        filtered_group = self._filter_bindings_for_kind(
            raw_group, station_config.station_kind
        )
        try:
            observations, cause, detail = self._parse_bindings_typed(
                filtered_group, station_id
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Last-resort backstop — an unanticipated shape must become a
            # typed failure scoped to THIS station, never an uncaught
            # exception that aborts the whole batch.
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
        if cause is not None:
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
