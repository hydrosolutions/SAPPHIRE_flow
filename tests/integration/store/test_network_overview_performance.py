from __future__ import annotations

import random
from collections.abc import Iterator  # noqa: TC003 -- fixture helper annotation
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

import polars as pl
import pytest
import sqlalchemy as sa
import structlog
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection, get_observation_clock
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.db.metadata import model_assignments
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import AccessTokenRole, EnsembleRepresentation, QcStatus
from sapphire_flow.types.ids import AccessTokenId, StationId
from tests.conftest import make_observation, make_station_config
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
)
from tests.integration.store.test_rejected_forecast_store import savepoint_factory

NOW = ensure_utc(datetime(2026, 11, 10, 10, 20, tzinfo=UTC))
OBSERVATION_STEPS = 168 * 6


# Seeding 10-minute data takes minutes at 160/200 stations, beyond the PR
# integration budget; those sizes run in the nightly slow job.
@pytest.mark.parametrize(
    "station_count",
    [
        21,
        pytest.param(160, marks=pytest.mark.slow),
        pytest.param(200, marks=pytest.mark.slow),
    ],
)
def test_native_overview_capacity(
    isolated_committed_db_engine: sa.Engine, station_count: int
) -> None:
    # Concurrent requests require commits; never seed the rollback test database.
    db_engine = isolated_committed_db_engine
    conn = db_engine.connect()
    setup = conn.begin()
    rng = random.Random(20261110 + station_count)
    models = [
        _seed_model(conn, f"overview-{index}-{uuid4().hex}") for index in range(4)
    ]
    station_ids = []
    tenant = None
    for index in range(station_count):
        station = make_station_config(
            station_id=StationId(UUID(int=station_count * 1000 + index + 1000)),
            code=f"OVERVIEW-{station_count}-{index}",
            network=f"overview_{station_count}",
            rng=rng,
        )
        tenant = station.tenant_id
        PgStationStore(conn).store_station(station)
        station_ids.append(station.id)
        # Real gauges report every 10 minutes; cover the full 168 h latest lookback.
        observations = [
            make_observation(
                station_id=station.id,
                parameter=parameter,
                timestamp=ensure_utc(NOW - timedelta(minutes=10 * step)),
                value=rng.uniform(0.2, 80),
                qc_status=[
                    QcStatus.RAW,
                    QcStatus.QC_UNCHECKED,
                    QcStatus.QC_SUSPECT,
                    QcStatus.QC_PASSED,
                ][step % 4],
                rng=rng,
            )
            for parameter in ("water_level", "discharge")
            for step in range(OBSERVATION_STEPS)
            if (step + index) % 11
        ]
        PgObservationStore(conn).store_observations(observations)
        for model in models:
            artifact = _seed_artifact(conn, station.id, model)
            conn.execute(
                sa.insert(model_assignments).values(
                    station_id=station.id,
                    model_id=model,
                    time_step=timedelta(hours=1),
                    status="active",
                )
            )
            levels = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
            # 44 times x seven levels x four outputs x200 =246400 value rows.
            medians = [rng.uniform(5, 90) for _ in range(44)]
            frame = pl.DataFrame(
                {
                    "valid_time": [
                        NOW + timedelta(hours=h) for h in range(44) for _ in levels
                    ],
                    "quantile": [q for _ in range(44) for q in levels],
                    "value": [v * (0.5 + q) for v in medians for q in levels],
                }
            )
            ensemble = ForecastEnsemble(
                representation=EnsembleRepresentation.QUANTILES,
                values=frame,
                station_id=station.id,
                issued_at=NOW,
                parameter="discharge",
                units="m³/s",
                forecast_horizon_steps=44,
                time_step=timedelta(hours=1),
                model_id=model,
            )
            record = replace(
                _make_forecast(station.id, model, artifact, issued_at=NOW),
                ensemble=ensemble,
                representation=EnsembleRepresentation.QUANTILES,
            )
            PgForecastStore(
                conn, transaction_factory=savepoint_factory(conn)
            ).store_forecast(record)
    setup.commit()
    conn.close()

    def request_connection() -> Iterator[sa.Connection]:
        with db_engine.begin() as connection:
            yield connection

    principal = Principal(
        token_id=AccessTokenId(UUID(int=1)),
        role=AccessTokenRole.REVIEWER,
        tenant_id=tenant,
        station_ids=frozenset(station_ids),
    )
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_connection] = request_connection
    app.dependency_overrides[require_principal] = lambda: principal
    app.dependency_overrides[get_observation_clock] = lambda: NOW
    queries = []
    query_times: list[float] = []
    started = [0.0]

    def observed(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        queries.append(statement)
        started[0] = perf_counter()

    def finished(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        query_times.append(perf_counter() - started[0])

    sa.event.listen(db_engine, "before_cursor_execute", observed)
    sa.event.listen(db_engine, "after_cursor_execute", finished)
    durations = []
    counts = []
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = None
        for _ in range(5):
            queries.clear()
            query_times.clear()
            start = perf_counter()
            response = client.get(
                f"/api/v1/networks/overview_{station_count}/overview",
                headers={"Accept-Encoding": "gzip"},
            )
            durations.append(perf_counter() - start)
            counts.append(sum(not sql.startswith("SET ") for sql in queries))
            if response.status_code != 200:
                structlog.get_logger().info(
                    "overview_benchmark_failed",
                    stations=station_count,
                    completed_query_seconds=query_times,
                    started_query_count=len(queries),
                    last_query_kind="observations"
                    if "overview_series" in queries[-1]
                    else "forecast_values"
                    if "forecast_values" in queries[-1]
                    else "other",
                )
            assert response.status_code == 200, response.text
            assert response.headers["content-encoding"] == "gzip"
        assert response is not None
        body = response.json()
        assert body["station_count"] == station_count
        assert all(
            len(row["forecasts"]) == 4
            and all(item["peak"] is not None for item in row["forecasts"])
            for row in body["items"]
        )
        assert max(counts) <= 20
        receipt = {
            "stations": station_count,
            "outputs_per_station": 4,
            "native_times": 44,
            "value_rows": station_count * 4 * 44 * 7,
            "query_counts": counts,
            "seconds": durations,
            "cold_seconds": durations[0],
            "warm_p95_seconds": sorted(durations[1:])[-1],
            "raw_bytes": len(response.content),
            "negotiated_gzip_bytes": int(response.headers["content-length"]),
            "role": "test database owner; principal scope enforced",
            "concurrency": 1,
            "last_query_seconds": query_times,
        }
        structlog.get_logger().info("overview_benchmark", **receipt)
        sa.event.remove(db_engine, "before_cursor_execute", observed)
        sa.event.remove(db_engine, "after_cursor_execute", finished)

        def concurrent_request(_: int) -> tuple[int, float]:
            started_at = perf_counter()
            with_client = TestClient(app, raise_server_exceptions=False)
            try:
                result = with_client.get(
                    f"/api/v1/networks/overview_{station_count}/overview",
                    headers={"Accept-Encoding": "gzip"},
                )
                return result.status_code, perf_counter() - started_at
            finally:
                with_client.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            concurrent = list(pool.map(concurrent_request, range(4)))
        structlog.get_logger().info(
            "overview_concurrent_benchmark",
            stations=station_count,
            refresh_period_seconds=60,
            concurrent_requests=4,
            results=concurrent,
        )
        assert all(status == 200 for status, _ in concurrent)

    finally:
        client.close()
        if sa.event.contains(db_engine, "before_cursor_execute", observed):
            sa.event.remove(db_engine, "before_cursor_execute", observed)
        if sa.event.contains(db_engine, "after_cursor_execute", finished):
            sa.event.remove(db_engine, "after_cursor_execute", finished)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
