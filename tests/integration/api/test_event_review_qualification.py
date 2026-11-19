from __future__ import annotations

import json
import platform
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from sapphire_flow.api.errors import http_exception_handler, unhandled_exception_handler
from sapphire_flow.api.event_review_schemas import ReviewSeriesResponse
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.routes import api_event_review, api_review, api_stations
from sapphire_flow.api.security import load_access_token_pepper
from sapphire_flow.services.event_review import read_review_series
from sapphire_flow.store.event_review import PgEventReviewStore, _catalog_statement
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole, ForcingType
from sapphire_flow.types.event_review import (
    MAX_REVIEW_BYTES,
    ReviewOptionsQuery,
    ReviewPolicy,
    ReviewSeriesQuery,
    ReviewSourceMode,
)
from sapphire_flow.types.ids import ModelId, StationId
from tests.conftest import make_station_config
from tests.integration.api.test_access_token_auth import _auth, _make_token
from tests.integration.store.test_hindcast_store import _seed_artifact, _seed_model

START = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
END = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))


@pytest.mark.slow
def test_event_review_year_qualification(
    isolated_committed_db_engine: sa.Engine,
) -> None:
    """Repeatable local qualification; not a deployed capacity or edge-quota claim."""
    engine = isolated_committed_db_engine
    app = FastAPI()
    app.state.engine = engine
    app.state.access_token_pepper = load_access_token_pepper()
    for router in (api_event_review.router, api_stations.router, api_review.router):
        app.include_router(router)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(PublicationCacheMiddleware)
    queries: dict[str, ReviewSeriesQuery] = {}
    with engine.begin() as conn:
        model = _seed_model(conn)
        for name, cadence, count in (("daily", 86400, 366), ("hourly", 3600, 8784)):
            station = make_station_config(
                station_id=StationId(uuid4()), code=f"dense-{uuid4()}"
            )
            PgStationStore(conn).store_station(station)
            artifact = _seed_artifact(conn, model, station.id)
            run = uuid4()
            conn.execute(
                sa.text("""
                INSERT INTO hindcast_forecasts
                    (id, station_id, model_id, model_artifact_id, hindcast_step,
                     forcing_type, representation, hindcast_run_id, parameter,
                     units, created_at, qc_status, time_step_seconds)
                SELECT gen_random_uuid(), :sid, :mid, :aid,
                    :start + n * :cadence * interval '1 second' - interval '48 hours',
                    'reanalysis', 'members', :run, 'discharge', 'm³/s', :end,
                    'raw', :cadence
                FROM generate_series(0, :count - 1) n
            """),
                {
                    "sid": station.id,
                    "mid": model,
                    "aid": artifact,
                    "run": run,
                    "start": START,
                    "end": END,
                    "cadence": cadence,
                    "count": count,
                },
            )
            conn.execute(
                sa.text("""
                INSERT INTO hindcast_values
                    (id, hindcast_forecast_id, hindcast_step, valid_time,
                     lead_time_hours, member_id, value)
                SELECT gen_random_uuid(), h.id, h.hindcast_step,
                    h.hindcast_step + lead * interval '1 hour', lead, member,
                    10.123456789012345 + member * 0.123456789012345 + lead * 0.001
                FROM hindcast_forecasts h
                CROSS JOIN unnest(ARRAY[24,48,72]) lead
                CROSS JOIN generate_series(0,20) member
                WHERE h.hindcast_run_id = :run
            """),
                {"run": run},
            )
            conn.execute(
                sa.text("""
                INSERT INTO observations
                    (id, station_id, timestamp, parameter, source, value, qc_status)
                SELECT gen_random_uuid(), :sid,
                    :start + n * :cadence * interval '1 second',
                    'discharge', 'measured', 12.123456789, 'qc_passed'
                FROM generate_series(0, :count - 1) n
            """),
                {"sid": station.id, "start": START, "cadence": cadence, "count": count},
            )
            queries[name] = ReviewSeriesQuery(
                scope=ReviewOptionsQuery(
                    station_id=station.id,
                    tenant_id=station.tenant_id,
                    policy=ReviewPolicy.ORDINARY,
                ),
                model_id=ModelId(model),
                lead_hours=48,
                start=START,
                end=END,
                requested_at=END + timedelta(days=1),
                source_mode=ReviewSourceMode.HINDCAST,
                hindcast_run_id=run,
                hindcast_artifact_id=artifact,
                forcing_type=ForcingType.REANALYSIS,
                time_step_seconds=cadence,
            )
        token = _make_token(
            conn,
            role=AccessTokenRole.REVIEWER,
            station_ids=frozenset(q.scope.station_id for q in queries.values()),
        )
        for table in ("hindcast_forecasts", "hindcast_values", "observations"):
            conn.execute(sa.text(f"ANALYZE {table}"))

    report: dict[str, Any] = {
        "host": platform.platform(),
        "machine": platform.machine(),
        "fixture": "366 days; daily/hourly headers; 21 members; 3 stored leads",
        "cases": {},
    }
    captured: list[str] = []

    def capture(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool
    ) -> None:
        captured.append(statement)

    def request(client: TestClient, query: ReviewSeriesQuery) -> dict[str, Any]:
        params = {
            "model_id": query.model_id,
            "lead_hours": query.lead_hours,
            "start": query.start.isoformat(),
            "end": query.end.isoformat(),
            "source_mode": "hindcast",
            "hindcast_run_id": str(query.hindcast_run_id),
            "hindcast_artifact_id": str(query.hindcast_artifact_id),
            "forcing_type": "reanalysis",
            "time_step_seconds": query.time_step_seconds,
        }
        started = perf_counter()
        response = client.get(
            f"/api/v1/stations/{query.scope.station_id}/review-series",
            params=params,
            headers=_auth(token),
        )
        result = {
            "seconds": perf_counter() - started,
            "status": response.status_code,
            "bytes": len(response.content),
        }
        assert response.headers["cache-control"] == "no-store"
        assert response.status_code in (200, 422), response.text[:300]
        if response.status_code == 422:
            assert response.json()["detail"]["code"] == "review_series_limit_exceeded"
        return result

    with TestClient(app) as client:
        for name, query in queries.items():
            with engine.begin() as conn:
                started = perf_counter()
                series = read_review_series(PgEventReviewStore(conn), query)
                elapsed = perf_counter() - started
                raw = (
                    ReviewSeriesResponse.from_domain(series).model_dump_json().encode()
                )
                statement = _catalog_statement(query.scope, query).compile(
                    dialect=engine.dialect
                )
                plan = conn.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(statement),
                    statement.params,
                ).scalar_one()[0]
            sa.event.listen(engine, "before_cursor_execute", capture)
            captured.clear()
            try:
                route = request(client, query)
            finally:
                sa.event.remove(engine, "before_cursor_execute", capture)
            assert sum("review_headers AS MATERIALIZED" in sql for sql in captured) == 1
            assert not any(
                word in sql.upper()
                for sql in captured
                for word in ("INSERT INTO", "DELETE FROM")
            )
            report["cases"][name] = {
                "service_seconds": elapsed,
                "serialized_bytes": len(raw),
                "points": sum(len(segment.points) for segment in series.segments),
                "route": route,
                "sql_statements": len(captured),
                "plan_ms": plan["Execution Time"],
                "plan": plan,
            }
            assert route["status"] == (200 if len(raw) <= MAX_REVIEW_BYTES else 422)
            assert route["seconds"] < 10
        # Half-year hourly read fits the same single-response ceiling; do not fan out.
        from dataclasses import replace

        half = replace(queries["hourly"], end=START + timedelta(days=180))
        report["cases"]["hourly_180_days"] = request(client, half)
        assert report["cases"]["hourly_180_days"]["status"] == 200
        with ThreadPoolExecutor(max_workers=3) as pool:
            concurrent = list(
                pool.map(lambda q: request(client, q), [queries["daily"], half, half])
            )
        report["concurrent_shared_token"] = concurrent
        assert all(
            item["status"] == 200 and item["seconds"] < 10 for item in concurrent
        )
        for name, query in queries.items():
            obs = client.get(
                f"/api/v1/stations/{query.scope.station_id}/observations",
                params={
                    "parameter": "discharge",
                    "start": START.isoformat(),
                    "end": END.isoformat(),
                },
                headers=_auth(token),
            )
            skill = client.get(
                f"/api/v1/stations/{query.scope.station_id}/skill", headers=_auth(token)
            )
            assert obs.status_code == skill.status_code == 200
            assert len(obs.json()) == (366 if name == "daily" else 8784)
            report["cases"][name]["observations_bytes"] = len(obs.content)
            report["cases"][name]["stored_skill"] = skill.json()
    print("EVENT_REVIEW_QUALIFICATION=" + json.dumps(report, default=str))  # noqa: T201
