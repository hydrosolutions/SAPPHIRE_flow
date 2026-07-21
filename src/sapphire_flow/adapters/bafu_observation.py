"""Collector-local adapter for the BAFU LINDAS observation archive collector
(Plan 136).

Fetches the WHOLE ``foen/hydro`` graph in a single SPARQL query
(``SELECT ?subject ?predicate ?object``) — this is deliberately NOT the
per-station path in ``adapters/hydro_scraper.py``: ``HydroScraperAdapter.
_build_sparql_query`` ``BIND``s exactly one ``?subject`` and its parser,
``_parse_bindings``, assumes a single subject per call (DC-1 in the plan).
Fed the flat triple-list a true whole-graph query returns, that parser would
silently merge every gauge's measurements into one row per poll.

This module's ``_parse_bindings`` instead groups triples BY ``?subject``,
discriminates river vs lake by the ``/river/observation/`` vs
``/lake/observation/`` URI segment (DC-3 — LINDAS is ground truth,
independent of any onboarding classification), and yields one
``BafuObservationRow`` per (subject, parameter).

EVALUATION-ONLY. Never constructs ``RawObservation``/``StationId`` (DC-2) —
see ``flows/collect_bafu_observations.py`` for the quarantined archive write
path.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from sapphire_flow import __version__
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.bafu_observation import (
    BafuObservationParameter,
    BafuObservationRow,
)
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.bafu_observation import LindasKind

log = structlog.get_logger(__name__)

_GRAPH_URI = "https://lindas.admin.ch/foen/hydro"
_BASE_URL = "https://environment.ld.admin.ch/foen/hydro"
_DIMENSION_URL = f"{_BASE_URL}/dimension"
_RIVER_SEGMENT = "/river/observation/"
_LAKE_SEGMENT = "/lake/observation/"

# Mirrors adapters/hydro_scraper.py's _PARAM_MAP target values (DC-2).
_PARAM_MAP: dict[str, BafuObservationParameter] = {
    "discharge": "discharge",
    "waterLevel": "water_level",
    "waterTemperature": "water_temperature",
}
# measurementTime is a recognized non-parameter predicate (supplies the row
# timestamp); the three above are the only recognized parameter predicates.
_DIMENSION_PREDICATES = (
    "discharge",
    "waterLevel",
    "waterTemperature",
    "measurementTime",
)

# Generous safety cap, well above the ~730 rows the live probe (2026-07-21)
# returned for 233 gauges — a bounded-request courtesy guard, not a
# per-page limit. Hitting it means the whole-graph fetch is silently missing
# part of the network (T2 truncation guard).
_QUERY_LIMIT = 10000

# Identifying User-Agent (same posture as adapters/bafu_forecast.py) so BAFU
# can see who is polling the public endpoint and object if needed.
USER_AGENT = (
    f"SAPPHIRE-Flow/{__version__} (hydrosolutions; marti@hydrosolutions.ch) "
    "observation-archive-collector"
)


class _SparqlTriple(BaseModel):
    subject: str
    predicate: str
    object: str


class BafuObservationAdapter:
    def __init__(
        self,
        endpoint: str,
        http_client: httpx.Client,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        retry_delay_seconds: float = 1.0,
        max_retries: int = 2,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError(f"SPARQL endpoint must use HTTPS, got: {endpoint!r}")
        self._endpoint = endpoint
        self._http_client = http_client
        self._sleeper = sleeper
        self._retry_delay_seconds = retry_delay_seconds
        self._max_retries = max_retries

    def fetch_all_observations(self) -> list[BafuObservationRow]:
        rows, _raw_payload = self.fetch_all_observations_with_raw()
        return rows

    def fetch_all_observations_with_raw(
        self,
    ) -> tuple[list[BafuObservationRow], dict[str, Any]]:
        """Fetch + parse the whole graph, ALSO returning the raw decoded
        SPARQL-results payload — used by the collector flow (T3/T4) to
        archive the raw JSON companion alongside the parsed parquet without a
        second network round-trip."""
        query = self._build_query()
        response = self._post_with_retries(query)
        try:
            payload = response.json()
            bindings = payload["results"]["bindings"]
        except (ValueError, KeyError, TypeError) as exc:
            # TypeError covers a wrong-SHAPED (but valid-JSON) payload — e.g.
            # a top-level list, or `results` itself a list — where indexing
            # with a string key raises TypeError rather than KeyError. Must
            # be caught here so it surfaces as AdapterError, never a bare
            # TypeError that would skip the collector flow's CRITICAL
            # heartbeat (T8).
            raise AdapterError(
                f"BAFU LINDAS whole-graph response is not a well-formed SPARQL "
                f"results JSON: {exc}"
            ) from exc

        if len(bindings) >= _QUERY_LIMIT:
            raise AdapterError(
                f"BAFU LINDAS whole-graph fetch hit the safety LIMIT "
                f"({_QUERY_LIMIT}) — the archive is likely missing part of "
                "the network this cycle (schema-drift/coverage signal)"
            )

        try:
            return self._parse_bindings(bindings), payload
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            # A malformed binding (missing subject/predicate/object.value, a
            # non-numeric literal, an un-parseable measurementTime, etc.)
            # must never propagate as a raw exception — it would skip the
            # collector flow's CRITICAL heartbeat (T8), the exact
            # silent-failure mode the flow's except-AdapterError handler is
            # meant to catch. _parse_subject/_parse_bindings already raise
            # AdapterError for RECOGNIZED schema-drift cases (unmapped
            # predicate, missing measurementTime); this is the backstop for
            # everything else.
            raise AdapterError(
                f"BAFU LINDAS whole-graph response has a malformed binding: {exc}"
            ) from exc

    @staticmethod
    def _build_query() -> str:
        predicates = ", ".join(
            f"<{_DIMENSION_URL}/{name}>" for name in _DIMENSION_PREDICATES
        )
        return (
            f"SELECT ?subject ?predicate ?object\n"
            f"FROM <{_GRAPH_URI}>\n"
            f"WHERE {{\n"
            f"  ?subject ?predicate ?object .\n"
            f"  FILTER (?predicate IN ({predicates}))\n"
            f"}}\n"
            f"LIMIT {_QUERY_LIMIT}"
        )

    @classmethod
    def _parse_bindings(
        cls, bindings: list[dict[str, Any]]
    ) -> list[BafuObservationRow]:
        prefix = f"{_DIMENSION_URL}/"
        by_subject: dict[str, list[_SparqlTriple]] = {}
        for raw in bindings:
            triple = _SparqlTriple(
                subject=raw["subject"]["value"],
                predicate=raw["predicate"]["value"],
                object=raw["object"]["value"],
            )
            by_subject.setdefault(triple.subject, []).append(triple)

        rows: list[BafuObservationRow] = []
        for subject, triples in by_subject.items():
            gauge_code, lindas_kind = cls._parse_subject(subject)
            timestamp: str | None = None
            values: dict[BafuObservationParameter, float] = {}

            for triple in triples:
                local_name = triple.predicate.removeprefix(prefix)
                if local_name == "measurementTime":
                    timestamp = triple.object
                elif local_name in _PARAM_MAP:
                    values[_PARAM_MAP[local_name]] = float(triple.object)
                else:
                    # Schema-drift signal (DC-2): never silently drop or
                    # stringify an unrecognized predicate through.
                    raise AdapterError(
                        f"Unrecognized LINDAS predicate {triple.predicate!r} "
                        f"for subject {subject!r} — schema drift"
                    )

            if timestamp is None:
                raise AdapterError(
                    f"Subject {subject!r} has no measurementTime binding"
                )
            try:
                measurement_time = ensure_utc(datetime.fromisoformat(timestamp))
            except (ValueError, TypeError) as exc:
                raise AdapterError(
                    f"Subject {subject!r} has an unparseable measurementTime: "
                    f"{timestamp!r}"
                ) from exc

            rows.extend(
                BafuObservationRow(
                    gauge_code=gauge_code,
                    lindas_kind=lindas_kind,
                    parameter=param,
                    value=value,
                    measurement_time=measurement_time,
                )
                for param, value in values.items()
            )
        return rows

    def _post_with_retries(self, query: str) -> httpx.Response:
        """POST the whole-graph query with a bounded retry cap (T3: a polite
        client with a request cap/retry), mirroring
        ``BafuForecastAdapter._get_with_retries``. Any network/HTTP failure
        (including a non-2xx status) is normalized to ``AdapterError`` so the
        collector flow's CRITICAL heartbeat is never silently skipped."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http_client.post(
                    self._endpoint,
                    data={"query": query},
                    headers={
                        "Accept": "application/sparql-results+json",
                        "User-Agent": USER_AGENT,
                    },
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning(
                    "bafu_observation.request_failed",
                    endpoint=self._endpoint,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < self._max_retries:
                    self._sleeper(self._retry_delay_seconds)
                continue

            if response.status_code >= 500 and attempt < self._max_retries:
                log.warning(
                    "bafu_observation.request_retrying",
                    endpoint=self._endpoint,
                    status_code=response.status_code,
                    attempt=attempt,
                )
                self._sleeper(self._retry_delay_seconds)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise AdapterError(
                    f"BAFU LINDAS whole-graph request to {self._endpoint} "
                    f"failed with status {response.status_code}: {exc}"
                ) from exc
            return response

        raise AdapterError(
            f"BAFU LINDAS whole-graph request to {self._endpoint} failed "
            f"after {self._max_retries + 1} attempt(s): {last_exc}"
        ) from last_exc

    @staticmethod
    def _parse_subject(subject: str) -> tuple[str, LindasKind]:
        if _RIVER_SEGMENT in subject:
            return subject.rsplit(_RIVER_SEGMENT, 1)[1], "river"
        if _LAKE_SEGMENT in subject:
            return subject.rsplit(_LAKE_SEGMENT, 1)[1], "lake"
        raise AdapterError(
            f"Subject URI {subject!r} has neither {_RIVER_SEGMENT!r} nor "
            f"{_LAKE_SEGMENT!r} — cannot discriminate river/lake kind"
        )
